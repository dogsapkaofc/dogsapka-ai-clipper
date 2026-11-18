"""Remote control API used by the iPhone app: pairing, auth, control, media streaming."""
import httpx
import pytest

from dogsapka_clipper.core.paths import AppPaths
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.models import Clip, ExportedVideo, ProcessingJob, Streamer
from dogsapka_clipper.services.backend import Backend
from dogsapka_clipper.services.remote import PAIR_MAX_ATTEMPTS, RemoteServer


@pytest.fixture
def env(tmp_path):
    # own secret store: tests must never touch the real OS credential vault
    b = Backend(AppPaths.create(tmp_path / "data"), secrets=SecretStore(tmp_path / "s.bin", use_keyring=False))
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(256)) * 400)              # 102 400 bytes
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    with b.db.session() as s:
        st = Streamer(login="anna", display_name="Anna", authorized=True, monitoring_enabled=False)
        s.add(st)
        s.flush()
        c = Clip(streamer_id=st.id, source_path="x", title="Nice play", thumbnail_path=str(thumb), duration=21.0)
        s.add(c)
        s.flush()
        e = ExportedVideo(clip_id=c.id, path=str(video), qc_passed=True)
        s.add(e)
        s.add(ProcessingJob(kind="process", clip_id=c.id, status="failed", error="boom"))
        s.flush()
        ids = {"streamer": st.id, "clip": c.id, "export": e.id}
    srv = RemoteServer(b, port=0)
    srv.start()
    http = httpx.Client(base_url=f"http://127.0.0.1:{srv.port}", timeout=10)
    yield b, srv, http, ids, video
    http.close()
    srv.stop()
    b.db.close()


def pair(srv, http):
    code = srv.open_pairing()
    r = http.post("/api/pair", json={"code": code, "device": "Test iPhone"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_info_is_public_everything_else_needs_a_token(env):
    b, srv, http, ids, _ = env
    assert http.get("/api/info").json()["app"] == "DOGSAPKA AI CLIPPER"
    for path in ("/api/status", "/api/streamers", "/api/clips", "/api/jobs", f"/api/exports/{ids['export']}/video"):
        assert http.get(path).status_code == 401
        assert http.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_pairing_code_is_one_time_and_limited(env):
    b, srv, http, ids, _ = env
    assert http.post("/api/pair", json={"code": "123456"}).status_code == 403      # pairing not open
    code = srv.open_pairing()
    for _ in range(PAIR_MAX_ATTEMPTS):
        assert http.post("/api/pair", json={"code": "000000" if code != "000000" else "111111"}).status_code == 403
    assert http.post("/api/pair", json={"code": code}).status_code == 403          # locked after too many tries
    headers = pair(srv, http)
    assert http.post("/api/pair", json={"code": srv._pairing.code if srv._pairing else "x"}).status_code == 403
    assert http.get("/api/status", headers=headers).status_code == 200
    stored = srv.devices()
    assert len(stored) == 1 and stored[0]["name"] == "Test iPhone"
    assert headers["Authorization"][7:] not in str(stored)                          # only the hash is stored


def test_revoked_device_loses_access(env):
    b, srv, http, ids, _ = env
    headers = pair(srv, http)
    srv.revoke(srv.devices()[0]["id"])
    assert http.get("/api/status", headers=headers).status_code == 401


def test_monitoring_control(env):
    b, srv, http, ids, _ = env
    h = pair(srv, http)
    assert http.post(f"/api/streamers/{ids['streamer']}/monitoring", json={"enabled": True}, headers=h).status_code == 200
    st = [x for x in http.get("/api/streamers", headers=h).json() if x["id"] == ids["streamer"]][0]
    assert st["monitoring"] is True and st["name"] == "Anna"
    r = http.post("/api/monitoring/all", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["changed"] == 1
    assert http.get("/api/status", headers=h).json()["monitored"] == 0


def test_clips_thumbnail_and_video_range(env):
    b, srv, http, ids, video = env
    h = pair(srv, http)
    clips = http.get("/api/clips", headers=h).json()
    assert clips[0]["title"] == "Nice play" and clips[0]["exports"][0]["id"] == ids["export"]
    assert http.get(f"/api/clips/{ids['clip']}/thumbnail", headers=h).content.startswith(b"\xff\xd8")
    full = http.get(f"/api/exports/{ids['export']}/video", headers=h)
    assert full.status_code == 200 and full.content == video.read_bytes()
    part = http.get(f"/api/exports/{ids['export']}/video", headers={**h, "Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == video.read_bytes()[100:200]
    assert part.headers["Content-Range"] == f"bytes 100-199/{video.stat().st_size}"
    tail = http.get(f"/api/exports/{ids['export']}/video", headers={**h, "Range": "bytes=-10"})
    assert tail.content == video.read_bytes()[-10:]


def test_upload_needs_rights_confirmation(env):
    b, srv, http, ids, _ = env
    h = pair(srv, http)
    r = http.post(f"/api/exports/{ids['export']}/upload", json={"account_ids": [1]}, headers=h)
    assert r.status_code == 400 and "confirm_rights" in r.json()["error"]


def test_jobs_list_and_retry(env):
    b, srv, http, ids, _ = env
    h = pair(srv, http)
    jobs = http.get("/api/jobs", headers=h).json()
    assert jobs[0]["status"] == "failed" and jobs[0]["error"] == "boom"
    assert http.post(f"/api/jobs/{jobs[0]['id']}/retry", json={}, headers=h).status_code == 200
    assert http.get("/api/jobs", headers=h).json()[0]["status"] == "queued"


def test_bad_requests(env):
    b, srv, http, ids, _ = env
    h = pair(srv, http)
    assert http.get("/api/nope", headers=h).status_code == 404
    assert http.post("/api/monitoring/all", content=b"[1,2]", headers=h).status_code == 400
