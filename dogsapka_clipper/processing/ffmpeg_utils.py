"""Locating and running FFmpeg / FFprobe / Streamlink safely."""
from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr[-4000:]


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    """Find a binary: env override, bundled ``tools/`` folder, then PATH."""
    env = os.environ.get(f"DOGSAPKA_{name.upper()}")
    if env and Path(env).exists():
        return env
    exe = name + (".exe" if sys.platform == "win32" else "")
    for cand in (_app_dir() / "tools" / exe, _app_dir() / "tools" / name / "bin" / exe):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    if name == "streamlink" and not getattr(sys, "frozen", False):
        # streamlink may be installed as a Python package without a launcher on PATH
        try:
            import streamlink  # noqa: F401
            return f"{sys.executable} -m streamlink"
        except ImportError:
            return None
    return None


def tool_cmd(name: str) -> list[str]:
    path = find_tool(name)
    if not path:
        raise FFmpegError(f"{name} was not found. Install it or place it in the 'tools' folder next to the app.")
    suffix = " -m streamlink"
    if path.endswith(suffix):   # python executable path may itself contain spaces
        return [path[: -len(suffix)], "-m", "streamlink"]
    return [path]


def popen_kwargs() -> dict:
    kw: dict = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw


def run(args: list[str], timeout: float = 600, check: bool = True,
        cancel: threading.Event | None = None) -> subprocess.CompletedProcess:
    """Run a command with timeout and optional cancellation; raise FFmpegError on failure."""
    log.debug("run: %s", " ".join(map(str, args)))
    proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, **popen_kwargs())
    out = err = b""
    try:
        if cancel is None:
            out, err = proc.communicate(timeout=timeout)
        else:
            import time
            deadline = time.monotonic() + timeout
            while True:
                try:
                    out, err = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if cancel.is_set():
                        proc.kill()
                        proc.communicate()
                        raise FFmpegError("Cancelled")
                    if time.monotonic() > deadline:
                        raise
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise FFmpegError(f"{Path(args[0]).name} timed out after {timeout:.0f}s")
    res = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if check and proc.returncode != 0:
        msg = err.decode("utf-8", "replace")
        raise FFmpegError(f"{Path(str(args[0])).name} failed (exit {proc.returncode}): {_last_error_line(msg)}", msg)
    return res


def _last_error_line(stderr: str) -> str:
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    for l in reversed(lines):
        if any(w in l.lower() for w in ("error", "invalid", "no such", "failed", "unable")):
            return l.strip()[:300]
    return lines[-1].strip()[:300] if lines else "unknown error"


@dataclass
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    raw: dict = field(default_factory=dict)


def probe(path: Path | str, timeout: float = 60) -> MediaInfo:
    res = run(tool_cmd("ffprobe") + ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
              timeout=timeout)
    data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    info = MediaInfo(raw=data)
    try:
        info.duration = float(data.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info.has_video:
            info.has_video = True
            info.width, info.height = int(s.get("width", 0)), int(s.get("height", 0))
            info.video_codec = s.get("codec_name", "")
            info.fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
            if not info.duration:
                info.duration = float(s.get("duration", 0) or 0)
        elif s.get("codec_type") == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = s.get("codec_name", "")
    return info


def _parse_rate(r: str | None) -> float:
    if not r or r in ("0/0",):
        return 0.0
    try:
        a, _, b = r.partition("/")
        return float(a) / float(b or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


@functools.lru_cache(maxsize=None)
def nvenc_available() -> bool:
    """True only if h264_nvenc can actually encode on this machine (not just compiled in)."""
    if not find_tool("ffmpeg"):
        return False
    try:
        run(tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                  "color=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
        return True
    except FFmpegError:
        return False


def choose_encoder(preference: str) -> str:
    if preference == "h264_nvenc":
        return "h264_nvenc" if nvenc_available() else "libx264"
    if preference == "auto":
        return "h264_nvenc" if nvenc_available() else "libx264"
    return "libx264"


def video_encoder_args(encoder: str, crf: int, bitrate_k: int = 0, fps: int = 30) -> list[str]:
    gop = str(fps * 2)
    if encoder == "h264_nvenc":
        args = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-g", gop]
        args += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.5)}k"] if bitrate_k else ["-cq", str(crf), "-b:v", "0"]
    else:
        args = ["-c:v", "libx264", "-preset", "medium", "-g", gop]
        args += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{int(bitrate_k * 1.5)}k", "-bufsize", f"{bitrate_k * 2}k"] \
            if bitrate_k else ["-crf", str(crf)]
    return args + ["-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart"]


def extract_frame(src: Path | str, t: float, dst: Path, width: int = 480) -> Path:
    """Extract one frame at ``t`` seconds from the start of ``src``.

    MPEG-TS recording segments keep their original stream timestamps, so input seeking would be
    relative to the wrong origin; for those, seek on the (zero-based) output timeline instead.
    """
    seek = ["-ss", f"{max(0.0, t):.3f}"]
    inp = ["-i", str(src)]
    args = (inp + seek) if str(src).lower().endswith(".ts") else (seek + inp)
    Path(dst).unlink(missing_ok=True)
    run(tool_cmd("ffmpeg") + ["-y", "-hide_banner", "-loglevel", "error"] + args +
        ["-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dst)], timeout=60)
    if not Path(dst).exists():
        raise FFmpegError(f"No frame could be extracted at {t:.1f}s from {Path(src).name}")
    return dst


def decode_audio_pcm(src: Path | str, sample_rate: int = 16000, start: float | None = None,
                     duration: float | None = None, timeout: float = 120):
    """Decode audio to mono float32 numpy array."""
    import numpy as np
    args = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"]
    res = run(args, timeout=timeout, check=False)
    if res.returncode != 0 and not res.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def decode_gray_frames(src: Path | str, fps: float = 2.0, size: tuple[int, int] = (160, 90), timeout: float = 120):
    """Decode low-res grayscale frames for visual analysis. Returns array (n, h, w) uint8."""
    import numpy as np
    w, h = size
    res = run(tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-i", str(src), "-an",
                                    "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
              timeout=timeout, check=False)
    buf = np.frombuffer(res.stdout, dtype=np.uint8)
    n = buf.size // (w * h)
    return buf[: n * w * h].reshape(n, h, w)
