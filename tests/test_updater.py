"""Auto-update: version comparison, release parsing, asset choice, verified download."""
import hashlib
import sys

import httpx
import pytest

from dogsapka_clipper.core import updater as U


def test_version_compare():
    assert U.is_newer("0.2.0", "0.1.0")
    assert U.is_newer("v1.0.0", "0.9.9")
    assert U.is_newer("0.10.0", "0.9.0")          # numeric, not string comparison
    assert not U.is_newer("0.1.0", "0.1.0")
    assert not U.is_newer("0.1.0", "0.2.0")


def release(tag="v0.2.0", **kw):
    return {"tag_name": tag, "body": "## News\n- stuff", "html_url": "https://github.com/x/y/releases/tag/" + tag,
            "assets": [
                {"name": f"DOGSAPKA_AI_CLIPPER_Setup_{tag[1:]}.exe", "browser_download_url": "https://dl/win.exe",
                 "size": 10, "digest": "sha256:" + "a" * 64, "state": "uploaded"},
                {"name": f"DOGSAPKA_AI_CLIPPER_{tag[1:]}_macOS_arm64.dmg", "browser_download_url": "https://dl/mac.dmg",
                 "size": 20, "state": "uploaded"}], **kw}


def client(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_newer_release_found_with_platform_asset(monkeypatch):
    monkeypatch.setattr(U.sys, "platform", "win32")
    info = U.check_for_update("0.1.0", http=client(release()))
    assert info.version == "0.2.0" and info.asset_url == "https://dl/win.exe" and info.sha256 == "a" * 64
    monkeypatch.setattr(U.sys, "platform", "darwin")
    monkeypatch.setattr(U.platform, "machine", lambda: "arm64")
    info = U.check_for_update("0.1.0", http=client(release()))
    assert info.asset_url == "https://dl/mac.dmg" and info.sha256 == ""


def test_no_update_when_current_skipped_or_prerelease():
    assert U.check_for_update("0.2.0", http=client(release())) is None
    assert U.check_for_update("0.1.0", http=client(release()), skip_version="0.2.0") is None
    assert U.check_for_update("0.1.0", http=client(release(prerelease=True))) is None
    assert U.check_for_update("0.1.0", http=client({}, status=404)) is None       # no releases yet


def test_rate_limit_or_network_error_is_reported():
    with pytest.raises(U.UpdateError):
        U.check_for_update("0.1.0", http=client({"message": "rate limit"}, status=403))


def test_release_without_installer_for_this_platform(monkeypatch):
    monkeypatch.setattr(U.sys, "platform", "linux")
    info = U.check_for_update("0.1.0", http=client(release()))
    assert info.version == "0.2.0" and not info.installable


def test_download_verifies_checksum(tmp_path):
    data = b"installer-bytes"
    good = hashlib.sha256(data).hexdigest()
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data)))
    info = U.UpdateInfo("0.2.0", "", "", "Setup.exe", "https://dl/x", len(data), good)
    seen = []
    path = U.download(info, seen.append, http=http, dest_dir=tmp_path)
    assert path.read_bytes() == data and seen[-1] == 1.0
    bad = U.UpdateInfo("0.2.0", "", "", "Setup2.exe", "https://dl/x", len(data), "0" * 64)
    with pytest.raises(U.UpdateError):
        U.download(bad, http=http, dest_dir=tmp_path)
    assert not (tmp_path / "Setup2.exe").exists()          # damaged file is removed


def test_source_checkout_updates_with_git():
    assert U.source_checkout() is not None          # tests run from the git clone
    assert U.can_self_install(U.UpdateInfo("9.9.9", "", ""))   # even without installer: git pull
