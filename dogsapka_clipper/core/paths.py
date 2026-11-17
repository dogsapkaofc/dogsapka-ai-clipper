"""Filesystem layout for DOGSAPKA AI CLIPPER.

All application data lives under a single root folder::

    DOGSAPKA_AI_CLIPPER/
        recordings/  clips/  exports/  thumbnails/  subtitles/
        cache/  logs/  models/  database/  temp/
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SUBDIRS = (
    "recordings", "clips", "exports", "thumbnails", "subtitles",
    "cache", "logs", "models", "database", "temp",
)


FOLDER_NAME = "DOGSAPKA_AI_CLIPPER"


def platform_default_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Movies"            # clips are videos; also visible in Finder
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / FOLDER_NAME


def config_dir() -> Path:
    """Small fixed per-user folder that only holds the pointer to the (movable) data folder."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / FOLDER_NAME


def location_file() -> Path:
    return config_dir() / "data_location.json"


def _install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def _read_location() -> dict:
    try:
        d = json.loads(location_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def default_root() -> Path:
    """Data folder: DOGSAPKA_DATA_DIR > folder chosen in the app > folder chosen in the installer > default."""
    env = os.environ.get("DOGSAPKA_DATA_DIR")
    if env:
        return Path(env).expanduser()
    chosen = _read_location().get("path")
    if chosen:
        return Path(chosen)
    try:   # written by the Windows installer ("Where should clips and recordings be stored?")
        p = (_install_dir() / "data_location.txt").read_text(encoding="utf-8-sig").strip()
        if p:
            return Path(p)
    except OSError:
        pass
    return platform_default_root()


def normalize_data_folder(folder: Path) -> Path:
    """Choosing "D:\\" or "D:\\Videos" puts the data into a DOGSAPKA_AI_CLIPPER subfolder there."""
    folder = Path(folder)
    return folder if folder.name == FOLDER_NAME else folder / FOLDER_NAME


def set_data_location(new_root: Path, move_from: Path | None = None) -> None:
    """Remember a new data folder (used from the next start). ``move_from``: move existing data there."""
    d = {"path": str(new_root)}
    if move_from is not None:
        d["move_from"] = str(move_from)
    location_file().parent.mkdir(parents=True, exist_ok=True)
    location_file().write_text(json.dumps(d, indent=2), encoding="utf-8")


def folder_size(root: Path) -> int:
    total = 0
    for p in Path(root).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def apply_pending_move(log=None) -> str | None:
    """Run at startup, before the database opens: move the data folder if the user asked for it.

    Moves every subfolder into the new root and rewrites the absolute file paths stored in the database.
    Returns an error message if the move could not be completed (data then stays where it was).
    """
    loc = _read_location()
    src, dst = loc.get("move_from"), loc.get("path")
    if not src or not dst:
        return None
    src_p, dst_p = Path(src), Path(dst)
    try:
        if src_p.resolve() == dst_p.resolve() or not src_p.exists():
            set_data_location(dst_p)
            return None
        dst_p.mkdir(parents=True, exist_ok=True)
        for item in list(src_p.iterdir()):
            target = dst_p / item.name
            if target.exists() and item.is_dir():
                for sub in list(item.rglob("*")):
                    rel = sub.relative_to(item)
                    if sub.is_file() and not (target / rel).exists():
                        (target / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(sub), str(target / rel))
                shutil.rmtree(item, ignore_errors=True)
            elif not target.exists():
                shutil.move(str(item), str(target))
        _rewrite_db_paths(dst_p / "database" / "dogsapka.sqlite3", src_p, dst_p)
        set_data_location(dst_p)
        try:
            src_p.rmdir()
        except OSError:
            pass
        if log:
            log.info("Data folder moved from %s to %s", src_p, dst_p)
        return None
    except Exception as e:   # keep using the old folder; the user can retry
        set_data_location(src_p)
        if log:
            log.exception("Moving the data folder failed")
        return f"Moving the data folder to {dst_p} failed: {e}. The previous folder {src_p} is still used."


def _rewrite_db_paths(db_file: Path, old: Path, new: Path) -> None:
    import sqlite3
    if not db_file.exists():
        return
    o, n = str(old), str(new)
    cols = {"clips": ("source_path", "thumbnail_path"), "exported_videos": ("path", "srt_path", "ass_path"),
            "recording_segments": ("path",), "streamers": ("profile_image_path",)}
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in cols.items():
            if table not in tables:
                continue
            for c in columns:
                # replace only the leading folder (substr keeps the rest of the path unchanged)
                con.execute(f"UPDATE {table} SET {c} = ? || substr({c}, ?) WHERE substr({c}, 1, ?) = ?",
                            (n, len(o) + 1, len(o), o))
        con.commit()
    finally:
        con.close()


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
