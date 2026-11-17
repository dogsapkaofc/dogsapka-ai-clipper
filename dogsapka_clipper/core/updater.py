"""Automatic updates from the project's GitHub releases.

At startup the app asks GitHub for the latest release. If it is newer than the running version and has an
installer for this platform, the user is offered to download and install it:

* Windows: the Inno Setup installer is downloaded, verified (SHA-256 digest published by GitHub) and run
  silently; it keeps the previous program and data folders and starts the new version when done.
* macOS: the .dmg is downloaded and verified; after the app quits, a small script replaces the app in place
  and starts it again. If the app folder is not writable, the disk image is opened for a manual drag & drop.
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from dogsapka_clipper import __version__

log = logging.getLogger(__name__)

REPO = "guralynoel/dogsapka-ai-clipper"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""            # from GitHub's asset digest ("sha256:…"), empty if not published

    @property
    def installable(self) -> bool:
        return bool(self.asset_url)


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def platform_asset_suffix() -> str | None:
    if sys.platform == "win32":
        return "Setup_{v}.exe"
    if sys.platform == "darwin":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
        return "macOS_" + arch + ".dmg"
    return None


def pick_asset(assets: list[dict], version: str) -> dict | None:
    suffix = platform_asset_suffix()
    if not suffix:
        return None
    want = suffix.format(v=version)
    for a in assets:
        if a.get("name", "").endswith(want) and a.get("state", "uploaded") == "uploaded":
            return a
    return None


def check_for_update(current: str = __version__, http: httpx.Client | None = None,
                     skip_version: str = "") -> UpdateInfo | None:
    """Return info about a newer release, or None. Network problems raise UpdateError."""
    client = http or httpx.Client(timeout=15, follow_redirects=True)
    try:
        r = client.get(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                            "User-Agent": "DOGSAPKA-AI-CLIPPER-updater"})
    except httpx.HTTPError as e:
        raise UpdateError(f"Could not check for updates: {e}") from e
    finally:
        if http is None:
            client.close()
    if r.status_code == 404:
        return None                      # no releases yet
    if r.status_code != 200:
        raise UpdateError(f"Update check failed (HTTP {r.status_code}).")
    rel = r.json()
    if rel.get("draft") or rel.get("prerelease"):
        return None
    version = (rel.get("tag_name") or "").lstrip("vV")
    if not version or not is_newer(version, current) or version == skip_version:
        return None
    info = UpdateInfo(version, rel.get("body") or "", rel.get("html_url") or RELEASES_PAGE)
    asset = pick_asset(rel.get("assets") or [], version)
    if asset:
        digest = asset.get("digest") or ""
        info.asset_name = asset["name"]
        info.asset_url = asset["browser_download_url"]
        info.asset_size = int(asset.get("size") or 0)
        info.sha256 = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
    return info


def download(info: UpdateInfo, progress: Callable[[float], None] | None = None,
             http: httpx.Client | None = None, dest_dir: Path | None = None) -> Path:
    """Download the installer to a temp folder and verify its checksum."""
    if not info.installable:
        raise UpdateError("This release has no installer for this computer.")
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix="dogsapka_update_"))
    dest = dest_dir / info.asset_name
    client = http or httpx.Client(timeout=60, follow_redirects=True)
    h = hashlib.sha256()
    try:
        with client.stream("GET", info.asset_url, headers={"User-Agent": "DOGSAPKA-AI-CLIPPER-updater"}) as r:
            if r.status_code != 200:
                raise UpdateError(f"Download failed (HTTP {r.status_code}).")
            total = int(r.headers.get("Content-Length") or info.asset_size or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(1.0, done / total))
    except httpx.HTTPError as e:
        raise UpdateError(f"Download failed: {e}") from e
    finally:
        if http is None:
            client.close()
    if info.asset_size and dest.stat().st_size != info.asset_size:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download is incomplete - please try again.")
    if info.sha256 and h.hexdigest() != info.sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("The downloaded file is damaged (checksum mismatch) - please try again.")
    return dest


def can_self_install() -> bool:
    """Only the packaged app updates itself; a source checkout is updated with git."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in ("win32", "darwin")


def start_install(installer: Path) -> str:
    """Launch the installation. The caller must quit the app right afterwards.

    Returns "restart" (the new version starts automatically) or "manual" (the user finishes it).
    """
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(installer), "/SILENT", "/SP-", "/NOCANCEL", "/CLOSEAPPLICATIONS"], creationflags=flags, close_fds=True)
        return "restart"
    if sys.platform == "darwin":
        app = _mac_app_bundle()
        if app and os.access(app.parent, os.W_OK):
            script = installer.with_suffix(".sh")
            script.write_text(_MAC_SCRIPT, encoding="utf-8")
            subprocess.Popen(["/bin/bash", str(script), str(os.getpid()), str(installer), str(app)],
                             start_new_session=True, close_fds=True)
            return "restart"
        subprocess.Popen(["open", str(installer)])
        return "manual"
    raise UpdateError("Automatic installation is not supported on this system.")


def _mac_app_bundle() -> Path | None:
    exe = Path(sys.executable).resolve()
    for p in exe.parents:
        if p.suffix == ".app":
            return p
    return None


# waits for the app to quit, swaps the .app bundle from the mounted disk image, restarts it
_MAC_SCRIPT = r"""#!/bin/bash
PID="$1"; DMG="$2"; APP="$3"
for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
MNT=$(mktemp -d /tmp/dogsapka_mnt.XXXX)
hdiutil attach -nobrowse -quiet -mountpoint "$MNT" "$DMG" || { open "$DMG"; exit 1; }
SRC=$(find "$MNT" -maxdepth 1 -name "*.app" | head -1)
if [ -n "$SRC" ]; then
  rm -rf "$APP.old" && mv "$APP" "$APP.old" && ditto "$SRC" "$APP" && rm -rf "$APP.old" || {
    [ -d "$APP.old" ] && [ ! -d "$APP" ] && mv "$APP.old" "$APP"; }
  xattr -cr "$APP" 2>/dev/null
fi
hdiutil detach -quiet "$MNT"
rm -f "$DMG"
open "$APP"
"""
