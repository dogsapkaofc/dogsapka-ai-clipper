"""Automated quality checks for rendered videos."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dogsapka_clipper.processing.ffmpeg_utils import FFmpegError, probe, run, tool_cmd


@dataclass
class QCReport:
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    black_ratio: float = 0.0
    max_volume_db: float | None = None
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def check_video(path: Path, expected_w: int | None = None, expected_h: int | None = None,
                expected_duration: float | None = None, expect_audio: bool = True) -> QCReport:
    rep = QCReport()
    if not path.exists() or path.stat().st_size < 1024:
        rep.passed = False
        rep.issues.append("Output file is missing or empty")
        return rep
    rep.size_bytes = path.stat().st_size
    try:
        info = probe(path)
    except FFmpegError as e:
        rep.passed = False
        rep.issues.append(f"Encoding error - file cannot be read: {e}")
        return rep
    rep.duration, rep.width, rep.height, rep.fps = info.duration, info.width, info.height, info.fps
    if not info.has_video:
        rep.issues.append("No video stream")
    if expected_w and expected_h and (info.width, info.height) != (expected_w, expected_h):
        rep.issues.append(f"Incorrect dimensions {info.width}x{info.height}, expected {expected_w}x{expected_h}")
    if expected_duration and abs(info.duration - expected_duration) > max(1.0, expected_duration * 0.05):
        rep.issues.append(f"Invalid duration {info.duration:.1f}s, expected about {expected_duration:.1f}s")
    if info.duration < 1.0:
        rep.issues.append("Video is shorter than 1 second")
    if expect_audio and not info.has_audio:
        rep.issues.append("Missing audio track")
    try:
        args = tool_cmd("ffmpeg") + ["-hide_banner", "-nostats", "-i", str(path),
                                     "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"]
        if info.has_audio:
            args = tool_cmd("ffmpeg") + ["-hide_banner", "-nostats", "-i", str(path),
                                         "-vf", "blackdetect=d=0.5:pix_th=0.10", "-af", "volumedetect", "-f", "null", "-"]
        res = run(args, timeout=600, check=False)
        err = res.stderr.decode("utf-8", "replace")
        if res.returncode != 0:
            rep.issues.append("Decoding the output failed (corrupted stream)")
        black = sum(float(m) for m in re.findall(r"black_duration:([\d.]+)", err))
        rep.black_ratio = black / info.duration if info.duration else 0.0
        if rep.black_ratio > 0.5:
            rep.issues.append(f"Mostly black video ({rep.black_ratio:.0%} black frames)")
        elif rep.black_ratio > 0.1:
            rep.warnings.append(f"Contains black frames ({rep.black_ratio:.0%})")
        m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", err)
        if m:
            rep.max_volume_db = -120.0 if m.group(1) == "-inf" else float(m.group(1))
            if rep.max_volume_db < -50:
                rep.issues.append("Audio track is silent")
            elif rep.max_volume_db > -0.1:
                rep.warnings.append("Audio may be clipping")
    except FFmpegError as e:
        rep.issues.append(f"Quality check could not decode file: {e}")
    rep.passed = not rep.issues
    return rep
