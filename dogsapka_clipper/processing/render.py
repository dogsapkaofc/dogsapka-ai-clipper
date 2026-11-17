"""FFmpeg render engine: 9:16 layouts, smart crop, zoom, subtitles, audio processing."""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dogsapka_clipper.processing.ffmpeg_utils import (FFmpegError, MediaInfo, _last_error_line, popen_kwargs, probe,
                                                  tool_cmd, video_encoder_args)
from dogsapka_clipper.processing.layout import LayoutAnalysis, Rect

log = logging.getLogger(__name__)


@dataclass
class RenderSpec:
    src: Path
    out: Path
    width: int = 1080
    height: int = 1920
    fps: int = 30
    encoder: str = "libx264"
    crf: int = 20
    video_bitrate_k: int = 0
    audio_bitrate_k: int = 160
    trim_start: float = 0.0
    trim_end: float | None = None
    layout: str = "crop"                       # crop | camera | facecam_top | blur | source
    aspect: str = "9:16"
    analysis: LayoutAnalysis | None = None
    crop_center_x: float | None = None         # manual override 0..1
    facecam: Rect | None = None                # manual override
    facecam_ratio: float = 0.38
    dynamic_focus: bool = True
    keep_ranges: list[tuple[float, float]] | None = None   # relative to trimmed clip time
    ass_path: Path | None = None
    fonts_dir: Path | None = None
    zoom_at: float | None = None               # output seconds of the peak
    zoom_strength: float = 0.08
    normalize_audio: bool = True
    volume: float = 1.0
    fade: bool = False
    extra: dict = field(default_factory=dict)


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


def escape_filter_path(p: Path) -> str:
    s = Path(p).resolve().as_posix()
    return "'" + s.replace("\\", "/").replace(":", r"\:").replace("'", r"\'") + "'"


def _x_expr(track: list[tuple[float, float]], cw: int, iw_expr: str = "iw") -> str:
    """Crop x position expression following a smoothed horizontal focus track."""
    if not track:
        return f"({iw_expr}-{cw})/2"
    if len(track) == 1:
        cx = track[0][1]
        return f"max(0,min({iw_expr}-{cw},{cx:.4f}*{iw_expr}-{cw}/2))"
    # piecewise-linear interpolation, nested if()
    expr = f"{track[-1][1]:.4f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(track, track[1:]))):
        seg = f"({x0:.4f}+({x1:.4f}-{x0:.4f})*(t-{t0:.3f})/{max(t1 - t0, 0.001):.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    expr = f"if(lt(t,{track[0][0]:.3f}),{track[0][1]:.4f},{expr})"
    return f"max(0,min({iw_expr}-{cw},({expr})*{iw_expr}-{cw}/2))"


def output_size(spec: RenderSpec, info: MediaInfo) -> tuple[int, int]:
    if spec.aspect == "source" or spec.layout == "source":
        long_side = max(spec.width, spec.height)
        iw, ih = info.width or 1920, info.height or 1080
        k = min(1.0, long_side / max(iw, ih))
        return _even(iw * k), _even(ih * k)
    return _even(spec.width), _even(spec.height)


def build_keep_filter(keep: list[tuple[float, float]], has_audio: bool) -> tuple[str, str, str]:
    """Cut out removed ranges while keeping audio and video locked together.

    Each kept range is trimmed from both streams and the pieces go through ONE concat filter with
    v=1:a=1, which aligns every piece's audio and video. (Re-timing video by frame count, as a
    select+setpts approach does, drifts against the audio on variable-frame-rate Twitch footage.)
    Returns (graph, video_label, audio_label).
    """
    n = len(keep)
    parts = [f"[0:v]split={n}" + "".join(f"[ks{i}]" for i in range(n))]
    if has_audio:
        parts.append(f"[0:a]asplit={n}" + "".join(f"[ka{i}]" for i in range(n)))
    inputs = ""
    for i, (a, b) in enumerate(keep):
        parts.append(f"[ks{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[kv{i}]")
        inputs += f"[kv{i}]"
        if has_audio:
            parts.append(f"[ka{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[kb{i}]")
            inputs += f"[kb{i}]"
    parts.append(f"{inputs}concat=n={n}:v=1:a={1 if has_audio else 0}[kvo]" + ("[kao]" if has_audio else ""))
    return ";".join(parts), "[kvo]", "[kao]" if has_audio else ""


def build_video_filter(spec: RenderSpec, info: MediaInfo, out_fps: float, vin: str = "[0:v]") -> str:
    W, H = output_size(spec, info)
    iw, ih = info.width or 1920, info.height or 1080
    chains: list[str] = []
    chains.append(f"{vin}fps={out_fps:g}[v0]")
    a = spec.analysis
    layout = spec.layout
    track: list[tuple[float, float]] = []
    if spec.crop_center_x is not None:
        track = [(0.0, spec.crop_center_x)]
    elif layout == "camera" and a and a.face_center:
        track = [(0.0, a.face_center[0])]
    elif a and spec.dynamic_focus and a.crop_track:
        track = [(max(0.0, t - spec.trim_start), x) for t, x in a.crop_track]
    if layout == "source":
        chains.append(f"[v0]scale={W}:{H}:flags=lanczos,setsar=1[vl]")
    elif layout == "blur":
        chains.append(f"[v0]split=2[bga][fga];"
                      f"[bga]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                      f"boxblur=10:2,eq=brightness=-0.06:saturation=1.15,scale={W}:{H}[bg];"
                      f"[fga]scale={W}:-2:flags=lanczos[fg];"
                      f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vl]")
    elif layout == "facecam_top" and (spec.facecam or (a and a.facecam)):
        fc = (spec.facecam or a.facecam).clamp(iw, ih)
        cam_h = _even(H * min(0.6, max(0.2, spec.facecam_ratio)))
        game_h = H - cam_h
        cw = min(iw, _even(ih * W / game_h))
        ch = min(ih, _even(cw * game_h / W))
        gy = (ih - ch) // 2
        chains.append(f"[v0]split=2[cama][gamea];"
                      f"[cama]crop={fc.w}:{fc.h}:{fc.x}:{fc.y},scale={W}:{cam_h}:force_original_aspect_ratio=increase:"
                      f"flags=lanczos,crop={W}:{cam_h}[cam];"
                      f"[gamea]crop={cw}:{ch}:'{_x_expr(track, cw)}':{gy},scale={W}:{game_h}:flags=lanczos[game];"
                      f"[cam][game]vstack=inputs=2,setsar=1[vl]")
    else:  # crop / camera / facecam without rect
        cw = min(iw, _even(ih * W / H))
        ch = min(ih, _even(cw * H / W))
        y = (ih - ch) // 2
        chains.append(f"[v0]crop={cw}:{ch}:'{_x_expr(track, cw)}':{y},scale={W}:{H}:flags=lanczos,setsar=1[vl]")
    last = "vl"
    post = []
    if spec.zoom_at is not None and spec.zoom_strength > 0:
        a0, d = max(0.0, spec.zoom_at - 0.8), 3.2
        z = f"1+{spec.zoom_strength:.3f}*sin(PI*min(max((in_time-{a0:.3f})/{d},0),1))"
        post.append(f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={out_fps:g}")
    if spec.ass_path:
        fd = f":fontsdir={escape_filter_path(spec.fonts_dir)}" if spec.fonts_dir and spec.fonts_dir.exists() else ""
        post.append(f"ass={escape_filter_path(spec.ass_path)}{fd}")
    if spec.fade:
        dur = spec.extra.get("out_duration", 0)
        post.append("fade=t=in:st=0:d=0.3")
        if dur > 1:
            post.append(f"fade=t=out:st={dur - 0.4:.3f}:d=0.4")
    post.append("format=yuv420p")
    chains.append(f"[{last}]{','.join(post)}[vout]")
    return ";".join(chains)


def build_audio_filter(spec: RenderSpec, out_duration: float, ain: str = "[0:a]") -> str:
    parts = []
    if abs(spec.volume - 1.0) > 1e-3:
        parts.append(f"volume={spec.volume:.3f}")
    if spec.normalize_audio:
        parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    parts.append("aresample=48000")
    if spec.fade and out_duration > 1:
        parts += ["afade=t=in:st=0:d=0.2", f"afade=t=out:st={out_duration - 0.4:.3f}:d=0.4"]
    return f"{ain}{','.join(parts)}[aout]"


def plan_output(spec: RenderSpec, info: MediaInfo) -> tuple[float, float]:
    """Return (output duration, fps)."""
    end = spec.trim_end if spec.trim_end is not None else info.duration
    end = min(end, info.duration) if info.duration else end
    length = max(0.1, end - spec.trim_start)
    if spec.keep_ranges:
        length = sum(min(b, length) - a for a, b in spec.keep_ranges if a < length)
    src_fps = info.fps or spec.fps
    # never invent frames: output fps <= source fps
    fps = spec.fps if src_fps >= spec.fps - 1 else (30 if src_fps >= 29 else round(src_fps))
    return length, float(fps)


def build_command(spec: RenderSpec, info: MediaInfo) -> tuple[list[str], float]:
    duration, fps = plan_output(spec, info)
    spec.extra["out_duration"] = duration
    end = spec.trim_end if spec.trim_end is not None else info.duration
    args = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-y", "-nostdin",
                                 "-progress", "pipe:1", "-nostats"]
    if spec.trim_start > 0:
        args += ["-ss", f"{spec.trim_start:.3f}"]
    args += ["-i", str(spec.src)]
    if end:
        args += ["-t", f"{max(0.1, end - spec.trim_start):.3f}"]
    vin, ain, pre = "[0:v]", "[0:a]", ""
    if spec.keep_ranges:
        pre, vin, ain = build_keep_filter(spec.keep_ranges, info.has_audio)
        pre += ";"
    fg = pre + build_video_filter(spec, info, fps, vin)
    if info.has_audio:
        fg += ";" + build_audio_filter(spec, duration, ain)
    args += ["-filter_complex", fg, "-map", "[vout]"]
    if info.has_audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", f"{spec.audio_bitrate_k}k", "-ar", "48000"]
    args += video_encoder_args(spec.encoder, spec.crf, spec.video_bitrate_k, int(fps))
    args += ["-r", f"{fps:g}", str(spec.out)]
    return args, duration


def render(spec: RenderSpec, progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None, pause: threading.Event | None = None,
           timeout: float = 3600) -> Path:
    info = probe(spec.src)
    if not info.has_video:
        raise FFmpegError("Source clip has no video stream.")
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = spec.out.with_name(spec.out.stem + ".part" + spec.out.suffix)
    final_out = spec.out
    spec.out = tmp_out
    try:
        args, duration = build_command(spec, info)
    finally:
        spec.out = final_out
    log.info("Rendering %s (%s, %s)", final_out.name, spec.layout, spec.encoder)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            **popen_kwargs())
    err_lines: list[str] = []

    def drain_err():
        for line in iter(proc.stderr.readline, b""):
            err_lines.append(line.decode("utf-8", "replace"))
    threading.Thread(target=drain_err, daemon=True).start()
    start = time.monotonic()
    suspended = False
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                    if progress and duration > 0:
                        progress(min(0.999, us / 1e6 / duration))
                except ValueError:
                    pass
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise FFmpegError("Cancelled")
            if pause is not None:
                if pause.is_set() and not suspended:
                    suspended = _suspend(proc, True)
                while pause.is_set() and not (cancel and cancel.is_set()):
                    time.sleep(0.2)
                if suspended:
                    suspended = not _suspend(proc, False)
            if time.monotonic() - start > timeout:
                proc.kill()
                raise FFmpegError(f"Render timed out after {timeout:.0f}s")
        proc.wait(30)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        tmp_out.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        stderr = "".join(err_lines)
        raise FFmpegError(f"Render failed: {_last_error_line(stderr)}", stderr)
    tmp_out.replace(final_out)
    if progress:
        progress(1.0)
    return final_out


def _suspend(proc: subprocess.Popen, stop: bool) -> bool:
    """Pause/resume a running ffmpeg process."""
    try:
        if sys.platform == "win32":
            import psutil  # optional
            p = psutil.Process(proc.pid)
            p.suspend() if stop else p.resume()
        else:
            import signal
            proc.send_signal(signal.SIGSTOP if stop else signal.SIGCONT)
        return True
    except Exception:
        return False


def render_preview_frame(spec: RenderSpec, t: float, out_png: Path, timeout: float = 60) -> Path:
    """Render a single output frame at clip time ``t`` with the current edit settings (before/after view)."""
    from dogsapka_clipper.processing.ffmpeg_utils import run
    info = probe(spec.src)
    s = RenderSpec(**{**spec.__dict__})
    s.trim_start, s.trim_end, s.keep_ranges, s.zoom_at, s.fade = 0.0, None, None, None, False
    s.extra = {}
    fg = build_video_filter(s, info, info.fps or 30)
    args = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-y", "-i", str(spec.src),
                                 "-filter_complex", fg, "-map", "[vout]", "-ss", f"{max(0.0, t):.3f}",
                                 "-frames:v", "1", str(out_png)]
    run(args, timeout=timeout)
    return out_png
