"""Server-side scheduling: no real account or upload is used in these tests."""
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.models import AppSetting, ExportedVideo, ProcessingJob, PublishAccount
from dogsapka_clipper.processing.jobs import JobQueue
from dogsapka_clipper.publishing.accounts import AccountManager, published_key
from dogsapka_clipper.publishing.youtube import YouTubeClient, YouTubeError, YouTubeUploadUncertainError
from test_accounts import FakeBackend

UTC = timezone.utc


@pytest.fixture
def account(db, tmp_path, monkeypatch):
    b = FakeBackend(db, tmp_path)
    manager = AccountManager(b)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    with db.session() as s:
        acc = PublishAccount(platform="youtube", name="Gaming", token_key="yt", privacy="public")
        exports = [ExportedVideo(path=str(path)) for _ in range(7)]
        s.add_all([acc, *exports])
        s.flush()
    client = SimpleNamespace(is_connected=lambda: True, channel_id=lambda: "CHANNEL", uploads=[], schedules=[], fail=False)

    def upload(*args, **kwargs):
        client.uploads.append(args)
        return f"video-{len(client.uploads)}"

    def schedule(vid, when):
        client.schedules.append((vid, when))
        if client.fail:
            raise YouTubeError("scheduling denied")
        return when.isoformat().replace("+00:00", "Z")

    client.upload, client.schedule = upload, schedule
    monkeypatch.setattr(manager, "client", lambda _: client)
    b.settings.publishing.youtube_schedule_enabled = True
    return b, manager, acc.id, [e.id for e in exports], client


def test_automatic_schedule_private_then_three_distinct_slots_daily(account):
    b, am, aid, exports, cl = account
    results = [am.upload_now(aid, eid, "t", "d", [], automatic=True) for eid in exports]
    assert all(args[4] == "private" for args in cl.uploads)
    instants = [datetime.fromisoformat(r["publish_at"].replace("Z", "+00:00")) for r in results]
    assert len(set(instants)) == 7
    local = [v.astimezone(ZoneInfo("Europe/Budapest")) for v in instants]
    assert all(v.strftime("%H:%M") in ("09:00", "15:00", "21:00") for v in local)
    assert max(sum(v.date() == day.date() for v in local) for day in local) <= 3
    assert all(r["state"] == "scheduled" for r in results)
    assert am.upload_now(aid, exports[0], "t", "d", [], automatic=True) == results[0]
    assert len(cl.uploads) == 7


def test_manual_privacy_remains_explicit_and_scheduled_mode_works_when_global_off(account):
    b, am, aid, exports, cl = account
    plain = am.upload_now(aid, exports[0], "t", "d", [], privacy="private")
    assert "publish_at" not in plain and not cl.schedules
    b.settings.publishing.youtube_schedule_enabled = False
    am.upload_now(aid, exports[1], "t", "d", [], privacy="scheduled")
    assert cl.schedules and cl.uploads[-1][4] == "private"


def test_schedule_retry_after_restart_reuses_video_and_reallocates_expired_slot(account):
    b, am, aid, exports, cl = account
    eid = exports[0]
    cl.fail = True
    with pytest.raises(YouTubeError):
        am.upload_now(aid, eid, "t", "d", [], automatic=True)
    with b.db.session() as s:
        pending = s.get(ExportedVideo, eid).published[published_key(aid)]
        assert pending["state"] == "schedule_pending" and pending["id"] == "video-1"
        row = s.get(AppSetting, f"yt_schedule:{aid}:{eid}")
        row.value = {**row.value, "publish_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()}
    cl.fail = False
    # Persisted video ID survives a new manager and changed privacy/settings.
    am = AccountManager(b)
    am.client = lambda _: cl
    result = am.upload_now(aid, eid, "t", "d", [], privacy="public")
    assert len(cl.uploads) == 1 and cl.schedules[-1][0] == "video-1"
    assert result["state"] == "scheduled" and cl.schedules[-1][1] > datetime.now(UTC)


def test_concurrent_channel_reservations_across_managers_share_daily_capacity(account):
    b, am, aid, exports, cl = account
    other = AccountManager(b)
    with ThreadPoolExecutor(max_workers=7) as pool:
        slots = list(pool.map(lambda eid: (am if eid % 2 else other)._reserve_slot(aid, eid, "CHANNEL"), exports))
    assert len(set(slots)) == 7
    # A second connection for the same actual YouTube channel shares the limit.
    slot = other._reserve_slot(aid + 1, 99, "CHANNEL")
    assert slot > max(slots)


def test_long_upload_allocates_only_after_upload_finishes(account):
    b, am, aid, exports, cl = account
    original = cl.upload
    def upload(*args, **kwargs):
        with b.db.session() as s:
            assert not s.scalars(select(AppSetting).where(AppSetting.key.like("yt_schedule:%"))).all()
        return original(*args, **kwargs)
    cl.upload = upload
    am.upload_now(aid, exports[0], "t", "d", [], automatic=True)
    assert cl.schedules[0][1] >= datetime.now(UTC) + timedelta(minutes=14)


def test_uncertain_upload_is_not_silently_inserted_twice(account):
    b, am, aid, exports, cl = account
    def uncertain(*a, **kw):
        cl.uploads.append(a)
        raise YouTubeUploadUncertainError("lost final response")
    cl.upload = uncertain
    with pytest.raises(YouTubeUploadUncertainError):
        am.upload_now(aid, exports[0], "t", "d", [], automatic=True)
    with pytest.raises(RuntimeError, match="already running or was interrupted"):
        am.upload_now(aid, exports[0], "t", "d", [], automatic=True)
    assert len(cl.uploads) == 1


def test_concurrent_upload_of_same_export_has_one_insert(account):
    b, am, aid, exports, cl = account
    entered, finish = threading.Event(), threading.Event()
    original = cl.upload
    def upload(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return original(*args, **kwargs)
    cl.upload = upload
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(am.upload_now, aid, exports[0], "t", "d", [], automatic=True)
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeError, match="already running"):
                am.upload_now(aid, exports[0], "t", "d", [], automatic=True)
        finally:
            finish.set()
        assert first.result()["state"] == "scheduled"
    assert len(cl.uploads) == 1


def test_late_completion_progress_cannot_lose_uploaded_video_id(account):
    b, am, aid, exports, cl = account
    original = cl.upload
    def upload(*args, **kwargs):
        result = original(*args, **kwargs)
        kwargs["progress"](1.0)
        return result
    cl.upload = upload
    def progress(_):
        raise RuntimeError("cancelled after remote completion")
    result = am.upload_now(aid, exports[0], "t", "d", [], progress, automatic=True)
    assert result["state"] == "scheduled" and result["id"] == "video-1"


def test_pending_video_can_resume_after_crashed_process(account):
    b, am, aid, exports, cl = account
    with b.db.session() as s:
        s.get(ExportedVideo, exports[0]).published = {published_key(aid): {
            "id": "existing-private", "state": "schedule_pending", "platform": "youtube",
            "privacy": "private", "account": "Gaming"}}
        s.add(AppSetting(key=f"upload_claim:{aid}:{exports[0]}", value={"pid": -1}))
    result = am.upload_now(aid, exports[0], "t", "d", [], automatic=True)
    assert not cl.uploads and result["id"] == "existing-private" and result["state"] == "scheduled"


def test_bulk_retry_includes_pending_schedule_but_skips_scheduled(db):
    q = JobQueue(db, EventBus())
    with db.session() as s:
        export = ExportedVideo(path="video.mp4", published={
            "account:1": {"id": "private-video", "state": "schedule_pending"},
            "account:2": {"id": "ready", "state": "scheduled"}})
        s.add(export)
        s.flush()
        for aid in (1, 2):
            s.add(ProcessingJob(kind="upload", status="failed", params={"account_id": aid, "export_id": export.id}))
    assert q.retry_failed_uploads() == 1
    with db.session() as s:
        assert s.scalars(select(ProcessingJob).where(ProcessingJob.status == "queued")).one().params["account_id"] == 1


def test_reauthorization_keeps_identity_and_restores_token_for_wrong_channel(account):
    b, am, aid, exports, cl = account
    b.secrets.set_json("yt", {"refresh_token": "original"})
    def login(_):
        assert b.secrets.get_json("yt")["refresh_token"] == "original"
        b.secrets.set_json(cl.TOKEN_KEY, {"refresh_token": "wrong"})
    cl.login = login
    cl.TOKEN_KEY = "yt"
    cl.channel_id = lambda: "CHANNEL" if b.secrets.get_json(cl.TOKEN_KEY)["refresh_token"] == "original" else "OTHER"
    with pytest.raises(RuntimeError, match="same YouTube channel"):
        am.reconnect_youtube(aid)
    assert b.secrets.get_json("yt")["refresh_token"] == "original"
    cl.channel_id = lambda: "CHANNEL"
    assert am.reconnect_youtube(aid).id == aid


def test_settings_roundtrip_and_corrupt_settings_fail_closed():
    cfg = AppSettings()
    cfg.publishing.youtube_schedule_enabled = True
    cfg.publishing.youtube_schedule_times = ["20:00", "08:00", "14:00"]
    restored = AppSettings.from_dict(cfg.to_dict())
    assert restored.publishing.youtube_schedule_times == ["08:00", "14:00", "20:00"]
    for bad in (["09:00"] * 3, None, "09:00", [9, 15, 21]):
        loaded = AppSettings.from_dict({"publishing": {"youtube_schedule_enabled": True, "youtube_schedule_times": bad}})
        assert not loaded.publishing.youtube_schedule_enabled
    assert not AppSettings.from_dict({"publishing": {"youtube_schedule_enabled": "false"}}).publishing.youtube_schedule_enabled
    assert not AppSettings.from_dict({"publishing": {"youtube_schedule_enabled": True,
             "youtube_schedule_timezone": "Missing/Zone"}}).publishing.youtube_schedule_enabled
    cfg.publishing.youtube_schedule_times = ["09:00"] * 3
    with pytest.raises(ValueError):
        cfg.validate()


def yt_client(tmp_path, handler):
    store = SecretStore(tmp_path / "tokens", use_keyring=False)
    store.set_json("youtube_token", {"access_token": "A", "refresh_token": "R", "expires_at": time.time() + 3600})
    return YouTubeClient("cid", store, httpx.Client(transport=httpx.MockTransport(handler)))


def test_native_schedule_uses_private_status_and_utc_timestamp(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"id": "VIDEO"})
    yt = yt_client(tmp_path, handler)
    future = (datetime.now(UTC) + timedelta(days=1)).astimezone(ZoneInfo("Europe/Budapest"))
    result = yt.schedule("VIDEO", future)
    req = calls[0]
    assert req.method == "PUT" and req.url.params["part"] == "status"
    status = json.loads(req.content)["status"]
    assert status["privacyStatus"] == "private" and status["publishAt"] == result and result.endswith("Z")
    assert datetime.fromisoformat(result.replace("Z", "+00:00")) == future


@pytest.mark.parametrize("bad", ["2026-10-01T09:00:00Z", "2030-10-01 09:00:00", datetime(2030, 1, 1), "nonsense"])
def test_bad_schedule_never_contacts_youtube(tmp_path, bad):
    def handler(_):
        pytest.fail("Invalid time caused a network request")
    with pytest.raises(YouTubeError):
        yt_client(tmp_path, handler).schedule("VIDEO", bad)


def test_legacy_permissions_give_actionable_reauthorize_error(tmp_path):
    yt = yt_client(tmp_path, lambda _: httpx.Response(403, json={"error": {"message": "insufficientPermissions"}}))
    with pytest.raises(YouTubeError, match="additional permission"):
        yt.schedule("VIDEO", datetime.now(UTC) + timedelta(days=1))


def test_lost_upload_completion_is_recovered_from_session_probe(tmp_path, monkeypatch):
    from dogsapka_clipper.publishing import youtube
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, headers={"Location": "https://upload.example/session"})
        if req.headers["Content-Range"].startswith("bytes */"):
            return httpx.Response(200, json={"id": "COMPLETE"})
        raise httpx.ReadError("final response lost")
    assert yt_client(tmp_path, handler).upload(video, "t", "d", []) == "COMPLETE"
