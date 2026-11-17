"""Multiple publish accounts, automatic upload rules and the schema migration."""
import sqlite3

import pytest

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.database import SCHEMA_VERSION, Database
from dogsapka_clipper.db.models import Clip, ExportedVideo, PublishAccount, Streamer
from dogsapka_clipper.publishing import accounts as acc_mod
from dogsapka_clipper.publishing.accounts import AccountManager, published_key


class FakeJobs:
    def __init__(self):
        self.handlers, self.submitted = {}, []

    def register(self, kind, fn):
        self.handlers[kind] = fn

    def submit(self, kind, clip_id=None, params=None):
        self.submitted.append((kind, clip_id, params))
        return len(self.submitted)


class FakeBackend:
    def __init__(self, db, tmp_path):
        self.db, self.bus, self.settings = db, EventBus(), AppSettings()
        self.secrets = SecretStore(tmp_path / "s.bin", use_keyring=False)
        self.jobs = FakeJobs()


class FakeClient:
    uploads = []

    def __init__(self, acc):
        self.acc = acc

    def is_connected(self):
        return True

    def upload(self, path, title, desc, tags, privacy, progress=None):
        FakeClient.uploads.append((self.acc.name, title, privacy))
        progress and progress(1.0)
        return f"vid-{self.acc.id}"

    def upload_to_inbox(self, path, progress=None):
        FakeClient.uploads.append((self.acc.name, "inbox", "inbox"))
        return "pub-1"


@pytest.fixture
def setup(db, tmp_path, monkeypatch):
    FakeClient.uploads = []
    b = FakeBackend(db, tmp_path)
    am = AccountManager(b)
    monkeypatch.setattr(am, "client", lambda acc: FakeClient(acc))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with db.session() as s:
        a = Streamer(login="alice", authorized=True)
        bob = Streamer(login="bob", authorized=True)
        sim = Streamer(login=acc_mod.SIMULATION_LOGIN, authorized=True)
        s.add_all([a, bob, sim])
        s.flush()
        ids = {}
        for st in (a, bob, sim):
            c = Clip(streamer_id=st.id, source_path="x", title=f"{st.login} clip", hashtags=["#gg"])
            s.add(c)
            s.flush()
            e = ExportedVideo(clip_id=c.id, path=str(video), qc_passed=True)
            s.add(e)
            s.flush()
            ids[st.login] = (st.id, e.id)
        y1 = PublishAccount(platform="youtube", name="Main", token_key="t1", auto_upload=True, privacy="public")
        y2 = PublishAccount(platform="youtube", name="Second", token_key="t2", auto_upload=True)
        t1 = PublishAccount(platform="tiktok", name="Tok", token_key="t3", auto_upload=False, privacy="inbox")
        s.add_all([y1, y2, t1])
        s.flush()
        accs = {"Main": y1.id, "Second": y2.id, "Tok": t1.id}
    return b, am, ids, accs


def test_auto_upload_needs_global_opt_in_and_confirmation(setup):
    b, am, ids, accs = setup
    eid = ids["alice"][1]
    assert am.auto_targets(eid) == []                       # off by default
    b.settings.publishing.auto_publish = True
    assert am.auto_targets(eid) == []                       # not confirmed yet
    b.settings.publishing.auto_publish_confirmed = True
    assert sorted(am.auto_targets(eid)) == sorted([accs["Main"], accs["Second"]])   # Tok has auto off


def test_auto_upload_rules(setup, db):
    b, am, ids, accs = setup
    b.settings.publishing.auto_publish = b.settings.publishing.auto_publish_confirmed = True
    assert am.auto_targets(ids[acc_mod.SIMULATION_LOGIN][1]) == []          # never simulation clips
    am.update(accs["Second"], streamer_filter=[ids["alice"][0]])
    assert accs["Second"] not in am.auto_targets(ids["bob"][1])            # per-account streamer filter
    assert accs["Second"] in am.auto_targets(ids["alice"][1])
    with db.session() as s:
        s.get(ExportedVideo, ids["bob"][1]).qc_passed = False
    assert am.auto_targets(ids["bob"][1]) == []                             # QC failed
    with db.session() as s:
        c = s.get(ExportedVideo, ids["alice"][1]).clip
        c.duplicate_of = 99
    assert am.auto_targets(ids["alice"][1]) == []                           # duplicate


def test_export_event_queues_one_upload_per_account_and_job_uploads(setup):
    b, am, ids, accs = setup
    b.settings.publishing.auto_publish = b.settings.publishing.auto_publish_confirmed = True
    eid = ids["alice"][1]
    b.bus.emit("video.exported", "x", "success", export_id=eid, duplicate=False)
    jobs = [p for k, _, p in b.jobs.submitted if k == "upload"]
    assert sorted(p["account_id"] for p in jobs) == sorted([accs["Main"], accs["Second"]])

    class Ctx:
        def __init__(self, params):
            self.params = params

        def progress(self, f, stage=""):
            pass
    for p in jobs:
        b.jobs.handlers["upload"](Ctx(p))
    assert sorted(FakeClient.uploads) == [("Main", "alice clip", "public"), ("Second", "alice clip", "private")]
    # recorded per account -> never uploaded twice
    assert am.auto_targets(eid) == []
    b.jobs.handlers["upload"](Ctx(jobs[0]))
    assert len(FakeClient.uploads) == 2


def test_manual_upload_to_tiktok_inbox(setup, db):
    b, am, ids, accs = setup
    res = am.upload_now(accs["Tok"], ids["bob"][1], "t", "d", [])
    assert res["mode"] == "inbox"
    with db.session() as s:
        assert published_key(accs["Tok"]) in s.get(ExportedVideo, ids["bob"][1]).published


def test_remove_account_deletes_token(setup):
    b, am, ids, accs = setup
    b.secrets.set_json("t1", {"refresh_token": "r"})
    am.remove(accs["Main"])
    assert am.get(accs["Main"]) is None and b.secrets.get_json("t1") is None


def test_legacy_single_tokens_become_accounts(db, tmp_path):
    b = FakeBackend(db, tmp_path)
    b.secrets.set_json("youtube_token", {"refresh_token": "r", "access_token": "a", "expires_at": 0})
    am = AccountManager(b)
    accs = am.list()
    assert [(a.platform, a.token_key) for a in accs] == [("youtube", "youtube_token")]
    AccountManager(b)                                           # idempotent
    assert len(am.list()) == 1


def test_migration_from_schema_1(tmp_path):
    """An existing v1 database (no platform columns, no publish_accounts table) is upgraded in place."""
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE streamers (id INTEGER PRIMARY KEY, login VARCHAR(64) UNIQUE, display_name VARCHAR(128),
            twitch_user_id VARCHAR(32), profile_image_url VARCHAR(512), profile_image_path VARCHAR(512),
            authorized BOOLEAN, monitoring_enabled BOOLEAN, is_live BOOLEAN, stream_title VARCHAR(512),
            category VARCHAR(256), viewer_count INTEGER, baseline JSON, created_at DATETIME, last_live_at DATETIME);
        CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER, applied_at DATETIME);
        INSERT INTO schema_version (version) VALUES (1);
        INSERT INTO streamers (login, display_name, authorized, monitoring_enabled, is_live, viewer_count, baseline)
            VALUES ('oldie', 'Oldie', 1, 1, 0, 0, '{}');
    """)
    con.commit()
    con.close()
    d = Database(path)
    try:
        assert d.current_version() == SCHEMA_VERSION
        with d.session() as s:
            st = s.query(Streamer).one()
            assert (st.login, st.platform, st.youtube_channel_id) == ("oldie", "twitch", "")
            s.add(PublishAccount(platform="youtube", name="x", token_key="k"))
    finally:
        d.close()


def test_fresh_database_migrates_cleanly(tmp_path):
    d = Database(tmp_path / "new.sqlite3")
    assert d.current_version() == SCHEMA_VERSION
    d.close()
