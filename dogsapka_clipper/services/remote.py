"""Remote control API for the iPhone app (local network, token-authenticated JSON over HTTP).

Off by default (Settings → Remote control). Pairing: the desktop shows a QR code containing its address and a
one-time 6-digit code (valid 10 minutes, 5 attempts). The phone exchanges that code for a long random token;
only a SHA-256 hash of each token is stored, and devices can be revoked at any time.

Endpoints (all JSON, ``Authorization: Bearer <token>`` except /api/info and /api/pair)::

    GET  /api/info                         name, version, whether pairing is open
    POST /api/pair            {code, device}            -> {token}
    GET  /api/status                       sessions, queue, monitoring counts, disk
    GET  /api/streamers                    list with live stats
    POST /api/streamers/<id>/monitoring    {enabled}
    POST /api/monitoring/all               {enabled}
    GET  /api/clips?limit=50               newest clips with their exports
    GET  /api/clips/<id>/thumbnail         JPEG
    GET  /api/exports/<id>/video           MP4 (HTTP range requests, for streaming)
    GET  /api/accounts                     connected upload accounts
    POST /api/exports/<id>/upload          {account_ids:[..], confirm_rights:true}
    GET  /api/jobs                         processing queue
    POST /api/jobs/<id>/<pause|resume|cancel|retry>
    POST /api/queue/pause_all              {paused}
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from dogsapka_clipper import __version__
from dogsapka_clipper.db.models import Clip, ExportedVideo, ProcessingJob, Streamer

if TYPE_CHECKING:
    from dogsapka_clipper.services.backend import Backend

log = logging.getLogger(__name__)

DEVICES_KEY = "remote_devices"           # secure store: [{id, name, hash, created, last_seen}]
DEFAULT_PORT = 47820
PAIR_TTL_S = 600
PAIR_MAX_ATTEMPTS = 5
MAX_BODY = 64 * 1024


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def local_addresses() -> list[str]:
    """Best-effort list of this computer's LAN IPv4 addresses (for the pairing QR code)."""
    addrs: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))            # no packet is sent; just picks the outgoing interface
        addrs.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in addrs and not ip.startswith("127."):
                addrs.append(ip)
    except OSError:
        pass
    return addrs or ["127.0.0.1"]


@dataclass
class Pairing:
    code: str
    expires: float
    attempts: int = 0


class RemoteServer:
    def __init__(self, backend: "Backend", port: int = DEFAULT_PORT) -> None:
        self.b = backend
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._pairing: Pairing | None = None
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._srv is not None

    def start(self) -> None:
        if self._srv:
            return
        handler = _make_handler(self)
        self._srv = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, name="remote-api", daemon=True)
        self._thread.start()
        log.info("Remote control API listening on port %d", self.port)

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        self._pairing = None

    # -- pairing / devices ------------------------------------------------------------------
    def open_pairing(self) -> str:
        """New one-time code (shown with the QR code on the desktop)."""
        with self._lock:
            self._pairing = Pairing(f"{secrets.randbelow(1_000_000):06d}", time.time() + PAIR_TTL_S)
            return self._pairing.code

    def close_pairing(self) -> None:
        with self._lock:
            self._pairing = None

    def pairing_open(self) -> bool:
        p = self._pairing
        return bool(p and p.expires > time.time() and p.attempts < PAIR_MAX_ATTEMPTS)

    def pair(self, code: str, device: str) -> str | None:
        with self._lock:
            p = self._pairing
            if not p or p.expires < time.time() or p.attempts >= PAIR_MAX_ATTEMPTS:
                return None
            if not hmac.compare_digest(str(code).strip(), p.code):
                p.attempts += 1
                return None
            self._pairing = None                       # one-time
            token = secrets.token_urlsafe(32)
            devices = self.devices()
            devices.append({"id": secrets.token_hex(6), "name": (device or "iPhone")[:60], "hash": _hash(token),
                            "created": time.time(), "last_seen": time.time()})
            self.b.secrets.set_json(DEVICES_KEY, {"devices": devices})
        self.b.bus.emit("remote.paired", f"Remote control paired: {device or 'iPhone'}", "success")
        return token

    def devices(self) -> list[dict]:
        return list((self.b.secrets.get_json(DEVICES_KEY) or {}).get("devices", []))

    def revoke(self, device_id: str) -> None:
        with self._lock:
            self.b.secrets.set_json(DEVICES_KEY, {"devices": [d for d in self.devices() if d["id"] != device_id]})

    def authenticate(self, token: str) -> dict | None:
        if not token:
            return None
        h = _hash(token)
        for d in self.devices():
            if hmac.compare_digest(d["hash"], h):
                if time.time() - d.get("last_seen", 0) > 60:      # don't rewrite the store on every request
                    with self._lock:
                        devs = self.devices()
                        for x in devs:
                            if x["id"] == d["id"]:
                                x["last_seen"] = time.time()
                        self.b.secrets.set_json(DEVICES_KEY, {"devices": devs})
                return d
        return None

    # -- API data -----------------------------------------------------------------------------
    def status(self) -> dict:
        b = self.b
        on, total = b.monitoring_state()
        du = shutil.disk_usage(b.paths.root)
        return {"version": __version__, "live_sessions": len(b.sessions),
                "recording": sum(1 for s in b.sessions.values() if s.recorder.running),
                "monitored": on, "streamers": total, "queue_pending": b.jobs.pending_count(),
                "queue_paused": b.jobs.globally_paused, "disk_free_gb": round(du.free / 1e9, 1)}

    def streamers(self) -> list[dict]:
        out = []
        with self.b.db.session() as s:
            rows = s.scalars(select(Streamer).order_by(Streamer.is_live.desc(), Streamer.display_name)).all()
            for r in rows:
                sess = self.b.sessions.get(r.id)
                st = sess.stats if sess else None
                out.append({
                    "id": r.id, "name": r.display_name or r.login, "login": r.login, "platform": r.platform,
                    "live": bool(r.is_live or sess), "monitoring": r.monitoring_enabled,
                    "title": r.stream_title, "category": r.category, "viewers": r.viewer_count,
                    "simulation": r.login == "dogsapka_simulation",
                    "stats": None if st is None else {
                        "score": round(st.score, 1), "threshold": round(st.threshold, 1),
                        "chat_rate": round(st.chat_rate, 2), "audio": round(st.audio_level, 3),
                        "buffer_s": round(st.buffer_s), "buffer_target": round(st.buffer_target),
                        "recording": st.recording, "highlights": st.highlights,
                        "scores": [round(v, 1) for _, v in list(st.scores)[-60:]]}})
        return out

    def clips(self, limit: int = 50) -> list[dict]:
        out = []
        with self.b.db.session() as s:
            rows = s.scalars(select(Clip).order_by(Clip.created_at.desc()).limit(max(1, min(limit, 200)))).all()
            for c in rows:
                exports = sorted(c.exports, key=lambda e: e.created_at or 0, reverse=True)
                out.append({
                    "id": c.id, "title": c.title or c.reason or f"Clip {c.id}",
                    "streamer": (c.streamer.display_name or c.streamer.login) if c.streamer else "",
                    "score": c.detection_score, "quality": c.quality_score, "duration": round(c.duration, 1),
                    "created": c.created_at.isoformat() if c.created_at else "",
                    "status": c.export_status, "has_thumbnail": bool(c.thumbnail_path and Path(c.thumbnail_path).exists()),
                    "exports": [{"id": e.id, "profile": e.profile, "qc_passed": e.qc_passed,
                                 "uploaded_to": [v.get("account", k) for k, v in (e.published or {}).items()
                                                 if isinstance(v, dict)],
                                 "available": Path(e.path).exists()} for e in exports]})
        return out

    def accounts(self) -> list[dict]:
        return [{"id": a.id, "platform": a.platform, "name": a.name, "privacy": a.privacy,
                 "auto_upload": a.auto_upload} for a in self.b.accounts.list()]

    def jobs(self) -> list[dict]:
        names = {"process": "Automatic edit & export", "render": "Render (editor)", "transcribe": "AI transcription",
                 "upload": "Upload"}
        with self.b.db.session() as s:
            rows = s.scalars(select(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(100)).all()
            return [{"id": j.id, "kind": j.kind, "name": names.get(j.kind, j.kind), "status": j.status,
                     "progress": round(j.progress, 3), "stage": j.stage, "error": (j.error or "")[:300],
                     "clip": (j.clip.title or j.clip.reason) if j.clip else ""} for j in rows]

    def file_for(self, kind: str, obj_id: int) -> Path | None:
        with self.b.db.session() as s:
            if kind == "thumbnail":
                c = s.get(Clip, obj_id)
                p = c.thumbnail_path if c else ""
            else:
                e = s.get(ExportedVideo, obj_id)
                p = e.path if e else ""
        return Path(p) if p and Path(p).exists() else None


_ROUTE = re.compile(r"^/api/(?P<a>[a-z_]+)(?:/(?P<id>\d+))?(?:/(?P<b>[a-z_]+))?/?$")


def _make_handler(server: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DOGSAPKA-Remote"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):     # keep tokens / paths out of the console
            log.debug("remote %s %s", self.command, self.path.split("?")[0])

        # -- helpers ------------------------------------------------------------------------
        def _json(self, code: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        def _auth(self) -> bool:
            h = self.headers.get("Authorization", "")
            token = h[7:].strip() if h.startswith("Bearer ") else ""
            if server.authenticate(token):
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def _file(self, path: Path, ctype: str) -> None:
            size = path.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            partial = bool(m and (m.group(1) or m.group(2)))
            if partial:
                if m.group(1):
                    start = int(m.group(1))
                    end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
                else:                                           # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        # -- routing ---------------------------------------------------------------------------
        def do_GET(self):  # noqa: N802
            try:
                self._route("GET")
            except Exception:
                log.exception("remote GET failed")
                self._json(500, {"error": "internal error"})

        def do_POST(self):  # noqa: N802
            try:
                self._route("POST")
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception:
                log.exception("remote POST failed")
                self._json(500, {"error": "internal error"})

        def _route(self, method: str) -> None:
            path, _, query = self.path.partition("?")
            m = _ROUTE.match(path)
            if not m:
                return self._json(404, {"error": "not found"})
            a, oid, sub = m.group("a"), m.group("id"), m.group("b")
            oid = int(oid) if oid else None
            if method == "GET" and a == "info":
                return self._json(200, {"app": "DOGSAPKA AI CLIPPER", "version": __version__,
                                        "pairing": server.pairing_open()})
            if method == "POST" and a == "pair":
                data = self._body()
                token = server.pair(str(data.get("code", "")), str(data.get("device", "")))
                if not token:
                    return self._json(403, {"error": "invalid or expired pairing code"})
                return self._json(200, {"token": token, "version": __version__})
            if not self._auth():
                return
            b = server.b
            if method == "GET":
                if a == "status":
                    return self._json(200, server.status())
                if a == "streamers" and oid is None:
                    return self._json(200, server.streamers())
                if a == "clips" and oid is None:
                    lim = re.search(r"limit=(\d+)", query)
                    return self._json(200, server.clips(int(lim.group(1)) if lim else 50))
                if a == "clips" and sub == "thumbnail":
                    f = server.file_for("thumbnail", oid)
                    return self._file(f, "image/jpeg") if f else self._json(404, {"error": "no thumbnail"})
                if a == "exports" and sub == "video":
                    f = server.file_for("video", oid)
                    return self._file(f, "video/mp4") if f else self._json(404, {"error": "video file missing"})
                if a == "accounts":
                    return self._json(200, server.accounts())
                if a == "jobs" and oid is None:
                    return self._json(200, server.jobs())
                return self._json(404, {"error": "not found"})
            data = self._body()
            if a == "streamers" and sub == "monitoring":
                b.set_monitoring(oid, bool(data.get("enabled")))
                return self._json(200, {"ok": True})
            if a == "monitoring" and sub == "all" and oid is None:
                return self._json(200, {"changed": b.set_monitoring_all(bool(data.get("enabled")))})
            if a == "exports" and sub == "upload":
                if data.get("confirm_rights") is not True:
                    return self._json(400, {"error": "confirm_rights is required"})
                ids = [int(x) for x in data.get("account_ids") or []]
                if not ids:
                    return self._json(400, {"error": "no accounts selected"})
                jobs = [b.accounts.queue_upload(acc, oid) for acc in ids]
                return self._json(200, {"jobs": jobs})
            if a == "jobs" and sub in ("pause", "resume", "cancel", "retry"):
                getattr(b.jobs, sub)(oid)
                return self._json(200, {"ok": True})
            if a == "queue" and sub == "pause_all" and oid is None:
                b.jobs.pause_all(bool(data.get("paused")))
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})
    return Handler
