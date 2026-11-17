"""Backend facade: owns the asyncio loop thread, database, monitor, sessions and job queue.

All public methods are thread-safe and may be called from the Qt UI thread.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
from sqlalchemy import select

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.core.paths import AppPaths
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.database import Database
from dogsapka_clipper.db.models import Clip, ExportedVideo, MonitoringSession, ProcessingJob, Streamer, utcnow
from dogsapka_clipper.processing.jobs import JobQueue
from dogsapka_clipper.processing.pipeline import ClipProcessor, seed_profiles
from dogsapka_clipper.recording.recorder import RecorderSource
from dogsapka_clipper.services.monitor import StreamMonitor, YouTubeMonitor
from dogsapka_clipper.services.session import LiveSession
from dogsapka_clipper.twitch.api import HelixClient, StreamInfo, TwitchAuth

log = logging.getLogger(__name__)


class Backend:
    def __init__(self, paths: AppPaths, db: Database | None = None, secrets: SecretStore | None = None,
                 bus: EventBus | None = None) -> None:
        self.paths = paths
        self.db = db or Database(paths.database_file)
        self.bus = bus or EventBus()
        self.secrets = secrets or SecretStore(paths.root / "database" / "secrets.bin")
        self.settings: AppSettings = self.db.load_settings()
        seed_profiles(self.db)
        self.jobs = JobQueue(self.db, self.bus, self.settings.output.workers)
        self.processor = ClipProcessor(self.db, paths, self.bus, lambda: self.settings)
        self.jobs.register("process", self.processor.handle_process)
        self.jobs.register("render", self.processor.handle_process)
        self.jobs.register("transcribe", self.processor.handle_transcribe)
        self.sessions: dict[int, LiveSession] = {}
        self._session_tasks: dict[int, asyncio.Task] = {}
        self._bg_tasks: list[asyncio.Task] = []
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="backend-loop", daemon=True)
        self._http: httpx.AsyncClient | None = None
        self.auth: TwitchAuth | None = None
        self.monitor: StreamMonitor | None = None
        self.yt_monitor: YouTubeMonitor | None = None
        from dogsapka_clipper.publishing.accounts import AccountManager
        self.accounts = AccountManager(self)
        self._started = False

    # -- lifecycle --------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self, monitor: bool = True) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        self.jobs.start()
        self.call(self._async_start(monitor)).result(timeout=30)
        self.bus.emit("app.started", "DOGSAPKA AI CLIPPER backend started", "info")

    async def _async_start(self, monitor: bool) -> None:
        self._http = httpx.AsyncClient(timeout=20)
        self.auth = TwitchAuth(self.settings.twitch.client_id, self.secrets, self._http)
        with self.db.session() as s:  # streamers are offline until confirmed live
            for st in s.scalars(select(Streamer)):
                st.is_live = False
            for ms in s.scalars(select(MonitoringSession).where(MonitoringSession.status == "active")):
                ms.status, ms.ended_at, ms.error = "error", utcnow(), "Application closed unexpectedly"
        # keep references: un-referenced tasks may be garbage collected, and they are cancelled on shutdown
        if monitor:
            self.monitor = StreamMonitor(self, lambda: HelixClient(self.auth))
            self._bg_tasks.append(asyncio.create_task(self.monitor.run()))
            self.yt_monitor = YouTubeMonitor(self, self.youtube_live_client)
            self._bg_tasks.append(asyncio.create_task(self.yt_monitor.run()))
        self._bg_tasks.append(asyncio.create_task(self._housekeeping()))

    def shutdown(self) -> None:
        if not self._started:
            return
        log.info("Shutting down backend")
        try:
            self.call(self._async_shutdown()).result(timeout=120)
        except Exception:
            log.exception("Error during shutdown")
        self.jobs.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(10)
        self.db.close()
        self._started = False

    async def _async_shutdown(self) -> None:
        if self.monitor:
            self.monitor.stop()
        if self.yt_monitor:
            self.yt_monitor.stop()
        for sess in list(self.sessions.values()):
            sess.stop()
        tasks = list(self._session_tasks.values())
        if tasks:
            await asyncio.wait(tasks, timeout=100)
        for t in self._bg_tasks:
            t.cancel()
        if self._bg_tasks:
            await asyncio.wait(self._bg_tasks, timeout=10)
        self._bg_tasks.clear()
        if self._http:
            await self._http.aclose()

    def call(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # -- settings ------------------------------------------------------------
    def update_settings(self, new: AppSettings) -> None:
        new.validate()
        old_client = self.settings.twitch.client_id
        self.settings = new
        self.db.save_settings(new)
        self.jobs.set_workers(new.output.workers)
        if self.auth and new.twitch.client_id != old_client:
            self.auth.client_id = new.twitch.client_id
            self.auth.logout()
        self.bus.emit("settings.changed", "Settings saved", "info")
        self._poke_monitors()

    def _poke_monitors(self) -> None:
        for m in (self.monitor, self.yt_monitor):
            if m:
                self.loop.call_soon_threadsafe(m.poke)

    def set_client_secret(self, secret: str | None) -> None:
        self.secrets.set(TwitchAuth.SECRET_KEY, secret or None)

    # -- streamers ------------------------------------------------------------
    def add_streamer(self, login: str, authorized: bool) -> int:
        login = login.strip().lower().lstrip("@").replace("https://www.twitch.tv/", "").strip("/")
        if not login or not all(c.isalnum() or c == "_" for c in login) or len(login) > 25:
            raise ValueError("Invalid Twitch username.")
        if not authorized:
            raise ValueError("You must confirm that you are authorized to record and redistribute this channel.")
        with self.db.session() as s:
            if s.scalars(select(Streamer).where(Streamer.login == login)).first():
                raise ValueError(f"{login} is already in your list.")
            st = Streamer(login=login, display_name=login, authorized=True, monitoring_enabled=True)
            s.add(st)
            s.flush()
            sid = st.id
        self.bus.emit("streamer.added", f"Streamer added: {login}", "info", streamer_id=sid)
        self.call(self._refresh_profile(sid, login))
        self._poke_monitors()
        return sid

    # -- YouTube live streamers -------------------------------------------------------
    def youtube_api_key(self) -> str:
        from dogsapka_clipper.youtube.live import SECRET_KEY
        return self.secrets.get(SECRET_KEY) or ""

    def set_youtube_api_key(self, key: str | None) -> None:
        from dogsapka_clipper.youtube.live import SECRET_KEY
        self.secrets.set(SECRET_KEY, (key or "").strip() or None)
        self._poke_monitors()

    def youtube_live_client(self):
        from dogsapka_clipper.youtube.live import YouTubeLiveClient
        return YouTubeLiveClient(self.youtube_api_key(), self._http, bearer_provider=self.accounts.youtube_read_token)

    def add_youtube_streamer(self, channel: str, authorized: bool) -> int:
        """Add a YouTube channel (handle, URL or channel id). Blocking (one API call) - call off the UI thread."""
        from dogsapka_clipper.youtube.live import parse_channel_input
        if not authorized:
            raise ValueError("You must confirm that you are authorized to record and redistribute this channel.")
        parse_channel_input(channel)            # validates the format before using any quota
        if not self.youtube_api_key():
            raise ValueError("Enter your YouTube Data API key in Settings → YouTube live first.")
        info = self.call(self.youtube_live_client().resolve_channel(channel)).result(timeout=30)
        handle = (info.handle or info.channel_id).lstrip("@").lower()
        login = ("yt-" + "".join(c if c.isalnum() or c in "_." else "_" for c in handle))[:64]
        with self.db.session() as s:
            if s.scalars(select(Streamer).where((Streamer.youtube_channel_id == info.channel_id) |
                                                (Streamer.login == login))).first():
                raise ValueError(f"{info.title} is already in your list.")
            st = Streamer(login=login, platform="youtube", display_name=info.title, authorized=True,
                          monitoring_enabled=True, youtube_channel_id=info.channel_id,
                          youtube_uploads_playlist=info.uploads_playlist, youtube_handle=info.handle,
                          profile_image_url=info.thumbnail_url)
            s.add(st)
            s.flush()
            sid = st.id
        self.bus.emit("streamer.added", f"YouTube streamer added: {info.title}", "info", streamer_id=sid)
        if info.thumbnail_url:
            self.call(self._download_avatar(sid, info.thumbnail_url, login))
        self._poke_monitors()
        return sid

    async def _download_avatar(self, sid: int, url: str, name: str) -> None:
        try:
            r = await self._http.get(url)
        except httpx.HTTPError:
            return
        if r.status_code == 200:
            p = self.paths.cache / f"avatar_{name}.png"
            p.write_bytes(r.content)
            with self.db.session() as s:
                st = s.get(Streamer, sid)
                if st:
                    st.profile_image_path = str(p)
            self.bus.emit("streamer.updated", "", streamer_id=sid)

    async def handle_live_youtube(self, sid: int, info, was_live: bool) -> None:
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            prev_video = st.live_video_id
            st.is_live, st.stream_title, st.viewer_count = True, info.title, info.viewer_count
            st.category = st.category or "YouTube"
            st.live_video_id = info.video_id
            st.last_live_at = utcnow()
            login = st.login
            name = st.display_name or info.channel_title
        if not was_live:
            self.bus.emit("streamer.live", f"{name} went live on YouTube: {info.title}", "success", streamer_id=sid)
        else:
            self.bus.emit("streamer.updated", "", streamer_id=sid)
        sess = self.sessions.get(sid)
        if sess and prev_video and prev_video != info.video_id:
            sess.stop()      # the channel started a new broadcast: the next poll starts a fresh session
            return
        if sid not in self.sessions:
            err = self._preflight()
            if err:
                self.bus.emit("recording.blocked", err, "error", streamer_id=sid)
                return
            src = RecorderSource("youtube", info.url, self.settings.recording.quality)
            self._start_session(LiveSession(self, sid, login, src, "youtube", title=info.title, category="YouTube",
                                            stream_id=info.video_id, live_chat_id=info.live_chat_id))

    async def _refresh_profile(self, sid: int, login: str) -> None:
        try:
            users = await HelixClient(self.auth).get_users([login])
        except Exception as e:
            log.info("Profile lookup for %s failed: %s", login, e)
            return
        if not users:
            self.bus.emit("streamer.not_found", f"Twitch user '{login}' was not found", "warning", streamer_id=sid)
            return
        u = users[0]
        img_path = ""
        if u.profile_image_url:
            try:
                r = await self._http.get(u.profile_image_url)
                if r.status_code == 200:
                    p = self.paths.cache / f"avatar_{u.login}.png"
                    p.write_bytes(r.content)
                    img_path = str(p)
            except httpx.HTTPError:
                pass
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            if st:
                st.display_name, st.twitch_user_id = u.display_name, u.id
                st.profile_image_url = u.profile_image_url
                st.profile_image_path = img_path or st.profile_image_path
        self.bus.emit("streamer.updated", "", streamer_id=sid)

    def remove_streamer(self, sid: int) -> None:
        self.stop_session(sid, wait=True)
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            if st:
                # exports are never deleted implicitly: detach them from the clips first
                for c in st.clips:
                    for e in c.exports:
                        e.clip_id = None
                s.delete(st)
        self.bus.emit("streamer.removed", "Streamer removed", "info", streamer_id=sid)

    def set_monitoring(self, sid: int, enabled: bool) -> None:
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            if st:
                st.monitoring_enabled = enabled
        if not enabled:
            self.stop_session(sid)
        else:
            self._poke_monitors()
        self.bus.emit("streamer.updated", "", streamer_id=sid)

    def set_monitoring_all(self, enabled: bool) -> int:
        """Start or stop monitoring every real streamer (the simulation entry is left alone).
        Stopping also ends running recordings safely. Returns the number of streamers changed."""
        with self.db.session() as s:
            rows = s.scalars(select(Streamer).where(Streamer.login != "dogsapka_simulation",
                                                    Streamer.monitoring_enabled.is_(not enabled))).all()
            ids = [r.id for r in rows]
            for r in rows:
                r.monitoring_enabled = enabled
        if not enabled:
            for sid in ids:
                self.stop_session(sid)
        else:
            self._poke_monitors()
        self.bus.emit("streamer.updated", "", streamer_id=None)
        return len(ids)

    def monitoring_state(self) -> tuple[int, int]:
        """(monitored, total) real streamers."""
        with self.db.session() as s:
            rows = s.scalars(select(Streamer).where(Streamer.login != "dogsapka_simulation")).all()
            return sum(1 for r in rows if r.monitoring_enabled), len(rows)

    # -- sessions -------------------------------------------------------------------
    def has_session(self, sid: int) -> bool:
        return sid in self.sessions

    def _preflight(self) -> str | None:
        free = shutil.disk_usage(self.paths.root).free / 1e9
        if free < 2.0:
            return f"Only {free:.1f} GB free disk space - recording paused. Free up space or change the folder."
        return None

    async def handle_live(self, sid: int, info: StreamInfo, was_live: bool) -> None:
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            st.is_live, st.stream_title, st.category, st.viewer_count = True, info.title, info.game_name, info.viewer_count
            st.last_live_at = utcnow()
            st.display_name = info.user_name or st.display_name
            login = st.login
        if not was_live:
            self.bus.emit("streamer.live", f"{info.user_name} went live: {info.title}", "success", streamer_id=sid)
        else:
            self.bus.emit("streamer.updated", "", streamer_id=sid)
        if sid not in self.sessions:
            err = self._preflight()
            if err:
                self.bus.emit("recording.blocked", err, "error", streamer_id=sid)
                return
            src = RecorderSource("twitch", login, self.settings.recording.quality)
            self._start_session(LiveSession(self, sid, login, src, "twitch", title=info.title,
                                            category=info.game_name, stream_id=info.stream_id))

    async def handle_offline(self, sid: int) -> None:
        with self.db.session() as s:
            st = s.get(Streamer, sid)
            if st and st.is_live:
                st.is_live = False
                self.bus.emit("streamer.offline", f"{st.display_name} went offline", "info", streamer_id=sid)
        sess = self.sessions.get(sid)
        if sess:
            sess.stop()

    def _start_session(self, sess: LiveSession) -> None:
        sid = sess.streamer_id
        self.sessions[sid] = sess

        async def runner():
            try:
                await sess.run()
            except Exception as e:
                log.exception("Session crashed")
                self.bus.emit("session.error", f"Monitoring session for {sess.login} failed: {e}", "error",
                              streamer_id=sid)
                with self.db.session() as s:
                    ms = s.get(MonitoringSession, sess.session_id)
                    if ms:
                        ms.status, ms.error, ms.ended_at = "error", str(e)[:1000], utcnow()
            finally:
                self.sessions.pop(sid, None)
                self._session_tasks.pop(sid, None)
                if sess.source.kind == "file":
                    with self.db.session() as s:
                        st = s.get(Streamer, sid)
                        if st:
                            st.is_live = False
                self.bus.emit("streamer.updated", "", streamer_id=sid)
        self._session_tasks[sid] = self.loop.create_task(runner())
        self.bus.emit("streamer.updated", "", streamer_id=sid)

    def stop_session(self, sid: int, wait: bool = False, timeout: float = 120) -> None:
        sess = self.sessions.get(sid)
        if not sess:
            return
        sess.stop()
        if wait:
            deadline = time.time() + timeout
            while sid in self.sessions and time.time() < deadline:
                time.sleep(0.2)

    def start_simulation(self, video: Path | None = None, duration: float = 150,
                         highlights: list[float] | None = None) -> int:
        """Start a fully local simulated live session (sample video + simulated chat)."""
        from dogsapka_clipper.simulation.simulator import generate_sample_stream
        import json
        if video is None:
            video = self.paths.cache / "simulation" / f"sample_{int(duration)}.mp4"
            meta = video.with_suffix(".json")
            if not video.exists() or not meta.exists() or (highlights is not None and
                                                           json.loads(meta.read_text())["highlights"] != highlights):
                self.bus.emit("simulation.preparing", "Generating simulation stream…", "info")
                generate_sample_stream(video, duration, highlights)
            highlights = json.loads(video.with_suffix(".json").read_text())["highlights"]
        with self.db.session() as s:
            st = s.scalars(select(Streamer).where(Streamer.login == "dogsapka_simulation")).first()
            if st is None:
                st = Streamer(login="dogsapka_simulation", display_name="DOGSAPKA Simulation", authorized=True,
                              monitoring_enabled=False, category="Simulation")
                s.add(st)
                s.flush()
            st.is_live, st.stream_title, st.category = True, "Simulated livestream", "Simulation"
            sid = st.id
        if sid in self.sessions:
            raise RuntimeError("A simulation is already running.")
        src = RecorderSource("file", str(video))
        sess = LiveSession(self, sid, "dogsapka_simulation", src, "simulated", sim_highlights=highlights or [],
                           title="Simulated livestream", category="Simulation")
        self.loop.call_soon_threadsafe(self._start_session, sess)
        self.bus.emit("streamer.live", "Simulation streamer went live", "success", streamer_id=sid)
        return sid

    # -- Twitch auth ----------------------------------------------------------------
    def twitch_device_login(self, on_code: Callable[[Any], None]) -> concurrent.futures.Future:
        async def flow():
            dc = await self.auth.start_device_flow()
            on_code(dc)
            await self.auth.poll_device_flow(dc)
            self.bus.emit("twitch.connected", "Twitch account connected", "success")
            if self.monitor:
                self.monitor.poke()
        return self.call(flow())

    def twitch_status(self) -> str:
        if not self.settings.twitch.client_id:
            return "not_configured"
        if self.auth and self.auth.has_user_token():
            return "connected"
        if self.secrets.get(TwitchAuth.SECRET_KEY):
            return "app_token"
        return "not_connected"

    # -- clips ---------------------------------------------------------------------
    def delete_clip(self, clip_id: int, delete_exports: bool = False) -> None:
        with self.db.session() as s:
            c = s.get(Clip, clip_id)
            if not c:
                return
            files = [c.source_path, c.thumbnail_path]
            for e in list(c.exports):
                if delete_exports:
                    files += [e.path, e.srt_path, e.ass_path]
                    s.delete(e)
                else:
                    e.clip_id = None
            s.delete(c)
        for f in files:
            if f:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
        self.bus.emit("clip.deleted", "Clip deleted", "info", clip_id=clip_id)

    def render_clip(self, clip_id: int, edit: dict, profile: str | None = None,
                    profile_overrides: dict | None = None, retranscribe: bool = False) -> int:
        return self.jobs.submit("render", clip_id, {"edit": edit, "profile": profile,
                                                    "profile_overrides": profile_overrides or {},
                                                    "retranscribe": retranscribe})

    # -- housekeeping ----------------------------------------------------------------
    async def _housekeeping(self) -> None:
        while True:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.cleanup)
            except Exception:
                log.exception("Housekeeping failed")
            await asyncio.sleep(1800)

    def cleanup(self) -> dict:
        """Delete stale recordings/temp files. Clips and exports are never removed here."""
        removed = 0
        cutoff = time.time() - self.settings.recording.cleanup_recordings_days * 86400
        active = {str(s.rec_dir) for s in self.sessions.values()}
        for d in (self.paths.recordings, self.paths.temp):
            for p in d.rglob("*"):
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff and not any(str(p).startswith(a) for a in active):
                        p.unlink()
                        removed += 1
                except OSError:
                    pass
        # storage cap: warn (never auto-delete clips/exports)
        used = sum(f.stat().st_size for f in self.paths.root.rglob("*") if f.is_file()) / 1e9
        if used > self.settings.recording.max_storage_gb:
            self.bus.emit("storage.limit", f"Storage usage {used:.1f} GB exceeds the {self.settings.recording.max_storage_gb:.0f} GB "
                                           f"limit. Delete old clips in the library.", "warning")
        return {"removed": removed, "used_gb": used}

    # -- stats for dashboard ---------------------------------------------------------------
    def dashboard_stats(self) -> dict:
        from sqlalchemy import func
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        with self.db.session() as s:
            total_st = s.scalar(select(func.count()).select_from(Streamer).where(Streamer.login != "dogsapka_simulation")) or 0
            live = s.scalar(select(func.count()).select_from(Streamer).where(Streamer.is_live.is_(True))) or 0
            clips = s.scalar(select(func.count()).select_from(Clip)) or 0
            clips_today = s.scalar(select(func.count()).select_from(Clip).where(Clip.created_at >= today)) or 0
            queue = s.scalar(select(func.count()).select_from(ProcessingJob)
                             .where(ProcessingJob.status.in_(("queued", "running", "paused")))) or 0
            exports = s.scalar(select(func.count()).select_from(ExportedVideo)) or 0
        du = shutil.disk_usage(self.paths.root)
        return {"monitored": total_st, "live": live, "recording": sum(1 for x in self.sessions.values() if x.recorder.running),
                "clips": clips, "clips_today": clips_today, "queue": queue, "exports": exports,
                "processing": self.jobs.active_count(), "disk_free_gb": du.free / 1e9, "disk_total_gb": du.total / 1e9}
