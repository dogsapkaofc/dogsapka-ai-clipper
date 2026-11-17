"""Twitch Helix API client and OAuth (official endpoints only).

Authentication options:
* App access token (client-credentials) when the user provided a Client Secret -
  sufficient for reading public stream status.
* User access token via the OAuth *Device Code Grant Flow* - the recommended flow for
  desktop applications. Tokens are refreshed automatically and stored in the SecretStore.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from dogsapka_clipper.core.secrets import SecretStore

log = logging.getLogger(__name__)

ID_BASE = "https://id.twitch.tv/oauth2"
HELIX = "https://api.twitch.tv/helix"
DEFAULT_SCOPES = ["chat:read"]


class TwitchError(Exception):
    """Base error; ``hint`` contains user-facing troubleshooting text."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class TwitchAuthError(TwitchError):
    pass


class TwitchAPIError(TwitchError):
    def __init__(self, message: str, status: int = 0, hint: str = "") -> None:
        super().__init__(message, hint)
        self.status = status


@dataclass
class StreamInfo:
    user_id: str
    user_login: str
    user_name: str
    stream_id: str
    title: str
    game_name: str
    viewer_count: int
    started_at: str
    thumbnail_url: str


@dataclass
class UserInfo:
    id: str
    login: str
    display_name: str
    profile_image_url: str


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class TwitchAuth:
    """Manages access tokens. Never logs token values."""

    TOKEN_KEY = "twitch_token"
    SECRET_KEY = "twitch_client_secret"

    def __init__(self, client_id: str, store: SecretStore, http: httpx.AsyncClient | None = None) -> None:
        self.client_id = client_id.strip()
        self.store = store
        self.http = http or httpx.AsyncClient(timeout=20)
        self._lock = asyncio.Lock()

    @property
    def client_secret(self) -> str | None:
        return self.store.get(self.SECRET_KEY)

    def token_record(self) -> dict | None:
        return self.store.get_json(self.TOKEN_KEY)

    def has_user_token(self) -> bool:
        rec = self.token_record()
        return bool(rec and rec.get("type") == "user" and rec.get("refresh_token"))

    def logout(self) -> None:
        self.store.set_json(self.TOKEN_KEY, None)

    async def _post_token(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            r = await self.http.post(f"{ID_BASE}/token", data=data)
        except httpx.HTTPError as e:
            raise TwitchAPIError(f"Network error contacting Twitch: {e.__class__.__name__}",
                                 hint="Check your internet connection.") from e
        body = _json(r)
        if r.status_code >= 400:
            raise TwitchAuthError(f"Twitch rejected the token request ({r.status_code}): {body.get('message', '')}",
                                  hint="Verify the Client ID / Secret in Settings or reconnect your account.")
        return body

    async def get_token(self) -> str:
        """Return a valid access token, refreshing or creating as needed."""
        if not self.client_id:
            raise TwitchAuthError("No Twitch Client ID configured.",
                                  hint="Register an application at dev.twitch.tv/console and enter its Client ID in Settings.")
        async with self._lock:
            rec = self.token_record()
            if rec and rec.get("expires_at", 0) - 120 > time.time():
                return rec["access_token"]
            if rec and rec.get("type") == "user" and rec.get("refresh_token"):
                return await self._refresh(rec)
            secret = self.client_secret
            if secret:
                body = await self._post_token({"client_id": self.client_id, "client_secret": secret,
                                               "grant_type": "client_credentials"})
                self._save(body, "app")
                return body["access_token"]
            raise TwitchAuthError("Twitch account is not connected.",
                                  hint="Open Settings → Twitch and click 'Connect Twitch account', "
                                       "or provide a Client Secret for app-only access.")

    async def _refresh(self, rec: dict) -> str:
        data = {"client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": rec["refresh_token"]}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            body = await self._post_token(data)
        except TwitchAuthError:
            self.logout()
            raise TwitchAuthError("Twitch session expired and could not be refreshed.",
                                  hint="Reconnect your Twitch account in Settings.")
        self._save(body, "user")
        return body["access_token"]

    def _save(self, body: dict, kind: str) -> None:
        self.store.set_json(self.TOKEN_KEY, {
            "type": kind,
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in", 3600)),
            "scopes": body.get("scope", []),
        })

    # -- device code flow ---------------------------------------------
    async def start_device_flow(self, scopes: list[str] | None = None) -> DeviceCode:
        if not self.client_id:
            raise TwitchAuthError("No Twitch Client ID configured.",
                                  hint="Enter your application's Client ID in Settings first.")
        try:
            r = await self.http.post(f"{ID_BASE}/device",
                                     data={"client_id": self.client_id, "scopes": " ".join(scopes or DEFAULT_SCOPES)})
        except httpx.HTTPError as e:
            raise TwitchAPIError(f"Network error: {e.__class__.__name__}") from e
        body = _json(r)
        if r.status_code >= 400:
            raise TwitchAuthError(f"Could not start Twitch login: {body.get('message', r.status_code)}",
                                  hint="Make sure the application is registered as a 'Public' client type.")
        return DeviceCode(body["device_code"], body["user_code"], body["verification_uri"],
                          int(body.get("expires_in", 1800)), int(body.get("interval", 5)))

    async def poll_device_flow(self, dc: DeviceCode, scopes: list[str] | None = None,
                               cancel: asyncio.Event | None = None) -> None:
        deadline = time.time() + dc.expires_in
        interval = dc.interval
        while time.time() < deadline:
            if cancel and cancel.is_set():
                raise TwitchAuthError("Login cancelled.")
            await asyncio.sleep(interval)
            r = await self.http.post(f"{ID_BASE}/token", data={
                "client_id": self.client_id, "scopes": " ".join(scopes or DEFAULT_SCOPES),
                "device_code": dc.device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            body = _json(r)
            if r.status_code == 200:
                self._save(body, "user")
                return
            msg = str(body.get("message", ""))
            if "authorization_pending" in msg:
                continue
            if "slow_down" in msg:
                interval += 5
                continue
            raise TwitchAuthError(f"Twitch login failed: {msg or r.status_code}")
        raise TwitchAuthError("Twitch login timed out.", hint="Start the login again.")


class HelixClient:
    def __init__(self, auth: TwitchAuth) -> None:
        self.auth = auth
        self.http = auth.http

    async def _get(self, path: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        for attempt in range(3):
            token = await self.auth.get_token()
            headers = {"Client-Id": self.auth.client_id, "Authorization": f"Bearer {token}"}
            try:
                r = await self.http.get(f"{HELIX}/{path}", params=params, headers=headers)
            except httpx.HTTPError as e:
                raise TwitchAPIError(f"Network error contacting Twitch: {e.__class__.__name__}",
                                     hint="Check your internet connection; monitoring will retry automatically.") from e
            if r.status_code == 401 and attempt == 0:
                rec = self.auth.token_record() or {}
                rec["expires_at"] = 0               # force refresh
                self.auth.store.set_json(self.auth.TOKEN_KEY, rec)
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 5))
                await asyncio.sleep(max(1.0, min(60.0, reset - time.time())))
                continue
            if r.status_code >= 400:
                body = _json(r)
                raise TwitchAPIError(f"Twitch API error {r.status_code}: {body.get('message', '')}", r.status_code)
            return _json(r)
        raise TwitchAPIError("Twitch API request failed after retries.")

    async def get_users(self, logins: list[str]) -> list[UserInfo]:
        out: list[UserInfo] = []
        for chunk in _chunks(logins, 100):
            data = await self._get("users", [("login", l) for l in chunk])
            out += [UserInfo(u["id"], u["login"], u["display_name"], u.get("profile_image_url", ""))
                    for u in data.get("data", [])]
        return out

    async def get_streams(self, logins: list[str]) -> dict[str, StreamInfo]:
        """Return live streams keyed by lowercase login; offline channels are absent."""
        out: dict[str, StreamInfo] = {}
        for chunk in _chunks(logins, 100):
            data = await self._get("streams", [("user_login", l) for l in chunk] + [("first", "100")])
            for s in data.get("data", []):
                if s.get("type") != "live":
                    continue
                out[s["user_login"].lower()] = StreamInfo(
                    s["user_id"], s["user_login"], s["user_name"], s["id"], s.get("title", ""),
                    s.get("game_name", ""), int(s.get("viewer_count", 0)), s.get("started_at", ""),
                    s.get("thumbnail_url", ""))
        return out


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]
