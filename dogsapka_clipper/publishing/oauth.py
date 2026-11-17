"""OAuth 2.0 authorization-code flow with PKCE and a loopback redirect (RFC 8252) for desktop apps."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = ("<html><body style='font-family:sans-serif;background:#0b0b12;color:#ececf4;text-align:center;padding:60px'>"
        "<h2>{msg}</h2><p>You can close this window and return to DOGSAPKA AI CLIPPER.</p></body></html>")


class OAuthError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def loopback_authorize(build_url, port: int = 0, path: str = "/callback", timeout: float = 300,
                       open_browser=webbrowser.open) -> tuple[str, str, str]:
    """Run the browser step. ``build_url(redirect_uri, state)`` returns the authorization URL.

    Returns (code, redirect_uri, state).
    """
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            if u.path != path:
                self.send_response(404)
                self.end_headers()
                return
            q = dict(urllib.parse.parse_qsl(u.query))
            result.update(q)
            ok = "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.format(msg="Authorization complete" if ok else "Authorization failed").encode())
            done.set()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", port), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    state = secrets.token_urlsafe(16)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        open_browser(build_url(redirect, state))
        if not done.wait(timeout):
            raise OAuthError("Authorization timed out.")
    finally:
        srv.shutdown()
        srv.server_close()
    if result.get("state") != state:
        raise OAuthError("Authorization response had an invalid state (possible CSRF) - try again.")
    if "code" not in result:
        raise OAuthError(f"Authorization was denied: {result.get('error_description') or result.get('error', 'unknown')}")
    return result["code"], redirect, state
