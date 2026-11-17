"""Periodic live/offline detection via the official Helix ``streams`` endpoint."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from sqlalchemy import select

from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.twitch.api import HelixClient, StreamInfo, TwitchAuthError, TwitchError

if TYPE_CHECKING:
    from dogsapka_clipper.services.backend import Backend

log = logging.getLogger(__name__)


class StreamMonitor:
    def __init__(self, backend: "Backend", helix_factory) -> None:
        self.backend = backend
        self.helix_factory = helix_factory
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self.last_error = ""
        self.consecutive_failures = 0
        self.offline_grace: dict[int, int] = {}   # streamer_id -> consecutive offline polls

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            interval = max(15, self.backend.settings.twitch.poll_interval_s)
            try:
                await self.poll_once()
                self.consecutive_failures = 0
                self.last_error = ""
            except TwitchAuthError as e:
                self.consecutive_failures += 1
                self._report(e, auth=True)
                interval = 300
            except TwitchError as e:
                self.consecutive_failures += 1
                self._report(e)
                interval = min(600, 15 * 2 ** min(self.consecutive_failures, 5)) + random.uniform(0, 5)
            except Exception as e:  # never let the monitor die silently
                self.consecutive_failures += 1
                log.exception("Monitor poll crashed")
                self._report(e)
                interval = 60
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def _report(self, e: Exception, auth: bool = False) -> None:
        msg = str(e)
        hint = getattr(e, "hint", "")
        if msg != self.last_error or self.consecutive_failures in (1, 5, 20):
            self.backend.bus.emit("twitch.error" if not auth else "twitch.auth_error",
                                  f"Twitch: {msg}" + (f" - {hint}" if hint else ""), "error")
        self.last_error = msg

    async def poll_once(self) -> None:
        with self.backend.db.session() as s:
            rows = s.scalars(select(Streamer).where(Streamer.monitoring_enabled.is_(True),
                                                    Streamer.authorized.is_(True),
                                                    Streamer.platform == "twitch")).all()
            streamers = [(r.id, r.login, r.is_live) for r in rows]
        if not streamers:
            return
        helix: HelixClient = self.helix_factory()
        live = await helix.get_streams([l for _, l, _ in streamers])
        for sid, login, was_live in streamers:
            info = live.get(login.lower())
            if info:
                self.offline_grace.pop(sid, None)
                await self.backend.handle_live(sid, info, was_live)
            elif was_live or self.backend.has_session(sid):
                # require two consecutive offline polls to ride out brief API glitches/reconnects
                n = self.offline_grace.get(sid, 0) + 1
                self.offline_grace[sid] = n
                if n >= 2:
                    self.offline_grace.pop(sid, None)
                    await self.backend.handle_offline(sid)


class YouTubeMonitor:
    """Live/offline detection for YouTube streamers (official Data API, quota-friendly polling)."""

    def __init__(self, backend: "Backend", client_factory) -> None:
        self.backend = backend
        self.client_factory = client_factory
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self.last_error = ""
        self.consecutive_failures = 0
        self.offline_grace: dict[int, int] = {}

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        from dogsapka_clipper.youtube.live import YouTubeLiveError, YouTubeQuotaError
        while not self._stop.is_set():
            interval = max(30, self.backend.settings.youtube_live.poll_interval_s)
            try:
                await self.poll_once()
                self.consecutive_failures = 0
                self.last_error = ""
            except YouTubeQuotaError as e:
                self._report(e)
                interval = 1800
            except YouTubeLiveError as e:
                self.consecutive_failures += 1
                self._report(e)
                interval = min(1800, interval * 2 ** min(self.consecutive_failures, 4))
            except Exception as e:
                self.consecutive_failures += 1
                log.exception("YouTube monitor poll crashed")
                self._report(e)
                interval = 300
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def _report(self, e: Exception) -> None:
        msg = str(e)
        hint = getattr(e, "hint", "")
        if msg != self.last_error or self.consecutive_failures in (1, 5, 20):
            self.backend.bus.emit("youtube.error", f"YouTube: {msg}" + (f" - {hint}" if hint else ""), "error")
        self.last_error = msg

    async def poll_once(self) -> None:
        with self.backend.db.session() as s:
            rows = s.scalars(select(Streamer).where(Streamer.monitoring_enabled.is_(True),
                                                    Streamer.authorized.is_(True),
                                                    Streamer.platform == "youtube")).all()
            streamers = [(r.id, r.youtube_channel_id, r.youtube_uploads_playlist, r.is_live) for r in rows
                         if r.youtube_channel_id]
        if not streamers:
            return
        client = self.client_factory()
        live = await client.live_streams([(cid, up) for _, cid, up, _ in streamers])
        for sid, cid, _, was_live in streamers:
            info = live.get(cid)
            if info:
                self.offline_grace.pop(sid, None)
                await self.backend.handle_live_youtube(sid, info, was_live)
            elif was_live or self.backend.has_session(sid):
                n = self.offline_grace.get(sid, 0) + 1
                self.offline_grace[sid] = n
                if n >= 2:
                    self.offline_grace.pop(sid, None)
                    await self.backend.handle_offline(sid)
