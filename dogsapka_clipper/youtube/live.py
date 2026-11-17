"""YouTube live detection and live chat via the official YouTube Data API v3.

Live detection is quota-friendly: a running live broadcast appears in the channel's *uploads* playlist,
so one ``playlistItems.list`` call per channel (1 unit) plus one ``videos.list`` call per 50 videos (1 unit)
tells whether the channel is live - instead of ``search.list`` (100 units per call).

Chat: ``liveChatMessages.list`` (~5 units per call) polled every ``chat_poll_s`` seconds. Messages arrive in
batches with their real send time; the session evaluates chat with a matching delay. Usernames are reduced
to a salted hash (same as Twitch chat) and message text is never stored.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

import httpx

from dogsapka_clipper.twitch.chat import ChatMessage, anonymize

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
SECRET_KEY = "youtube_api_key"

_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_HANDLE_RE = re.compile(r"^@?[0-9A-Za-z._-]{3,30}$")


class YouTubeLiveError(RuntimeError):
    def __init__(self, msg: str, hint: str = "") -> None:
        super().__init__(msg)
        self.hint = hint


class YouTubeQuotaError(YouTubeLiveError):
    pass


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    handle: str
    thumbnail_url: str
    uploads_playlist: str


@dataclass
class LiveInfo:
    channel_id: str
    video_id: str
    title: str
    channel_title: str
    viewer_count: int
    started_at: str
    live_chat_id: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_channel_input(text: str) -> tuple[str, str]:
    """Return ("id", "UC…") or ("handle", "@name") from a channel id, handle or channel URL."""
    t = text.strip()
    t = re.sub(r"^https?://", "", t)
    t = re.sub(r"^(www\.|m\.)?youtube\.com/", "", t)
    t = t.split("?")[0].strip("/")
    for prefix in ("channel/",):
        if t.startswith(prefix):
            t = t[len(prefix):].split("/")[0]
    t = t.split("/")[0]
    if _ID_RE.match(t):
        return "id", t
    if _HANDLE_RE.match(t):
        return "handle", t if t.startswith("@") else "@" + t
    raise ValueError("Enter a YouTube channel handle (@name), channel URL or channel ID (UC…).")


def uploads_playlist_for(channel_id: str) -> str:
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else ""


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


class YouTubeLiveClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient,
                 bearer_provider: Callable[[], str | None] | None = None) -> None:
        self.api_key = api_key
        self.http = http
        self.bearer_provider = bearer_provider

    async def _get(self, path: str, params: dict, allow_oauth_retry: bool = False) -> dict:
        if not self.api_key:
            raise YouTubeLiveError("YouTube API key is not set.",
                                   "Create a key for 'YouTube Data API v3' in Google Cloud and enter it in "
                                   "Settings → YouTube live.")
        try:
            r = await self.http.get(f"{API}/{path}", params={**params, "key": self.api_key})
        except httpx.HTTPError as e:
            raise YouTubeLiveError(f"YouTube API not reachable: {e}") from e
        if r.status_code in (401, 403) and allow_oauth_retry and self.bearer_provider:
            token = self.bearer_provider()
            if token:
                r = await self.http.get(f"{API}/{path}", params=params,
                                        headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return r.json()
        reason, message = "", r.text[:200]
        try:
            err = r.json().get("error", {})
            message = err.get("message", message)
            reason = (err.get("errors") or [{}])[0].get("reason", "")
        except ValueError:
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise YouTubeQuotaError("YouTube API quota exhausted.",
                                    "Live checks resume when the daily quota resets (midnight Pacific time). "
                                    "Increase the check interval or monitor fewer YouTube channels.")
        if reason in ("keyInvalid", "badRequest") and "key" in message.lower():
            raise YouTubeLiveError("The YouTube API key is invalid.", "Check the key in Settings → YouTube live.")
        raise YouTubeLiveError(f"YouTube API error {r.status_code}: {message}")

    async def resolve_channel(self, text: str) -> ChannelInfo:
        kind, value = parse_channel_input(text)
        params = {"part": "snippet,contentDetails"}
        params["id" if kind == "id" else "forHandle"] = value
        data = await self._get("channels", params)
        items = data.get("items") or []
        if not items:
            raise YouTubeLiveError(f"YouTube channel '{value}' was not found.")
        it = items[0]
        sn = it.get("snippet", {})
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        uploads = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") \
            or uploads_playlist_for(it["id"])
        return ChannelInfo(it["id"], sn.get("title", value), sn.get("customUrl", value if kind == "handle" else ""),
                           thumb, uploads)

    async def live_streams(self, channels: list[tuple[str, str]]) -> dict[str, LiveInfo]:
        """``channels``: (channel_id, uploads_playlist). Returns channel_id -> LiveInfo for live channels."""
        video_ids: list[str] = []
        for cid, uploads in channels:
            data = await self._get("playlistItems", {"part": "contentDetails", "maxResults": 5,
                                                     "playlistId": uploads or uploads_playlist_for(cid)})
            video_ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])
                          if i.get("contentDetails", {}).get("videoId")]
        wanted = {cid for cid, _ in channels}
        live: dict[str, LiveInfo] = {}
        for k in range(0, len(video_ids), 50):
            data = await self._get("videos", {"part": "snippet,liveStreamingDetails",
                                              "id": ",".join(video_ids[k:k + 50])})
            for v in data.get("items", []):
                sn, ls = v.get("snippet", {}), v.get("liveStreamingDetails", {})
                cid = sn.get("channelId", "")
                if (cid in wanted and sn.get("liveBroadcastContent") == "live" and ls.get("actualStartTime")
                        and not ls.get("actualEndTime") and cid not in live):
                    live[cid] = LiveInfo(cid, v["id"], sn.get("title", ""), sn.get("channelTitle", ""),
                                         int(ls.get("concurrentViewers") or 0), ls.get("actualStartTime", ""),
                                         ls.get("activeLiveChatId", ""))
        return live

    async def chat_page(self, live_chat_id: str, page_token: str | None) -> tuple[list[ChatMessage], str | None, int]:
        params = {"liveChatId": live_chat_id, "part": "snippet,authorDetails", "maxResults": 2000}
        if page_token:
            params["pageToken"] = page_token
        data = await self._get("liveChat/messages", params, allow_oauth_retry=True)
        msgs = []
        for it in data.get("items", []):
            sn = it.get("snippet", {})
            text = sn.get("displayMessage") or sn.get("textMessageDetails", {}).get("messageText", "")
            if sn.get("type") not in (None, "textMessageEvent", "superChatEvent") or not text:
                continue
            author = it.get("authorDetails", {}).get("channelId") or sn.get("authorChannelId", "")
            msgs.append(ChatMessage(_ts(sn.get("publishedAt", "")), anonymize(author), text))
        return msgs, data.get("nextPageToken"), int(data.get("pollingIntervalMillis") or 0)


class YouTubeChatReader:
    """Polls a broadcast's live chat and forwards messages (same callback shape as TwitchChatReader)."""

    def __init__(self, client: YouTubeLiveClient, live_chat_id: str,
                 on_message: Callable[[ChatMessage], Awaitable[None]], poll_s: float = 20.0,
                 on_status: Callable[[str, str], None] | None = None) -> None:
        self.client = client
        self.live_chat_id = live_chat_id
        self.on_message = on_message
        self.poll_s = poll_s
        self.on_status = on_status or (lambda lvl, msg: None)
        self.connected = False
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if not self.live_chat_id:
            self.on_status("warning", "This YouTube stream has no public live chat - using audio/video detection only")
            return
        token: str | None = None
        first = True
        failures = 0
        while not self._stop.is_set():
            try:
                msgs, token, server_ms = await self.client.chat_page(self.live_chat_id, token)
                failures = 0
                if not self.connected:
                    self.connected = True
                    self.on_status("success", "YouTube live chat connected")
                if not first:   # the first page is chat history from before monitoring started
                    for m in msgs:
                        await self.on_message(m)
                first = False
                wait = max(self.poll_s, server_ms / 1000.0)
            except YouTubeQuotaError as e:
                self.connected = False
                self.on_status("error", f"YouTube chat paused: {e} {e.hint}")
                wait = 900
            except YouTubeLiveError as e:
                self.connected = False
                failures += 1
                if failures in (1, 5):
                    self.on_status("warning", f"YouTube chat unavailable: {e}")
                if "403" in str(e) or failures > 20:
                    return   # chat disabled / members-only: keep using audio + video
                wait = min(300, 15 * failures)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        self.connected = False
