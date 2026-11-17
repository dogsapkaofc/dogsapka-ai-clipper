"""Builds a clip file from buffered segments with accurate boundaries."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from dogsapka_clipper.processing.ffmpeg_utils import FFmpegError, probe, run, tool_cmd
from dogsapka_clipper.recording.buffer import Segment

log = logging.getLogger(__name__)


@dataclass
class ClipResult:
    path: Path
    start: float        # wall-clock start actually achieved
    duration: float
    reencoded: bool


def plan_window(event_ts: float, pre: float, post: float, min_len: float, max_len: float,
                peak_start: float | None = None, peak_end: float | None = None) -> tuple[float, float]:
    """Compute a clip window that always starts *before* the event and respects limits.

    ``peak_start``/``peak_end`` describe a merged event span; the window covers the whole span
    plus context, then is trimmed to ``max_len`` keeping the event near 1/2-2/3 of the clip.
    """
    s0 = min(event_ts, peak_start if peak_start is not None else event_ts)
    e0 = max(event_ts, peak_end if peak_end is not None else event_ts)
    start, end = s0 - pre, e0 + post
    length = end - start
    if length < min_len:
        extra = min_len - length
        start -= extra * 0.6
        end += extra * 0.4
    elif length > max_len:
        # keep context before the event; never start after the event
        start = max(start, event_ts - max_len * 0.6)
        end = start + max_len
        if end < event_ts + 3:
            end = event_ts + 3
            start = end - max_len
    return start, end


def validate_segments(segments: list[Segment]) -> list[Segment]:
    ok: list[Segment] = []
    for s in segments:
        try:
            if not s.path.exists() or s.path.stat().st_size < 1024:
                continue
            info = probe(s.path, timeout=20)
            if info.duration > 0.1 and (info.has_video or info.has_audio):
                ok.append(s)
        except FFmpegError as e:
            log.warning("Skipping corrupt segment %s: %s", s.path.name, e)
    return ok


def _join_segments(segs: list[Segment], joined: Path, temp_dir: Path, cancel) -> None:
    """Join TS segments without breaking A/V sync.

    Segments of one recorder run are byte-concatenated (``concat:`` protocol): MPEG-TS is designed
    for this and the original, continuous timestamps are preserved. The concat *demuxer* must not be
    used here - it re-bases every file on its duration, which shifts audio against video a little at
    every boundary and accumulates to hundreds of milliseconds. Only when the recorder was restarted
    (different run folders) are the per-run parts joined with the demuxer, at that single boundary.
    """
    ff = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-y"]
    runs: list[list[Segment]] = []
    for s in segs:
        if runs and runs[-1][-1].path.parent == s.path.parent:
            runs[-1].append(s)
        else:
            runs.append([s])

    def proto(group: list[Segment], out: Path) -> None:
        # Windows' command line limit is ~32k chars; a 300 s buffer of 4 s segments fits easily
        src = "concat:" + "|".join(str(x.path.resolve()) for x in group)
        run(ff + ["-i", src, "-map", "0:v?", "-map", "0:a?", "-c", "copy", str(out)], timeout=600, cancel=cancel)

    if len(runs) == 1:
        proto(runs[0], joined)
        return
    parts = []
    try:
        for i, g in enumerate(runs):
            part = temp_dir / f"{joined.stem}_part{i}.ts"
            proto(g, part)
            parts.append(part)
        lst = temp_dir / f"{joined.stem}_parts.txt"
        lst.write_text("".join(f"file '{x.resolve().as_posix()}'\n" for x in parts), encoding="utf-8")
        parts.append(lst)
        run(ff + ["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)], timeout=600, cancel=cancel)
    finally:
        for x in parts:
            x.unlink(missing_ok=True)


def extract_clip(segments: list[Segment], start: float, end: float, out_path: Path, temp_dir: Path,
                 cancel: threading.Event | None = None, validate: bool = True) -> ClipResult:
    segs = validate_segments(segments) if validate else list(segments)
    if not segs:
        raise FFmpegError("No valid recording segments available for this clip.")
    segs.sort(key=lambda s: s.start)
    start = max(start, segs[0].start)
    end = min(end, segs[-1].end)
    if end - start < 1.0:
        raise FFmpegError("Recorded footage is too short for this clip.")
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joined = temp_dir / f"{out_path.stem}_joined.ts"
    ff = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-y"]
    try:
        _join_segments(segs, joined, temp_dir, cancel)
        offset = start - segs[0].start
        length = end - start
        aligned = offset < 0.25 and abs(end - segs[-1].end) < 0.25
        if aligned:
            run(ff + ["-i", str(joined), "-map", "0:v?", "-map", "0:a?", "-c", "copy",
                      "-movflags", "+faststart", str(out_path)], timeout=600, cancel=cancel)
        else:
            # Accurate trimming requires re-encoding. Seek on the output side: the joined TS keeps the
            # stream's original timestamps, and output seeking is exact for both audio and video.
            run(ff + ["-i", str(joined), "-ss", f"{offset:.3f}", "-t", f"{length:.3f}",
                      "-map", "0:v?", "-map", "0:a?",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)],
                timeout=1800, cancel=cancel)
        info = probe(out_path)
        if info.duration < 0.5:
            raise FFmpegError("Clip output is empty or corrupted.")
        return ClipResult(out_path, start, info.duration, not aligned)
    finally:
        joined.unlink(missing_ok=True)
