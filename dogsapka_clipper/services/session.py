"""A live monitoring session for one streamer: recording buffer + detectors + clip creation."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dogsapka_clipper.core.paths import safe_filename
from dogsapka_clipper.db.models import Clip, Highlight, MonitoringSession, RecordingSegment, Streamer, utcnow
from dogsapka_clipper.detection.audio import AudioExcitementDetector
from dogsapka_clipper.detection.chat import ChatActivityDetector, ChatReactionDetector
from dogsapka_clipper.detection.scorer import HighlightEvent, HighlightScorer
from dogsapka_clipper.detection.visual import VisualChangeDetector
from dogsapka_clipper.processing.ffmpeg_utils import FFmpegError, decode_audio_pcm, decode_gray_frames, extract_frame
from dogsapka_clipper.recording.buffer import Segment, SegmentRingBuffer
from dogsapka_clipper.recording.clipper import extract_clip, plan_window
from dogsapka_clipper.recording.recorder import RecorderSource, StreamRecorder
from dogsapka_clipper.twitch.chat import ChatMessage, TwitchChatReader

if TYPE_CHECKING:
    from dogsapka_clipper.services.backend import Backend

log = logging.getLogger(__name__)


@dataclass
class PendingClip:
    event: HighlightEvent
    start: float
    end: float
    token: int
    highlight_id: int
    created: float = field(default_factory=time.time)


@dataclass
class LiveStats:
    streamer_id: int
    login: str
    chat_rate: float = 0.0
    chat_baseline: float = 0.0
    audio_level: float = 0.0
    score: float = 0.0
    threshold: float = 60.0
    buffer_s: float = 0.0
    buffer_target: float = 120.0
    recording: bool = False
    chat_connected: bool = False
    highlights: int = 0
    scores: list = field(default_factory=list)
    events: list = field(default_factory=list)   # recent highlight markers (ts, score)
    signals: dict = field(default_factory=dict)


class LiveSession:
    def __init__(self, backend: "Backend", streamer_id: int, login: str, source: RecorderSource,
                 chat_mode: str = "twitch", sim_highlights: list[float] | None = None,
                 title: str = "", category: str = "", stream_id: str = "", live_chat_id: str = "") -> None:
        self.backend = backend
        self.streamer_id = streamer_id
        self.login = login
        self.source = source
        self.chat_mode = chat_mode
        self.sim_highlights = sim_highlights or []
        self.live_chat_id = live_chat_id
        st = backend.settings
        # YouTube chat is polled in batches: evaluate chat this many seconds in the past so that every
        # message of the window has arrived (Twitch IRC is real time -> 0).
        self.chat_lag_s = (st.youtube_live.chat_poll_s + 5.0) if chat_mode == "youtube" else 0.0
        self.rs = st.recording
        self.ds = st.detection
        self._stop = asyncio.Event()
        self.ended = False
        self.started_wall = time.time()
        with backend.db.session() as s:
            sess = MonitoringSession(streamer_id=streamer_id, title=title, category=category,
                                     twitch_stream_id=stream_id, is_simulation=source.kind == "file")
            s.add(sess)
            s.flush()
            self.session_id = sess.id
            baseline = dict(s.get(Streamer, streamer_id).baseline or {})
        self.rec_dir = backend.paths.recordings / safe_filename(login) / f"session_{self.session_id}"
        self.buffer = SegmentRingBuffer(self.rs.buffer_seconds + self.rs.pre_event_s, on_delete=self._on_segment_deleted)
        sens = self.ds.sensitivity
        self.chat_act = ChatActivityDetector(sensitivity=sens)
        self.chat_react = ChatReactionDetector(sensitivity=sens)
        self.audio = AudioExcitementDetector(sensitivity=sens)
        self.visual = VisualChangeDetector(sensitivity=sens) if self.ds.use_visual else None
        self._visual_baseline = baseline.get("visual")
        self.chat_act.load_state(baseline.get("chat_activity"))
        self.chat_react.load_state(baseline.get("chat_reactions"))
        self.audio.load_state(baseline.get("audio"))
        if self.visual:
            self.visual.load_state(baseline.get("visual"))
        self.scorer = HighlightScorer(self.ds, self.rs.post_event_s, self._on_highlight,
                                      eval_lag_s=max(self.rs.segment_seconds * 2 + 3, self.chat_lag_s + 5),
                                      score_adjuster=backend._preference_adjustment if source.kind != "file" else None)
        self.pending: list[PendingClip] = []
        self._analysis = ThreadPoolExecutor(1, thread_name_prefix=f"analyze-{login}")
        self._clipper = ThreadPoolExecutor(1, thread_name_prefix=f"clip-{login}")
        self._clip_futures: list = []
        self.recorder = StreamRecorder(source, self.rec_dir, self.rs.segment_seconds, self._on_segment,
                                       on_status=self._rec_status,
                                       oauth_token_provider=None)
        self.chat_reader = None   # TwitchChatReader | YouTubeChatReader
        self._sim_chat = None
        self.stats = LiveStats(streamer_id, login, buffer_target=self.rs.buffer_seconds)
        self.highlight_count = 0
        self._seg_ids: dict[str, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    def _rec_status(self, level: str, msg: str) -> None:
        self.backend.bus.emit("recording.status", msg, level, streamer_id=self.streamer_id)

    def _on_segment(self, seg: Segment) -> None:
        """Recorder thread: a new segment is complete."""
        self.buffer.add(seg)
        try:
            with self.backend.db.session() as s:
                row = RecordingSegment(session_id=self.session_id, path=str(seg.path), start_ts=seg.start,
                                       duration=seg.duration)
                s.add(row)
                s.flush()
                self._seg_ids[str(seg.path)] = row.id
        except Exception:
            log.exception("could not record segment row")
        self._analysis.submit(self._analyze_segment, seg)

    def _on_segment_deleted(self, seg: Segment) -> None:
        sid = self._seg_ids.pop(str(seg.path), None)
        if sid:
            try:
                with self.backend.db.session() as s:
                    r = s.get(RecordingSegment, sid)
                    if r:
                        r.deleted = True
            except Exception:
                pass

    def _analyze_segment(self, seg: Segment) -> None:
        try:
            if self.ds.use_audio:
                pcm = decode_audio_pcm(seg.path, timeout=30)
                for ts, smp in self.audio.process(pcm, seg.start):
                    self.scorer.push("audio", ts, smp)
            if self.ds.use_visual:
                if self.visual is None:
                    self.visual = VisualChangeDetector(sensitivity=self.ds.sensitivity)
                    self.visual.load_state(self._visual_baseline)
                visual = self.visual
                frames = decode_gray_frames(seg.path, fps=visual.fps, timeout=30)
                for ts, smp in visual.process(frames, seg.start):
                    self.scorer.push("visual", ts, smp)
        except FFmpegError as e:
            log.warning("Segment analysis failed for %s: %s", seg.path.name, e)
        except Exception:
            log.exception("Segment analysis crashed")

    async def _on_chat(self, msg: ChatMessage) -> None:
        if self.ds.use_chat_activity:
            self.chat_act.add(msg.ts, msg.user_hash)
        if self.ds.use_chat_reactions:
            self.chat_react.add(msg.ts, msg.text, msg.emotes)

    # ------------------------------------------------------------------
    def apply_detection_settings(self, settings) -> None:
        """Apply on the session loop; video models are initialized on the analysis worker."""
        self.ds = settings
        self.scorer.s = settings
        for detector in (self.chat_act, self.chat_react, self.audio, self.visual):
            if detector is not None:
                detector.sensitivity = settings.sensitivity

    def _on_highlight(self, ev: HighlightEvent) -> None:
        start, end = plan_window(ev.peak_ts, self.rs.pre_event_s, self.rs.post_event_s,
                                 self.rs.min_clip_s, self.rs.max_clip_s, ev.start_ts, ev.end_ts)
        # never start before the footage we actually still have
        start = max(start, self.buffer.earliest_start())
        token = self.buffer.protect(start, end)
        with self.backend.db.session() as s:
            h = Highlight(session_id=self.session_id, event_ts=ev.peak_ts, start_ts=start, end_ts=end,
                          score=round(ev.score, 1), reasons=ev.reasons, signals=ev.signals)
            s.add(h)
            s.flush()
            hid = h.id
        self.pending.append(PendingClip(ev, start, end, token, hid))
        self.highlight_count += 1
        self.stats.events.append((ev.peak_ts, ev.score))
        self.stats.events = self.stats.events[-50:]
        self.backend.bus.emit("highlight.detected",
                              f"Highlight detected for {self.login}: {ev.score:.0f}/100 ({ev.category}) - "
                              f"{'; '.join(ev.reasons[:2])}", "success",
                              streamer_id=self.streamer_id, score=ev.score, ts=ev.peak_ts)

    def _check_pending(self, force: bool = False) -> None:
        latest = self.buffer.latest_end()
        for pc in list(self.pending):
            if force or latest >= pc.end or time.time() - pc.created > self.rs.post_event_s + 120:
                self.pending.remove(pc)
                self._clip_futures.append(self._clipper.submit(self._make_clip, pc))

    def _make_clip(self, pc: PendingClip) -> None:
        paths = self.backend.paths
        try:
            segs = self.buffer.range(pc.start, pc.end)
            stamp = datetime.fromtimestamp(pc.event.peak_ts).strftime("%Y%m%d_%H%M%S")
            name = safe_filename(f"{self.login}_{stamp}_{int(pc.event.score)}")
            out = paths.clips / safe_filename(self.login) / f"{name}.mp4"
            res = extract_clip(segs, pc.start, pc.end, out, paths.temp)
            peak_rel = max(0.0, pc.event.peak_ts - res.start)
            thumb = paths.thumbnails / f"{name}.jpg"
            try:
                extract_frame(out, min(peak_rel, max(0.0, res.duration - 0.5)), thumb)
            except FFmpegError:
                thumb = Path("")
            from dogsapka_clipper.processing.ffmpeg_utils import probe
            info = probe(out)
            with self.backend.db.session() as s:
                c = Clip(streamer_id=self.streamer_id, highlight_id=pc.highlight_id, source_path=str(out),
                         thumbnail_path=str(thumb) if str(thumb) else "", duration=res.duration,
                         width=info.width, height=info.height, fps=info.fps,
                         detection_score=round(pc.event.score, 1), quality_score=round(pc.event.score * 0.8, 1),
                         reason="; ".join(pc.event.reasons[:3])[:250],
                         edit_state={"peak_rel": peak_rel, "trim_start": 0.0, "trim_end": res.duration})
                s.add(c)
                s.flush()
                clip_id = c.id
            self.backend.bus.emit("clip.saved", f"Clip saved successfully: {out.name} ({res.duration:.0f}s)",
                                  "success", clip_id=clip_id, streamer_id=self.streamer_id)
            if self.backend.settings.output.auto_render:
                self.backend.jobs.submit("process", clip_id)
        except Exception as e:
            log.exception("Clip creation failed")
            self.backend.bus.emit("clip.failed", f"Could not save clip for {self.login}: {e}", "error",
                                  streamer_id=self.streamer_id)
        finally:
            self.buffer.release(pc.token)

    # ------------------------------------------------------------------
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        bus = self.backend.bus
        self.started_wall = time.time()
        if self.rs.enabled:
            self.recorder.start()
        chat_task = None
        if self.chat_mode == "twitch" and self.backend.settings.twitch.use_chat:
            self.chat_reader = TwitchChatReader(self.login, self._on_chat,
                                                on_status=lambda l, m: bus.emit("chat.status", m, l,
                                                                                streamer_id=self.streamer_id))
            chat_task = asyncio.create_task(self.chat_reader.run())
        elif self.chat_mode == "youtube" and self.backend.settings.youtube_live.use_chat:
            from dogsapka_clipper.youtube.live import YouTubeChatReader
            self.chat_reader = YouTubeChatReader(
                self.backend.youtube_live_client(), self.live_chat_id, self._on_chat,
                poll_s=self.backend.settings.youtube_live.chat_poll_s,
                on_status=lambda l, m: bus.emit("chat.status", m, l, streamer_id=self.streamer_id))
            chat_task = asyncio.create_task(self.chat_reader.run())
        elif self.chat_mode == "simulated":
            from dogsapka_clipper.simulation.simulator import SimulatedChat
            self._sim_chat = SimulatedChat(self.sim_highlights)
            chat_task = asyncio.create_task(self._sim_chat.run(self._on_chat, start_wall=self.started_wall))
        bus.emit("session.started", f"Monitoring session started for {self.login}", "info",
                 streamer_id=self.streamer_id, session_id=self.session_id)
        try:
            while not self._stop.is_set():
                now = time.time()
                if self.ds.use_chat_activity or self.ds.use_chat_reactions:
                    if chat_task is not None:
                        t_chat = now - self.chat_lag_s
                        if self.ds.use_chat_activity:
                            self.scorer.push("chat_activity", t_chat, self.chat_act.evaluate(t_chat))
                        if self.ds.use_chat_reactions:
                            self.scorer.push("chat_reactions", t_chat, self.chat_react.evaluate(t_chat))
                self.scorer.tick(now)
                self._check_pending()
                self._update_stats()
                if self.source.kind == "file" and self.recorder.finished() and self.rs.enabled:
                    bus.emit("session.sim_ended", f"Simulation stream for {self.login} finished", "info",
                             streamer_id=self.streamer_id)
                    # let the analysis of the last segments and the scorer catch up
                    await asyncio.sleep(1.0)
                    await asyncio.get_running_loop().run_in_executor(None, self._analysis.submit(lambda: None).result)
                    self.scorer.tick(time.time() + self.scorer.eval_lag_s)
                    break
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self.chat_reader:
                self.chat_reader.stop()
            if self._sim_chat:
                self._sim_chat.stop()
            if chat_task:
                chat_task.cancel()
                try:
                    await chat_task
                except (asyncio.CancelledError, Exception):
                    pass
            await asyncio.get_running_loop().run_in_executor(None, self._shutdown)

    def _update_stats(self) -> None:
        st = self.stats
        st.chat_rate = self.chat_act.last_rate
        st.chat_baseline = self.chat_act.rate_stats.mean
        st.audio_level = self.audio.last_level
        st.score = self.scorer.current_score()
        st.threshold = self.scorer.threshold
        st.buffer_s = min(self.buffer.covered_seconds(), self.rs.buffer_seconds)
        st.recording = self.recorder.running
        st.chat_connected = bool(self.chat_reader and self.chat_reader.connected) or self._sim_chat is not None
        st.highlights = self.highlight_count
        st.scores = list(self.scorer.scores)[-180:]
        self.backend.bus.emit("live.stats", "", streamer_id=self.streamer_id, stats=st)

    def stop(self) -> None:
        if self._loop and not self._stop.is_set():
            self._loop.call_soon_threadsafe(self._stop.set)

    def _shutdown(self) -> None:
        """Stop recording safely, finalize clips, persist baselines, release resources."""
        self.recorder.stop()
        self._analysis.shutdown(wait=True)
        self.scorer.flush()
        self._check_pending(force=True)
        for f in list(self._clip_futures):
            try:
                f.result(timeout=900)
            except Exception:
                pass
        self._clipper.shutdown(wait=True)
        baseline = {"chat_activity": self.chat_act.state(), "chat_reactions": self.chat_react.state(),
                    "audio": self.audio.state()}
        if self.visual:
            baseline["visual"] = self.visual.state()
        with self.backend.db.session() as s:
            sess = s.get(MonitoringSession, self.session_id)
            if sess:
                sess.ended_at = utcnow()
                sess.status = "ended"
            strm = s.get(Streamer, self.streamer_id)
            if strm and self.source.kind != "file":
                # simulation must not pollute the real streamer's learned baseline
                strm.baseline = baseline
        self.buffer.clear(delete_files=True)
        _cleanup_dir(self.rec_dir)
        self.ended = True
        self.backend.bus.emit("session.ended", f"Monitoring session ended for {self.login} "
                                               f"({self.highlight_count} highlights)", "info",
                              streamer_id=self.streamer_id, session_id=self.session_id)


def _cleanup_dir(d: Path) -> None:
    """Remove leftover recording files of an ended session (never touches clips/exports)."""
    if not d.exists():
        return
    for p in sorted(d.rglob("*"), reverse=True):
        try:
            p.unlink() if p.is_file() else p.rmdir()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass
