import asyncio

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.services.monitor import StreamMonitor
from dogsapka_clipper.twitch.api import StreamInfo, TwitchAPIError


class FakeBackend:
    def __init__(self, db):
        self.db, self.bus, self.settings = db, EventBus(), AppSettings()
        self.live, self.offline, self.sessions = [], [], set()

    async def handle_live(self, sid, info, was_live):
        self.live.append(sid)
        self.sessions.add(sid)
        with self.db.session() as s:
            s.get(Streamer, sid).is_live = True

    async def handle_offline(self, sid):
        self.offline.append(sid)
        self.sessions.discard(sid)
        with self.db.session() as s:
            s.get(Streamer, sid).is_live = False

    def has_session(self, sid):
        return sid in self.sessions


class FakeHelix:
    def __init__(self):
        self.live = set()
        self.fail = False

    async def get_streams(self, logins):
        if self.fail:
            raise TwitchAPIError("boom")
        return {l: StreamInfo("1", l, l, "s", "t", "g", 1, "", "") for l in logins if l in self.live}


def test_live_then_offline_with_grace(db):
    with db.session() as s:
        s.add(Streamer(login="a", authorized=True))
        s.add(Streamer(login="unauth", authorized=False))
    b = FakeBackend(db)
    h = FakeHelix()
    m = StreamMonitor(b, lambda: h)
    h.live = {"a", "unauth"}
    asyncio.run(m.poll_once())
    assert b.live == [1]           # unauthorized channel is never monitored
    h.live = set()
    asyncio.run(m.poll_once())
    assert b.offline == []         # one offline poll is tolerated (API glitch / reconnect)
    asyncio.run(m.poll_once())
    assert b.offline == [1]


def test_errors_are_reported_not_silent(db):
    with db.session() as s:
        s.add(Streamer(login="a", authorized=True))
    b = FakeBackend(db)
    h = FakeHelix()
    h.fail = True
    events = []
    b.bus.subscribe(events.append)
    m = StreamMonitor(b, lambda: h)

    async def run_briefly():
        t = asyncio.create_task(m.run())
        await asyncio.sleep(0.2)
        m.stop()
        await t
    asyncio.run(run_briefly())
    assert any(e.kind == "twitch.error" and e.level == "error" for e in events)
    assert m.consecutive_failures >= 1
