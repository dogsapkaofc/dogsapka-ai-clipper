import asyncio
import time

import httpx
import pytest

from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.twitch.api import HelixClient, TwitchAuth, TwitchAuthError
from dogsapka_clipper.twitch.chat import parse_privmsg


def make(tmp_path, handler, secret="sec"):
    store = SecretStore(tmp_path / "s.bin", use_keyring=False)
    if secret:
        store.set(TwitchAuth.SECRET_KEY, secret)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = TwitchAuth("cid", store, http)
    return auth, HelixClient(auth), store


def stream_json(login):
    return {"user_id": "1", "user_login": login, "user_name": login.title(), "id": "s1", "type": "live",
            "title": "t", "game_name": "G", "viewer_count": 5, "started_at": "", "thumbnail_url": ""}


def test_live_offline_detection(tmp_path):
    calls = {"token": 0}

    def handler(req: httpx.Request):
        if req.url.path == "/oauth2/token":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "A", "expires_in": 3600})
        if req.url.path == "/helix/streams":
            assert req.headers["Authorization"] == "Bearer A" and req.headers["Client-Id"] == "cid"
            return httpx.Response(200, json={"data": [stream_json("alive")]})
        return httpx.Response(404)
    auth, helix, _ = make(tmp_path, handler)
    live = asyncio.run(helix.get_streams(["alive", "dead"]))
    assert "alive" in live and "dead" not in live
    assert live["alive"].game_name == "G"
    assert calls["token"] == 1


def test_auth_failure_raises_helpful_error(tmp_path):
    def handler(req):
        return httpx.Response(400, json={"message": "invalid client secret"})
    auth, helix, _ = make(tmp_path, handler)
    with pytest.raises(TwitchAuthError) as e:
        asyncio.run(helix.get_streams(["x"]))
    assert e.value.hint


def test_not_connected_without_secret(tmp_path):
    auth, helix, _ = make(tmp_path, lambda r: httpx.Response(500), secret=None)
    with pytest.raises(TwitchAuthError):
        asyncio.run(auth.get_token())


def test_expired_user_token_is_refreshed(tmp_path):
    seen = {}

    def handler(req):
        if req.url.path == "/oauth2/token":
            body = dict(httpx.QueryParams(req.content.decode()))
            seen.update(body)
            return httpx.Response(200, json={"access_token": "NEW", "refresh_token": "R2", "expires_in": 3600})
        return httpx.Response(200, json={"data": []})
    auth, helix, store = make(tmp_path, handler, secret=None)
    store.set_json(TwitchAuth.TOKEN_KEY, {"type": "user", "access_token": "OLD", "refresh_token": "R1",
                                          "expires_at": time.time() - 10})
    assert asyncio.run(auth.get_token()) == "NEW"
    assert seen["grant_type"] == "refresh_token" and seen["refresh_token"] == "R1"
    assert store.get_json(TwitchAuth.TOKEN_KEY)["refresh_token"] == "R2"


def test_401_triggers_token_refresh_and_retry(tmp_path):
    state = {"n": 0}

    def handler(req):
        if req.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": f"T{state['n']}", "expires_in": 3600})
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(401, json={"message": "Invalid OAuth token"})
        return httpx.Response(200, json={"data": []})
    auth, helix, _ = make(tmp_path, handler)
    assert asyncio.run(helix.get_streams(["x"])) == {}
    assert state["n"] == 2


def test_device_flow(tmp_path):
    polls = {"n": 0}

    def handler(req):
        if req.url.path == "/oauth2/device":
            return httpx.Response(200, json={"device_code": "D", "user_code": "ABCD", "verification_uri": "https://v",
                                             "expires_in": 60, "interval": 0})
        polls["n"] += 1
        if polls["n"] < 3:
            return httpx.Response(400, json={"message": "authorization_pending"})
        return httpx.Response(200, json={"access_token": "U", "refresh_token": "R", "expires_in": 100})
    auth, _, store = make(tmp_path, handler, secret=None)

    async def flow():
        dc = await auth.start_device_flow()
        assert dc.user_code == "ABCD"
        await auth.poll_device_flow(dc)
    asyncio.run(flow())
    assert auth.has_user_token()


def test_parse_privmsg_anonymizes():
    line = ("@badge-info=;emotes=25:0-4;user-id=123 :someuser!someuser@someuser.tmi.twitch.tv "
            "PRIVMSG #chan :Kappa that was INSANE")
    m = parse_privmsg(line)
    assert m.text == "Kappa that was INSANE"
    assert m.emotes == ["Kappa"]
    assert "someuser" not in m.user_hash and len(m.user_hash) == 16
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan :Welcome") is None
