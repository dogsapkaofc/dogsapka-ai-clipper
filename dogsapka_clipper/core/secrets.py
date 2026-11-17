"""Secure storage for OAuth tokens and client secrets.

Order of preference:
1. ``keyring`` package (Windows Credential Manager / macOS Keychain / Secret Service).
2. Windows DPAPI (per-user encryption, via ctypes – no extra dependency).
3. A JSON file restricted to the current user (chmod 600) as a last resort.

Secret values are never logged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)
SERVICE = "DOGSAPKA_AI_CLIPPER"


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class SecretStore:
    def __init__(self, fallback_file: Path, use_keyring: bool = True) -> None:
        self._file = fallback_file
        self._lock = threading.Lock()
        self._keyring = None
        if use_keyring:
            try:
                import keyring  # type: ignore
                keyring.get_keyring()
                self._keyring = keyring
            except Exception:
                self._keyring = None

    @property
    def backend(self) -> str:
        if self._keyring:
            return "keyring"
        return "dpapi" if sys.platform == "win32" else "file"

    # -- file backend --------------------------------------------------
    def _load(self) -> dict[str, str]:
        if not self._file.exists():
            return {}
        raw = self._file.read_bytes()
        try:
            if sys.platform == "win32":
                raw = _dpapi(base64.b64decode(raw), encrypt=False)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            log.error("Secret store is unreadable; it will be reset.")
            return {}

    def _save(self, data: dict[str, str]) -> None:
        raw = json.dumps(data).encode("utf-8")
        if sys.platform == "win32":
            raw = base64.b64encode(_dpapi(raw, encrypt=True))
        tmp = self._file.with_suffix(".tmp")
        tmp.write_bytes(raw)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._file)

    # -- public API ----------------------------------------------------
    def get(self, key: str) -> str | None:
        with self._lock:
            if self._keyring:
                try:
                    return self._keyring.get_password(SERVICE, key)
                except Exception:
                    log.warning("keyring read failed, using file store")
            return self._load().get(key)

    def set(self, key: str, value: str | None) -> None:
        with self._lock:
            if self._keyring:
                try:
                    if value is None:
                        try:
                            self._keyring.delete_password(SERVICE, key)
                        except Exception:
                            pass
                    else:
                        self._keyring.set_password(SERVICE, key, value)
                    return
                except Exception:
                    log.warning("keyring write failed, using file store")
            data = self._load()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
            self._save(data)

    def get_json(self, key: str) -> dict | None:
        v = self.get(key)
        try:
            return json.loads(v) if v else None
        except ValueError:
            return None

    def set_json(self, key: str, value: dict | None) -> None:
        self.set(key, None if value is None else json.dumps(value))
