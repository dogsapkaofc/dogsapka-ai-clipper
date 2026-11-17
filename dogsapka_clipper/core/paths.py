"""Filesystem layout for DOGSAPKA AI CLIPPER.

All application data lives under a single root folder::

    DOGSAPKA_AI_CLIPPER/
        recordings/  clips/  exports/  thumbnails/  subtitles/
        cache/  logs/  models/  database/  temp/
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SUBDIRS = (
    "recordings", "clips", "exports", "thumbnails", "subtitles",
    "cache", "logs", "models", "database", "temp",
)


def default_root() -> Path:
    env = os.environ.get("DOGSAPKA_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "DOGSAPKA_AI_CLIPPER"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def create(cls, root: Path | None = None) -> "AppPaths":
        p = cls(Path(root) if root else default_root())
        p.ensure()
        return p

    def ensure(self) -> None:
        for name in SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def __getattr__(self, item: str) -> Path:  # recordings, clips, ...
        if item in SUBDIRS:
            return self.root / item
        raise AttributeError(item)

    @property
    def database_file(self) -> Path:
        return self.root / "database" / "dogsapka.sqlite3"

    @property
    def log_file(self) -> Path:
        return self.root / "logs" / "dogsapka.log"


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, max_len: int = 80, default: str = "file") -> str:
    """Return a filename that is valid on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFKC", name or "")
    name = _INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if not name:
        name = default
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name[:max_len].rstrip(" .") or default


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1
