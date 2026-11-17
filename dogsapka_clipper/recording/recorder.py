"""Live recorder: Streamlink (official public playback) -> FFmpeg segment muxer.

Produces a rolling set of MPEG-TS segments without re-encoding. In simulation mode a local
file is read in real time instead of a Twitch stream.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dogsapka_clipper.processing.ffmpeg_utils import popen_kwargs, tool_cmd
from dogsapka_clipper.recording.buffer import Segment, WallClockMapper

log = logging.getLogger(__name__)


@dataclass
class RecorderSource:
    kind: str              # "twitch" | "youtube" | "file"
    target: str            # Twitch login, YouTube live video URL or file path
    quality: str = "best"
    loop: bool = False


class StreamRecorder:
    def __init__(self, source: RecorderSource, out_dir: Path, segment_seconds: int,
                 on_segment: Callable[[Segment], None],
                 on_status: Callable[[str, str], None] | None = None,
                 oauth_token_provider: Callable[[], str | None] | None = None) -> None:
        self.source = source
        self.out_dir = out_dir
        self.segment_seconds = segment_seconds
        self.on_segment = on_segment
        self.on_status = on_status or (lambda lvl, msg: None)
        self._token_provider = oauth_token_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._procs: list[subprocess.Popen] = []
        self.running = False
        self.restarts = 0
        self.last_error = ""
        self._run_no = 0

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"rec-{self.source.target}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        self._terminate()
        if self._thread:
            self._thread.join(timeout)
        self.running = False

    def finished(self) -> bool:
        return self._thread is None or not self._thread.is_alive()

    # ------------------------------------------------------------------
    def _build(self, run_dir: Path) -> tuple[list[str] | None, list[str]]:
        seg_args = ["-map", "0:v?", "-map", "0:a?", "-c", "copy",
                    "-f", "segment", "-segment_time", str(self.segment_seconds),
                    "-segment_format", "mpegts", "-segment_list", str(run_dir / "segments.csv"),
                    "-segment_list_type", "csv", "-segment_list_flags", "live",
                    str(run_dir / "seg_%06d.ts")]
        ff = tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if self.source.kind == "file":
            ff += ["-re"] + (["-stream_loop", "-1"] if self.source.loop else []) + ["-i", self.source.target]
            return None, ff + seg_args
        if self.source.kind == "youtube":
            # target is the live video URL (https://www.youtube.com/watch?v=…)
            sl = tool_cmd("streamlink") + ["--stdout", "--retry-open", "3", "--stream-segment-attempts", "5",
                                           self.source.target, f"{self.source.quality},best"]
            return sl, ff + ["-fflags", "+genpts+discardcorrupt", "-i", "pipe:0"] + seg_args
        sl = tool_cmd("streamlink") + ["--stdout", "--twitch-disable-ads", "--retry-open", "3",
                                       "--stream-segment-attempts", "5"]
        token = self._token_provider() if self._token_provider else None
        if token:
            sl += ["--twitch-api-header", f"Authorization=OAuth {token}"]
        sl += [f"https://www.twitch.tv/{self.source.target}", f"{self.source.quality},best"]
        return sl, ff + ["-fflags", "+genpts+discardcorrupt", "-i", "pipe:0"] + seg_args

    def _terminate(self) -> None:
        for p in self._procs:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(5)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        self._procs = []

    def _loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            started = time.time()
            ok = self._run_once()
            if self._stop.is_set():
                break
            if self.source.kind == "file" and ok and not self.source.loop:
                break  # simulation source ended normally
            if time.time() - started > 120:
                backoff = 2.0
            self.restarts += 1
            self.on_status("warning", f"Recorder for {self.source.target} stopped ({self.last_error or 'stream ended'}); "
                                      f"restarting in {backoff:.0f}s")
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 60)
        self.running = False

    def _run_once(self) -> bool:
        self._run_no += 1
        run_dir = self.out_dir / f"run_{int(time.time())}_{self._run_no}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            sl_cmd, ff_cmd = self._build(run_dir)
        except Exception as e:
            self.last_error = str(e)
            self.on_status("error", str(e))
            self._stop.wait(30)
            return False
        mapper = WallClockMapper()
        try:
            if sl_cmd:
                sl = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      stdin=subprocess.DEVNULL, **popen_kwargs())
                ff = subprocess.Popen(ff_cmd, stdin=sl.stdout, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, **popen_kwargs())
                sl.stdout.close()  # let streamlink get SIGPIPE if ffmpeg exits
                self._procs = [sl, ff]
            else:
                ff = subprocess.Popen(ff_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, **popen_kwargs())
                self._procs = [ff]
        except OSError as e:
            self.last_error = f"could not start recorder: {e}"
            return False
        self.running = True
        self.on_status("info", f"Recording buffer started for {self.source.target}")
        stderr_buf: list[str] = []
        threading.Thread(target=_drain, args=(ff.stderr, stderr_buf), daemon=True).start()
        csv_path = run_dir / "segments.csv"
        pos = 0
        index = 0
        while True:
            alive = ff.poll() is None
            pos, rows = _read_new_rows(csv_path, pos)
            now = time.time()
            for name, st, en in rows:
                mapper.observe(en, now)
                p = run_dir / name
                if p.exists() and p.stat().st_size > 0 and en > st:
                    index += 1
                    self.on_segment(Segment(path=p, start=mapper.to_wall(st), duration=en - st, index=index))
            if not alive:
                break
            if self._stop.wait(0.25):
                break
        self._terminate()
        self.running = False
        rc = ff.returncode
        self.last_error = (stderr_buf[-1].strip() if stderr_buf else "") if rc not in (0, None, 255, -15) else ""
        return rc == 0 or not self.last_error


def _drain(stream, buf: list[str]) -> None:
    try:
        for line in iter(stream.readline, b""):
            buf.append(line.decode("utf-8", "replace"))
            if len(buf) > 50:
                del buf[:25]
    except Exception:
        pass


def _read_new_rows(path: Path, pos: int) -> tuple[int, list[tuple[str, float, float]]]:
    if not path.exists():
        return pos, []
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()
    # only consume complete lines
    last_nl = data.rfind(b"\n")
    if last_nl < 0:
        return pos, []
    chunk = data[: last_nl + 1]
    rows = []
    for line in chunk.decode("utf-8", "replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 3:
            try:
                rows.append((parts[0], float(parts[1]), float(parts[2])))
            except ValueError:
                continue
    return pos + len(chunk), rows
