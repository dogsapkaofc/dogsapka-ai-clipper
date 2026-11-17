"""YouTube live streamers: channel parsing, live detection, chat, monitor routing, recorder command."""
import asyncio
from pathlib import Path

import httpx
import pytest

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.detection.chat import ChatActivityDetector, ChatReactionDetector
from dogsapka_clipper.recording.recorder import RecorderSource, StreamRecorder
from dogsapka_clipper.services.monitor import StreamMonitor, YouTubeMonitor
from dogsapka_clipper.youtube.live import (LiveInfo, YouTubeChatReader, YouTubeLiveClient, YouTubeLiveError,
                                           YouTubeQuotaError, parse_channel_input)

CID = "UC" + "a" * 22
CID2 = "UC" + "b" * 22


@pytest.mark.parametrize("text,expected", [
    ("@MyChannel", ("handle", "@MyChannel")),
    ("mychannel", ("handle", "@mychannel")),
    ("https://www.youtube.com/@MyChannel", ("handle", "@MyChannel")),
    ("youtube.com/@MyChannel/streams", ("handle", "@MyChannel")),
    (f"https://www.youtube.com/channel/{CID}", ("id", CID)),
    (CID, ("id", CID)),
])
def test_parse_channel_input(text, expected):
    assert parse_channel_input(text) == expected


def test_parse_channel_input_rejects_garbage():
    with pytest.raises(ValueError):
        parse_channel_input("a b c !!")


def client(handler, key="KEY", bearer=None):
    return YouTubeLiveClient(key, httpx.AsyncClient(transport=httpx.MockTransport(handler)), bearer_provider=bearer)


def test_resolve_channel_by_handle():
    def handler(req):
        assert req.url.path == "/youtube/v3/channels"
        assert req.url.params["forHandle"] == "@mychannel" and req.url.params["key"] == "KEY"
        return httpx.Response(200, json={"items": [{"id": CID, "snippet": {
            "title": "My Channel", "customUrl": "@mychannel", "thumbnails": {"default": {"url": "http://x/a.png"}}},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "a" * 22}}}]})
    info = asyncio.run(client(handler).resolve_channel("https://youtube.com/@mychannel"))
    assert (info.channel_id, info.title, info.uploads_playlist) == (CID, "My Channel", "UU" + "a" * 22)


def test_resolve_unknown_channel():
    with pytest.raises(YouTubeLiveError):
        asyncio.run(client(lambda r: httpx.Response(200, json={"items": []})).resolve_channel("@nobody"))


def test_live_detection_uses_cheap_endpoints():
    """playlistItems + videos (1 unit each) - never search.list (100 units)."""
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith("/playlistItems"):
            pid = req.url.params["playlistId"]
            vids = ["live1", "old1"] if pid == "UU" + "a" * 22 else ["vod2"]
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v}} for v in vids]})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [
                {"id": "live1", "snippet": {"channelId": CID, "title": "LIVE NOW", "channelTitle": "A",
                                            "liveBroadcastContent": "live"},
                 "liveStreamingDetails": {"actualStartTime": "2026-11-17T10:00:00Z", "concurrentViewers": "321",
                                          "activeLiveChatId": "CHAT1"}},
                {"id": "old1", "snippet": {"channelId": CID, "liveBroadcastContent": "none"},
                 "liveStreamingDetails": {"actualStartTime": "x", "actualEndTime": "y"}},
                {"id": "vod2", "snippet": {"channelId": CID2, "liveBroadcastContent": "none"}}]})
        return httpx.Response(404)
    live = asyncio.run(client(handler).live_streams([(CID, ""), (CID2, "")]))
    assert set(live) == {CID}
    li = live[CID]
    assert (li.video_id, li.viewer_count, li.live_chat_id) == ("live1", 321, "CHAT1")
    assert li.url == "https://www.youtube.com/watch?v=live1"
    assert not any(p.endswith("/search") for p in seen)
    assert seen.count("/youtube/v3/videos") == 1      # all videos in one batched call


def test_quota_error_is_specific():
    def handler(req):
        return httpx.Response(403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    with pytest.raises(YouTubeQuotaError) as ei:
        asyncio.run(client(handler).live_streams([(CID, "")]))
    assert ei.value.hint


def test_missing_api_key():
    with pytest.raises(YouTubeLiveError):
        asyncio.run(client(lambda r: httpx.Response(200), key="").live_streams([(CID, "")]))


def test_chat_reader_skips_history_and_uses_oauth_fallback():
    pages = {"n": 0}

    def handler(req):
        if "Authorization" not in req.headers:
            return httpx.Response(403, json={"error": {"message": "login required", "errors": [{"reason": "forbidden"}]}})
        assert req.headers["Authorization"] == "Bearer TOK"
        pages["n"] += 1
        items = [{"snippet": {"type": "textMessageEvent", "displayMessage": f"KEKW {pages['n']}",
                              "publishedAt": "2026-11-17T10:00:05Z"},
                  "authorDetails": {"channelId": "UCuser"}}]
        return httpx.Response(200, json={"items": items, "nextPageToken": f"p{pages['n']}", "pollingIntervalMillis": 0})

    got = []
    yc = client(handler, bearer=lambda: "TOK")

    async def run():
        reader = YouTubeChatReader(yc, "CHAT1", lambda m: _collect(got, m), poll_s=0.01)
        task = asyncio.create_task(reader.run())
        while pages["n"] < 3:
            await asyncio.sleep(0.01)
        reader.stop()
        await task
        return reader
    asyncio.run(run())
    assert got and all(m.text != "KEKW 1" for m in got)       # first page = history before monitoring
    assert got[0].ts == pytest.approx(1794909605.0)            # real send time is kept
    assert got[0].user_hash != "UCuser"                        # usernames are anonymized


async def _collect(out, m):
    out.append(m)


def test_chat_detectors_ignore_newer_messages_when_evaluated_in_the_past():
    """YouTube chat is evaluated with a delay; messages after the evaluation time must not count."""
    act, react = ChatActivityDetector(), ChatReactionDetector()
    for i in range(50):                    # a burst 20 s *after* the evaluation time
        act.add(1000.0 + 20 + i * 0.05, f"u{i}")
        react.add(1000.0 + 20 + i * 0.05, "KEKW")
    assert act.evaluate(1000.0).detail["rate"] == 0
    assert react.evaluate(1000.0).detail["rate"] == 0
    assert act.evaluate(1023.0).detail["rate"] > 5


class FakeBackend:
    def __init__(self, db):
        self.db, self.bus, self.settings = db, EventBus(), AppSettings()
        self.yt_live, self.tw_live, self.offline, self.sessions = [], [], [], set()

    async def handle_live_youtube(self, sid, info, was_live):
        self.yt_live.append((sid, info.video_id))
        self.sessions.add(sid)
        with self.db.session() as s:
            s.get(Streamer, sid).is_live = True

    async def handle_live(self, sid, info, was_live):
        self.tw_live.append(sid)

    async def handle_offline(self, sid):
        self.offline.append(sid)
        self.sessions.discard(sid)
        with self.db.session() as s:
            s.get(Streamer, sid).is_live = False

    def has_session(self, sid):
        return sid in self.sessions


class FakeYT:
    def __init__(self):
        self.live = {}
        self.asked = []

    async def live_streams(self, channels):
        self.asked.append([c for c, _ in channels])
        return {c: self.live[c] for c, _ in channels if c in self.live}


class FakeHelix:
    def __init__(self):
        self.asked = []

    async def get_streams(self, logins):
        self.asked.append(list(logins))
        return {}


def test_monitors_only_check_their_own_platform(db):
    with db.session() as s:
        s.add(Streamer(login="twitchguy", authorized=True))
        yt = Streamer(login="yt-ytgal", platform="youtube", youtube_channel_id=CID, authorized=True)
        s.add(yt)
        s.flush()
        yt_id = yt.id
    b = FakeBackend(db)
    fy, fh = FakeYT(), FakeHelix()
    ym, tm = YouTubeMonitor(b, lambda: fy), StreamMonitor(b, lambda: fh)
    asyncio.run(tm.poll_once())
    assert fh.asked == [["twitchguy"]]                         # YouTube channel never sent to Twitch
    fy.live[CID] = LiveInfo(CID, "vid1", "t", "c", 1, "", "")
    asyncio.run(ym.poll_once())
    assert fy.asked == [[CID]] and b.yt_live == [(yt_id, "vid1")]
    fy.live.clear()
    asyncio.run(ym.poll_once())
    assert b.offline == []                                     # one missed poll is tolerated
    asyncio.run(ym.poll_once())
    assert b.offline == [yt_id]


def test_youtube_recorder_command(tmp_path):
    rec = StreamRecorder(RecorderSource("youtube", "https://www.youtube.com/watch?v=abc", "720p"), tmp_path, 4,
                         lambda seg: None)
    sl, ff = rec._build(Path(tmp_path))
    assert "https://www.youtube.com/watch?v=abc" in sl and "720p,best" in sl
    assert not any("twitch" in a for a in sl)
    assert "pipe:0" in ff
