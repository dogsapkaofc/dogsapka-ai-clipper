import json
import threading
import urllib.parse
import urllib.request

import httpx
import pytest

from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.publishing.oauth import OAuthError, loopback_authorize, pkce_pair
from dogsapka_clipper.publishing.tiktok import TikTokClient, chunk_plan
from dogsapka_clipper.publishing.youtube import YouTubeClient


def test_pkce():
    v, c = pkce_pair()
    assert 43 <= len(v) <= 128 and "=" not in c


def fake_browser(code="CODE", tamper=False):
    def open_(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        state = "bad" if tamper else q["state"]
        cb = f"{q['redirect_uri']}?code={code}&state={state}"
        threading.Thread(target=lambda: urllib.request.urlopen(cb).read(), daemon=True).start()
    return open_


def test_loopback_flow_and_state_check():
    code, redirect, _ = loopback_authorize(lambda r, s: f"https://auth?redirect_uri={r}&state={s}",
                                           open_browser=fake_browser())
    assert code == "CODE" and redirect.startswith("http://127.0.0.1:")
    with pytest.raises(OAuthError):
        loopback_authorize(lambda r, s: f"https://auth?redirect_uri={r}&state={s}", open_browser=fake_browser(tamper=True))


def test_youtube_resumable_upload(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    got = {}

    def handler(req: httpx.Request):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})
        if req.method == "POST":
            got["meta"] = json.loads(req.content)
            return httpx.Response(200, headers={"Location": "https://upload/session1"})
        if req.method == "PUT":
            got["range"] = req.headers["Content-Range"]
            return httpx.Response(200, json={"id": "VID123"})
        return httpx.Response(404)
    store = SecretStore(tmp_path / "s", use_keyring=False)
    yt = YouTubeClient("cid", store, httpx.Client(transport=httpx.MockTransport(handler)))
    yt.login(open_browser=fake_browser())
    assert yt.is_connected()
    prog = []
    vid = yt.upload(video, "Title", "Desc", ["#gaming"], "private", progress=prog.append)
    assert vid == "VID123" and got["range"] == "bytes 0-999/1000"
    assert got["meta"]["status"]["privacyStatus"] == "private" and got["meta"]["snippet"]["tags"] == ["gaming"]
    assert prog[-1] == 1.0


def test_tiktok_chunk_plan():
    assert chunk_plan(1000) == (1000, 1)
    cs, n = chunk_plan(100 * 1024 * 1024)
    assert cs == 10 * 1024 * 1024 and n == 10


def test_tiktok_inbox_upload_and_privacy_check(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"y" * 500)
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path == "/v2/oauth/token/":
            return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 100,
                                             "scope": "user.info.basic,video.upload,video.publish", "open_id": "o"})
        if req.url.path.endswith("/inbox/video/init/"):
            return httpx.Response(200, json={"data": {"publish_id": "P1", "upload_url": "https://up/1"},
                                             "error": {"code": "ok"}})
        if req.url.path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
        if req.method == "PUT":
            assert req.headers["Content-Range"] == "bytes 0-499/500"
            return httpx.Response(201)
        return httpx.Response(404)
    store = SecretStore(tmp_path / "s", use_keyring=False)
    store.set(TikTokClient.SECRET_KEY, "sec")
    tt = TikTokClient("key", store, httpx.Client(transport=httpx.MockTransport(handler)))
    tt.login(direct_post=True, open_browser=fake_browser())
    assert tt.upload_to_inbox(video) == "P1"
    from dogsapka_clipper.publishing.tiktok import TikTokError
    with pytest.raises(TikTokError):
        tt.direct_post(video, "t", "PUBLIC_TO_EVERYONE")   # not allowed for this creator
