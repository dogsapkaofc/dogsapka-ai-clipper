"""Multiple publish accounts (YouTube channels / TikTok accounts) and automatic uploads.

Each account has its own OAuth token in the secure store (``token_key``). Automatic upload is strictly opt-in:
the global switch (Settings → Publishing) must be on *and* confirmed, the account must have auto-upload enabled,
and only exports that passed QC and are not duplicates are uploaded - once per account.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from sqlalchemy import select

from dogsapka_clipper.db.models import Clip, ExportedVideo, PublishAccount, Streamer

if TYPE_CHECKING:
    from dogsapka_clipper.processing.jobs import JobContext
    from dogsapka_clipper.services.backend import Backend

log = logging.getLogger(__name__)

YT_PRIVACY = ("private", "unlisted", "public")
TT_MODES = ("inbox", "SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")
SIMULATION_LOGIN = "dogsapka_simulation"


def published_key(acc_id: int) -> str:
    return f"account:{acc_id}"


class AccountManager:
    def __init__(self, backend: "Backend") -> None:
        self.b = backend
        self._migrate_legacy()
        backend.jobs.register("upload", self.handle_upload_job)
        backend.bus.subscribe(self._on_event)

    # -- accounts -------------------------------------------------------------------
    def list(self) -> list[PublishAccount]:
        with self.b.db.session() as s:
            return list(s.scalars(select(PublishAccount).order_by(PublishAccount.platform, PublishAccount.id)))

    def get(self, acc_id: int) -> PublishAccount | None:
        with self.b.db.session() as s:
            return s.get(PublishAccount, acc_id)

    def client(self, acc: PublishAccount):
        p = self.b.settings.publishing
        if acc.platform == "youtube":
            from dogsapka_clipper.publishing.youtube import YouTubeClient
            return YouTubeClient(p.youtube_client_id, self.b.secrets, token_key=acc.token_key)
        from dogsapka_clipper.publishing.tiktok import TikTokClient
        return TikTokClient(p.tiktok_client_key, self.b.secrets, token_key=acc.token_key)

    def add_youtube(self, open_browser=None) -> PublishAccount:
        """Browser sign-in for one more YouTube channel. Blocking - run off the UI thread."""
        from dogsapka_clipper.publishing.youtube import YouTubeClient
        key = f"youtube_token_{uuid.uuid4().hex[:12]}"
        yt = YouTubeClient(self.b.settings.publishing.youtube_client_id, self.b.secrets, token_key=key)
        yt.login(open_browser)
        try:
            name = yt.channel_title() or "YouTube channel"
        except Exception:
            name = "YouTube channel"
        return self._insert("youtube", name, key, self.b.settings.publishing.youtube_privacy)

    def add_tiktok(self, direct_post: bool = False, open_browser=None) -> PublishAccount:
        from dogsapka_clipper.publishing.tiktok import TikTokClient
        key = f"tiktok_token_{uuid.uuid4().hex[:12]}"
        tt = TikTokClient(self.b.settings.publishing.tiktok_client_key, self.b.secrets, token_key=key)
        tt.login(direct_post=direct_post, open_browser=open_browser)
        name = tt.display_name() or "TikTok account"
        return self._insert("tiktok", name, key, "SELF_ONLY" if direct_post else "inbox")

    def _insert(self, platform: str, name: str, key: str, privacy: str) -> PublishAccount:
        with self.b.db.session() as s:
            acc = PublishAccount(platform=platform, name=name, token_key=key, privacy=privacy)
            s.add(acc)
            s.flush()
        self.b.bus.emit("accounts.changed", f"{'YouTube' if platform == 'youtube' else 'TikTok'} account added: {name}",
                        "success")
        return acc

    def update(self, acc_id: int, *, auto_upload: bool | None = None, privacy: str | None = None,
               streamer_filter: list[int] | None = None) -> None:
        with self.b.db.session() as s:
            acc = s.get(PublishAccount, acc_id)
            if not acc:
                return
            if auto_upload is not None:
                acc.auto_upload = auto_upload
            if privacy is not None:
                allowed = YT_PRIVACY if acc.platform == "youtube" else TT_MODES
                if privacy in allowed:
                    acc.privacy = privacy
            if streamer_filter is not None:
                acc.streamer_filter = [int(x) for x in streamer_filter]
        self.b.bus.emit("accounts.changed", "", "info")

    def remove(self, acc_id: int) -> None:
        with self.b.db.session() as s:
            acc = s.get(PublishAccount, acc_id)
            if not acc:
                return
            self.b.secrets.set_json(acc.token_key, None)
            s.delete(acc)
        self.b.bus.emit("accounts.changed", "Account removed", "info")

    def youtube_read_token(self) -> str | None:
        """Bearer token of a connected YouTube account (scope youtube.readonly) - used for live chat
        when the API key alone is not accepted."""
        for acc in self.list():
            if acc.platform == "youtube":
                try:
                    return self.client(acc).token()
                except Exception:
                    continue
        return None

    def _migrate_legacy(self) -> None:
        """Earlier versions stored a single YouTube / TikTok token - turn them into accounts."""
        try:
            existing = {a.token_key for a in self.list()}
            for platform, key, privacy in (("youtube", "youtube_token", self.b.settings.publishing.youtube_privacy),
                                           ("tiktok", "tiktok_token", "inbox")):
                if key not in existing and (self.b.secrets.get_json(key) or {}).get("refresh_token"):
                    with self.b.db.session() as s:
                        s.add(PublishAccount(platform=platform, token_key=key, privacy=privacy,
                                             name="YouTube channel" if platform == "youtube" else "TikTok account"))
        except Exception:
            log.exception("Legacy account migration failed")

    # -- uploads --------------------------------------------------------------------
    def queue_upload(self, acc_id: int, export_id: int, title: str | None = None, description: str | None = None,
                     tags: list[str] | None = None) -> int:
        with self.b.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            clip_id = e.clip_id if e else None
        params = {"account_id": acc_id, "export_id": export_id}
        if title is not None:
            params.update(title=title, description=description or "", tags=tags or [])
        return self.b.jobs.submit("upload", clip_id, params)

    def upload_now(self, acc_id: int, export_id: int, title: str, description: str, tags: list[str],
                   progress: Callable[[float], None] | None = None, privacy: str | None = None) -> dict:
        """Upload one export to one account (blocking). Records the result on the export."""
        acc = self.get(acc_id)
        if acc is None:
            raise RuntimeError("This account was removed.")
        with self.b.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            if e is None:
                raise RuntimeError("The exported video no longer exists.")
            path = Path(e.path)
        if not path.exists():
            raise RuntimeError(f"Video file is missing: {path}")
        cl = self.client(acc)
        if not cl.is_connected():
            raise RuntimeError(f"{acc.name}: the account is no longer connected - remove it and add it again.")
        mode = privacy or acc.privacy
        if acc.platform == "youtube":
            vid = cl.upload(path, title, description, tags, mode, progress=progress)
            res = {"platform": "youtube", "account": acc.name, "id": vid, "privacy": mode}
        elif mode == "inbox":
            res = {"platform": "tiktok", "account": acc.name, "publish_id": cl.upload_to_inbox(path, progress),
                   "mode": "inbox"}
        else:
            pid = cl.direct_post(path, f"{title} {' '.join(tags)}".strip(), mode, progress=progress)
            res = {"platform": "tiktok", "account": acc.name, "publish_id": pid, "mode": "direct", "privacy": mode}
        with self.b.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            if e:
                e.published = {**(e.published or {}), published_key(acc_id): res}
        return res

    def handle_upload_job(self, ctx: "JobContext") -> None:
        acc_id, export_id = int(ctx.params["account_id"]), int(ctx.params["export_id"])
        acc = self.get(acc_id)
        name = acc.name if acc else f"account {acc_id}"
        with self.b.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            if e and published_key(acc_id) in (e.published or {}):
                ctx.progress(1.0, f"Already uploaded to {name}")
                return
            c = s.get(Clip, e.clip_id) if e and e.clip_id else None
            title = ctx.params.get("title") or (c.title if c else "") or (Path(e.path).stem if e else "")
            desc = ctx.params.get("description") if "title" in ctx.params else (c.description if c else "")
            tags = ctx.params.get("tags") if "title" in ctx.params else list(c.hashtags or []) if c else []
        ctx.progress(0.01, f"Uploading to {name}")
        res = self.upload_now(acc_id, export_id, title, desc or "", tags or [],
                              progress=lambda f: ctx.progress(min(0.99, f), f"Uploading to {name}"))
        where = "YouTube" if res["platform"] == "youtube" else "TikTok"
        detail = f"video id {res['id']}, {res['privacy']}" if where == "YouTube" else \
            ("sent to the TikTok inbox - finish posting in the app" if res.get("mode") == "inbox" else "post submitted")
        self.b.bus.emit("video.published", f"{where} ({name}): uploaded - {detail}", "success",
                        export_id=export_id, account_id=acc_id)
        ctx.progress(1.0, f"Uploaded to {name}")

    def auto_targets(self, export_id: int) -> list[int]:
        """Accounts that should automatically receive this export."""
        p = self.b.settings.publishing
        if not (p.auto_publish and p.auto_publish_confirmed):
            return []
        with self.b.db.session() as s:
            e = s.get(ExportedVideo, export_id)
            if e is None or not e.qc_passed:
                return []
            c = s.get(Clip, e.clip_id) if e.clip_id else None
            if c is None or c.duplicate_of:
                return []
            st = s.get(Streamer, c.streamer_id)
            if st is None or not st.authorized or st.login == SIMULATION_LOGIN:
                return []
            done = set((e.published or {}).keys())
            out = []
            for acc in s.scalars(select(PublishAccount).where(PublishAccount.auto_upload.is_(True))):
                if acc.streamer_filter and st.id not in acc.streamer_filter:
                    continue
                if published_key(acc.id) in done:
                    continue
                out.append(acc.id)
            return out

    def _on_event(self, ev) -> None:
        if ev.kind != "video.exported" or not ev.data.get("export_id") or ev.data.get("duplicate"):
            return
        try:
            for acc_id in self.auto_targets(int(ev.data["export_id"])):
                self.queue_upload(acc_id, int(ev.data["export_id"]))
        except Exception:
            log.exception("Auto-upload scheduling failed")
