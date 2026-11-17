"""TikTok publishing through the official Content Posting API.

* ``upload_to_inbox`` (scope ``video.upload``): sends the video to the creator's TikTok inbox; the creator
  finishes posting in the TikTok app. Works for apps that are not yet audited.
* ``direct_post`` (scope ``video.publish``): only for apps approved by TikTok. Privacy options are taken
  from the creator's settings (``creator_info``), as TikTok requires. Unaudited apps can only post privately.

If no API access is configured, use the manual export (TikTok-ready MP4 + caption).
"""
from __future__ import annotations

import math
import time
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx

from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.publishing.oauth import OAuthError, loopback_authorize, pkce_pair

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) following TikTok's media transfer rules."""
    if size <= MAX_SINGLE:
        return size, 1
    count = size // CHUNK          # the final chunk absorbs the remainder (may be up to 2x chunk size)
    return CHUNK, count


class TikTokClient:
    TOKEN_KEY = "tiktok_token"
    SECRET_KEY = "tiktok_client_secret"

    def __init__(self, client_key: str, store: SecretStore, http: httpx.Client | None = None,
                 token_key: str | None = None) -> None:
        self.client_key = client_key
        if token_key:
            self.TOKEN_KEY = token_key      # one token per connected account
        self.store = store
        self.http = http or httpx.Client(timeout=120)

    def is_connected(self) -> bool:
        return bool((self.store.get_json(self.TOKEN_KEY) or {}).get("refresh_token"))

    def scopes(self) -> list[str]:
        return (self.store.get_json(self.TOKEN_KEY) or {}).get("scope", "").split(",")

    def logout(self) -> None:
        self.store.set_json(self.TOKEN_KEY, None)

    def login(self, direct_post: bool = False, open_browser=None) -> None:
        secret = self.store.get(self.SECRET_KEY)
        if not self.client_key or not secret:
            raise TikTokError("Enter your approved TikTok app's client key and secret in Settings → Publishing.")
        verifier, challenge = pkce_pair()
        scope = "user.info.basic,video.upload" + (",video.publish" if direct_post else "")

        def url(redirect, state):
            return AUTH_URL + "?" + urllib.parse.urlencode({
                "client_key": self.client_key, "response_type": "code", "scope": scope, "redirect_uri": redirect,
                "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        kw = {"open_browser": open_browser} if open_browser else {}
        code, redirect, _ = loopback_authorize(url, **kw)
        r = self.http.post(f"{API}/oauth/token/", data={
            "client_key": self.client_key, "client_secret": secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier})
        body = r.json()
        if r.status_code != 200 or "access_token" not in body:
            raise OAuthError(f"TikTok token exchange failed: {body.get('error_description') or body}")
        self._save(body)

    def _save(self, body: dict) -> None:
        self.store.set_json(self.TOKEN_KEY, {"access_token": body["access_token"],
                                             "refresh_token": body.get("refresh_token", ""),
                                             "expires_at": time.time() + int(body.get("expires_in", 86400)),
                                             "scope": body.get("scope", ""), "open_id": body.get("open_id", "")})

    def token(self) -> str:
        rec = self.store.get_json(self.TOKEN_KEY)
        if not rec:
            raise TikTokError("TikTok account is not connected.")
        if rec["expires_at"] - 60 > time.time():
            return rec["access_token"]
        r = self.http.post(f"{API}/oauth/token/", data={
            "client_key": self.client_key, "client_secret": self.store.get(self.SECRET_KEY) or "",
            "grant_type": "refresh_token", "refresh_token": rec["refresh_token"]})
        body = r.json()
        if "access_token" not in body:
            self.logout()
            raise TikTokError("TikTok authorization expired - please reconnect.")
        self._save(body)
        return body["access_token"]

    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(f"{API}{path}", json=payload,
                           headers={"Authorization": f"Bearer {self.token()}",
                                    "Content-Type": "application/json; charset=UTF-8"})
        body = r.json() if r.content else {}
        err = body.get("error", {})
        if r.status_code != 200 or (err.get("code") not in (None, "ok")):
            raise TikTokError(f"TikTok API error: {err.get('code', r.status_code)} {err.get('message', '')}")
        return body.get("data", {})

    def display_name(self) -> str:
        """Account name for the account list (scope user.info.basic)."""
        try:
            r = self.http.get(f"{API}/user/info/", params={"fields": "display_name"},
                              headers={"Authorization": f"Bearer {self.token()}"})
            return (r.json().get("data", {}).get("user", {}) or {}).get("display_name", "") if r.status_code == 200 else ""
        except (httpx.HTTPError, ValueError):
            return ""

    def creator_info(self) -> dict:
        return self._post("/post/publish/creator_info/query/", {})

    def _transfer(self, upload_url: str, path: Path, chunk: int, count: int, progress) -> None:
        size = path.stat().st_size
        with open(path, "rb") as f:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                f.seek(start)
                data = f.read(end - start + 1)
                r = self.http.put(upload_url, content=data, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise TikTokError(f"TikTok upload failed at chunk {i + 1}/{count} ({r.status_code})")
                if progress:
                    progress((i + 1) / count)

    def upload_to_inbox(self, path: Path, progress: Callable[[float], None] | None = None) -> str:
        size = path.stat().st_size
        chunk, count = chunk_plan(size)
        d = self._post("/post/publish/inbox/video/init/", {"source_info": {
            "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
        self._transfer(d["upload_url"], path, chunk, count, progress)
        return d["publish_id"]

    def direct_post(self, path: Path, title: str, privacy_level: str, disable_comment: bool = False,
                    disable_duet: bool = False, disable_stitch: bool = False,
                    progress: Callable[[float], None] | None = None) -> str:
        info = self.creator_info()
        if privacy_level not in info.get("privacy_level_options", []):
            raise TikTokError(f"Privacy '{privacy_level}' is not allowed for this creator. "
                              f"Allowed: {', '.join(info.get('privacy_level_options', []))}")
        size = path.stat().st_size
        chunk, count = chunk_plan(size)
        d = self._post("/post/publish/video/init/", {
            "post_info": {"title": title[:2200], "privacy_level": privacy_level, "disable_comment": disable_comment,
                          "disable_duet": disable_duet, "disable_stitch": disable_stitch},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count}})
        self._transfer(d["upload_url"], path, chunk, count, progress)
        return d["publish_id"]

    def status(self, publish_id: str) -> dict:
        return self._post("/post/publish/status/fetch/", {"publish_id": publish_id})
