"""YouTube upload via the official YouTube Data API v3 (resumable upload).

Uploading a vertical video of <= 60 s (180 s for newer uploads) makes it *eligible* to appear as a Short;
YouTube decides how it is surfaced - nothing here guarantees recommendation.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.publishing.oauth import OAuthError, loopback_authorize, pkce_pair

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
CHUNK = 8 * 1024 * 1024


class YouTubeError(RuntimeError):
    upload_uncertain = False


class YouTubeUploadUncertainError(YouTubeError):
    """The server may have completed an upload whose response was lost."""

    upload_uncertain = True


class YouTubeClient:
    TOKEN_KEY = "youtube_token"
    SECRET_KEY = "youtube_client_secret"

    def __init__(self, client_id: str, store: SecretStore, http: httpx.Client | None = None,
                 token_key: str | None = None) -> None:
        self.client_id = client_id
        if token_key:
            self.TOKEN_KEY = token_key      # one token per connected account
        self.store = store
        self.http = http or httpx.Client(timeout=60)

    def is_connected(self) -> bool:
        return bool((self.store.get_json(self.TOKEN_KEY) or {}).get("refresh_token"))

    def logout(self) -> None:
        self.store.set_json(self.TOKEN_KEY, None)

    def login(self, open_browser=None) -> None:
        if not self.client_id:
            raise YouTubeError("Enter a Google OAuth client ID (type: Desktop app) in Settings → Publishing.")
        verifier, challenge = pkce_pair()

        def url(redirect, state):
            return AUTH_URL + "?" + urllib.parse.urlencode({
                "client_id": self.client_id, "redirect_uri": redirect, "response_type": "code",
                "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent select_account"})
        kw = {"open_browser": open_browser} if open_browser else {}
        code, redirect, _ = loopback_authorize(url, **kw)
        data = {"client_id": self.client_id, "code": code, "code_verifier": verifier, "redirect_uri": redirect,
                "grant_type": "authorization_code"}
        if self.store.get(self.SECRET_KEY):
            data["client_secret"] = self.store.get(self.SECRET_KEY)
        r = self.http.post(TOKEN_URL, data=data)
        if r.status_code != 200:
            raise OAuthError(f"Google token exchange failed: {r.text[:200]}")
        self._save(r.json())

    def _save(self, body: dict, old: dict | None = None) -> None:
        self.store.set_json(self.TOKEN_KEY, {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token") or (old or {}).get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in", 3600))})

    def token(self) -> str:
        rec = self.store.get_json(self.TOKEN_KEY)
        if not rec:
            raise YouTubeError("YouTube account is not connected.")
        if rec["expires_at"] - 60 > time.time():
            return rec["access_token"]
        data = {"client_id": self.client_id, "refresh_token": rec["refresh_token"], "grant_type": "refresh_token"}
        if self.store.get(self.SECRET_KEY):
            data["client_secret"] = self.store.get(self.SECRET_KEY)
        r = self.http.post(TOKEN_URL, data=data)
        if r.status_code != 200:
            self.logout()
            raise YouTubeError("YouTube authorization expired - please connect your account again.")
        self._save(r.json(), rec)
        return self.store.get_json(self.TOKEN_KEY)["access_token"]

    def channel_title(self) -> str:
        r = self.http.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {self.token()}"})
        items = r.json().get("items", []) if r.status_code == 200 else []
        return items[0]["snippet"]["title"] if items else ""

    def channel_id(self) -> str:
        r = self.http.get(CHANNELS_URL, params={"part": "id", "mine": "true"},
                          headers={"Authorization": f"Bearer {self.token()}"})
        if r.status_code != 200:
            raise YouTubeError(f"Could not verify the YouTube channel: {_err(r)}")
        items = r.json().get("items", [])
        if len(items) != 1 or not items[0].get("id"):
            raise YouTubeError("Choose one YouTube channel when signing in.")
        return items[0]["id"]

    def schedule(self, video_id: str, publish_at: datetime | str) -> str:
        """Schedule an uploaded, never-published private video; return its UTC publication time."""
        scheduled = _normalize_publish_at(publish_at)
        if not isinstance(video_id, str) or not video_id.strip():
            raise YouTubeError("A video ID is required to schedule publication.")
        r = self.http.put(VIDEOS_URL, params={"part": "status"},
                          headers={"Authorization": f"Bearer {self.token()}"},
                          json={"id": video_id, "status": {"privacyStatus": "private", "publishAt": scheduled,
                                                          "selfDeclaredMadeForKids": False}})
        if r.status_code != 200:
            if r.status_code == 403 and ("insufficientPermissions" in r.text
                                        or "insufficient authentication scopes" in r.text.lower()
                                        or "insufficient_scope" in r.headers.get("WWW-Authenticate", "")):
                raise YouTubeError("YouTube scheduling needs additional permission. Reconnect your YouTube account "
                                   "in Settings → Publishing, then retry. The uploaded video remains private.")
            raise YouTubeError(f"YouTube rejected scheduled publication: {_err(r)}")
        return scheduled

    def upload(self, path: Path, title: str, description: str, tags: list[str], privacy: str = "private",
               progress: Callable[[float], None] | None = None, category_id: str = "20",
               publish_at: datetime | str | None = None) -> str:
        """Resumable upload; returns the video id. ``category_id`` 20 = Gaming.

        ``publish_at`` schedules publication on YouTube and forces private upload visibility.
        For long uploads, upload privately first and call :meth:`schedule` after saving the returned ID.
        """
        scheduled = _normalize_publish_at(publish_at) if publish_at is not None else None
        size = path.stat().st_size
        meta = {"snippet": {"title": title[:100], "description": description[:5000],
                            "tags": [t.lstrip("#") for t in tags][:30], "categoryId": category_id},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
        if scheduled is not None:
            meta["status"].update(privacyStatus="private", publishAt=scheduled)
        r = self.http.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                           headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
                                    "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                           content=json.dumps(meta))
        if r.status_code != 200 or "Location" not in r.headers:
            raise YouTubeError(f"YouTube rejected the upload: {_err(r)}")
        session = r.headers["Location"]
        sent = 0
        retries = 0
        with open(path, "rb") as f:
            while sent < size:
                f.seek(sent)
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                try:
                    r = self.http.put(session, content=chunk, headers={
                        "Authorization": f"Bearer {self.token()}", "Content-Range": f"bytes {sent}-{end}/{size}"})
                except httpx.HTTPError:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    if progress:
                        progress(1.0)
                    return r.json()["id"]
                if r is not None and r.status_code == 308:
                    rng = r.headers.get("Range")
                    sent = int(rng.split("-")[1]) + 1 if rng else 0
                    retries = 0
                elif r is None or r.status_code >= 500:
                    # Do not send bytes again until YouTube confirms how much it received.
                    # A lost final response can already represent a completed upload.
                    while True:
                        retries += 1
                        if retries > 6:
                            raise YouTubeUploadUncertainError(
                                "Could not confirm whether YouTube completed the upload. Check YouTube Studio "
                                "before retrying to avoid uploading the same video twice.")
                        time.sleep(min(60, 2 ** retries))
                        try:
                            q = self.http.put(session, headers={"Authorization": f"Bearer {self.token()}",
                                                                 "Content-Range": f"bytes */{size}"})
                        except httpx.HTTPError:
                            continue
                        if q.status_code in (200, 201):
                            if progress:
                                progress(1.0)
                            return q.json()["id"]
                        if q.status_code == 308:
                            rng = q.headers.get("Range")
                            sent = int(rng.split("-")[1]) + 1 if rng else 0
                            break
                        if q.status_code >= 500:
                            continue
                        raise YouTubeUploadUncertainError(
                            f"Could not confirm the YouTube upload: {_err(q)}. Check YouTube Studio before retrying.")
                else:
                    raise YouTubeError(f"Upload failed: {_err(r)}")
                if progress:
                    progress(sent / size)
        raise YouTubeUploadUncertainError("YouTube did not confirm upload completion. Check YouTube Studio before retrying.")


def _normalize_publish_at(value: datetime | str) -> str:
    """Reject ambiguous/local or elapsed times before any API request can publish a video."""
    try:
        if isinstance(value, str):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
                raise ValueError
            value = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        utc = value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise YouTubeError("Publication time must be a timezone-aware datetime or RFC 3339 timestamp.") from exc
    if utc <= datetime.now(timezone.utc):
        raise YouTubeError("Publication time must be in the future.")
    return utc.isoformat().replace("+00:00", "Z")


def _err(r: httpx.Response) -> str:
    try:
        e = r.json().get("error", {})
        return f"{r.status_code} {e.get('message', '')}"
    except ValueError:
        return f"{r.status_code} {r.text[:200]}"
