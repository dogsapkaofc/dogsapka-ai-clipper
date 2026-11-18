"""Automatic clip processing pipeline (analysis -> subtitles -> layout -> render -> QC -> metadata)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.core.paths import AppPaths, safe_filename, unique_path
from dogsapka_clipper.db.database import Database
from dogsapka_clipper.db.models import Clip, EditingProfile, ExportedVideo, Highlight, MonitoringSession
from dogsapka_clipper.processing import analysis as clip_analysis
from dogsapka_clipper.processing.ffmpeg_utils import FFmpegError, choose_encoder, probe
from dogsapka_clipper.processing.jobs import JobContext
from dogsapka_clipper.processing.layout import FaceDetector, LayoutAnalysis, Rect, analyze_layout
from dogsapka_clipper.processing.metadata import generate_metadata
from dogsapka_clipper.processing.profiles import BUILTIN_PROFILES, builtin, resolve_profile
from dogsapka_clipper.processing.qc import check_video
from dogsapka_clipper.processing.ranking import quality_score
from dogsapka_clipper.processing.render import RenderSpec, render
from dogsapka_clipper.processing.subtitles import (Transcriber, TranscriptionUnavailable, build_captions,
                                               shift_words, transcript_words, write_ass, write_srt)

log = logging.getLogger(__name__)


def seed_profiles(db: Database) -> None:
    with db.session() as s:
        existing = {p.key: p for p in s.scalars(select(EditingProfile))}
        for key, cfg in BUILTIN_PROFILES.items():
            row = existing.get(key)
            if row is None:
                s.add(EditingProfile(key=key, name=cfg["name"], builtin=True, config=resolve_profile(cfg)))
            elif row.builtin:   # built-ins always follow the current code (custom profiles are untouched)
                row.name, row.config = cfg["name"], resolve_profile(cfg)


def load_profile(db: Database, key: str) -> dict:
    with db.session() as s:
        p = s.scalars(select(EditingProfile).where(EditingProfile.key == key)).first()
        if p:
            return resolve_profile({**p.config, "name": p.name})
    return builtin(key)


class ClipProcessor:
    def __init__(self, db: Database, paths: AppPaths, bus: EventBus, settings: Callable[[], AppSettings]) -> None:
        self.db = db
        self.paths = paths
        self.bus = bus
        self.settings = settings
        # OpenCV's detector mutates input size / inference state. Reuse it on each worker,
        # rather than sharing one instance across concurrently processed videos.
        self._face_local = threading.local()

    def _detector(self) -> FaceDetector | None:
        if not self.settings().ai.face_detection:
            return None
        detector = getattr(self._face_local, "detector", None)
        if detector is None:
            detector = self._face_local.detector = FaceDetector()
        return detector

    # ------------------------------------------------------------------
    def handle_transcribe(self, ctx: JobContext) -> None:
        self._transcribe(ctx.clip_id, ctx, force=True)

    def handle_process(self, ctx: JobContext) -> None:
        """Full automatic processing; ``params`` may carry editor overrides."""
        st = self.settings()
        p = ctx.params
        with self.db.session() as s:
            clip = s.get(Clip, ctx.clip_id)
            if clip is None:
                raise RuntimeError("Clip no longer exists")
            clip.edit_status = "processing"
            src = Path(clip.source_path)
            edit = dict(clip.edit_state or {})
            streamer = clip.streamer
            sname, slogin = streamer.display_name or streamer.login, streamer.login
            hl = clip.highlight
            reasons = list(hl.reasons) if hl else [clip.reason]
            signals = dict(hl.signals) if hl else {}
            peak_rel = edit.get("peak_rel")
            sess_cat = hl.session.category if hl and hl.session else streamer.category
            stream_title = hl.session.title if hl and hl.session else ""
            det_score = clip.detection_score
        if not src.exists():
            raise FFmpegError(f"Source clip file is missing: {src}")
        edit.update({k: v for k, v in (p.get("edit") or {}).items()})
        profile = load_profile(self.db, p.get("profile") or edit.get("profile") or st.output.profile)
        profile.update(p.get("profile_overrides") or edit.get("profile_overrides") or {})

        # 1. analysis ------------------------------------------------------
        ctx.progress(0.02, "Analyzing clip")
        info = probe(src)
        an_dict = edit.get("analysis")
        if an_dict and an_dict.get("version") == 3 and not p.get("reanalyze"):  # re-run older analyses
            analysis = LayoutAnalysis.from_dict(an_dict)
        else:
            try:
                analysis = analyze_layout(src, self._detector(), detect_faces=st.ai.face_detection)
            except Exception as e:
                log.warning("Layout analysis failed (%s); using centred crop", e)
                analysis = LayoutAnalysis("gameplay", info.width, info.height)
            edit["analysis"] = {**analysis.to_dict(), "version": 3}
        ctx.progress(0.1, "Analyzing clip")

        # 2. transcription ----------------------------------------------------
        transcript = None
        if profile["subtitle_style"] != "none" and edit.get("subtitles", True):
            transcript = self._transcribe(ctx.clip_id, ctx, force=bool(p.get("retranscribe")))
        ctx.progress(0.35, "Choosing layout")

        # 3. layout ---------------------------------------------------------
        layout = edit.get("layout") or profile["layout"]
        if profile["aspect"] == "source":
            layout = "source"
        elif layout == "auto":
            layout = analysis.recommended_layout(profile.get("fallback_layout", "crop"))
        if layout == "facecam_top" and not (edit.get("facecam") or analysis.facecam):
            layout = "camera" if analysis.kind == "camera" else "crop"

        trim_start = float(edit.get("trim_start", 0.0))
        trim_end = float(edit.get("trim_end") or info.duration)
        trim_end = min(trim_end, info.duration)
        length = max(1.0, trim_end - trim_start)

        # 4. silence handling ------------------------------------------------
        keep = None
        if profile.get("trim_silence") and edit.get("trim_silence", True) and info.has_audio:
            prot = None
            if peak_rel is not None:
                prot = (max(0.0, peak_rel - trim_start - 6), peak_rel - trim_start + 6)
            try:
                keep = clip_analysis.silence_keep_ranges(src, trim_start, length, protect=prot)
            except FFmpegError:
                keep = None
        out_len = sum(b - a for a, b in keep) if keep else length

        # 5. subtitles ---------------------------------------------------------
        o = st.output
        W, H = (o.width, o.height)
        stem = safe_filename(f"{slogin}_{ctx.clip_id}_{profile.get('name', 'export')}")
        sub_dir = self.paths.subtitles
        ass_path = srt_path = None
        if transcript and transcript.get("segments"):
            words = shift_words(transcript_words(transcript), trim_start, length, keep)
            if words and st.ai.censor_profanity:
                from dogsapka_clipper.processing.profanity import mask_words
                words = mask_words(words)
            if words:
                if profile.get("export_srt", True):
                    srt_path = write_srt(build_captions(words, max_words=7, max_chars=42), sub_dir / f"{stem}.srt")
                if profile["subtitle_style"] != "none":
                    out_w, out_h = (W, H) if layout != "source" else (info.width or W, info.height or H)
                    if layout == "source":
                        k = min(1.0, max(W, H) / max(out_w, out_h))
                        out_w, out_h = int(out_w * k) // 2 * 2, int(out_h * k) // 2 * 2
                    ass_path = write_ass(words, sub_dir / f"{stem}.ass", out_w, out_h,
                                         style=edit.get("subtitle_style") or profile["subtitle_style"],
                                         accent=edit.get("accent_color") or profile["accent_color"],
                                         position=edit.get("subtitle_position") or profile["subtitle_position"],
                                         font=profile.get("font", "Arial Black"),
                                         font_size=int(profile.get("font_size") or 0))

        # 6. render ------------------------------------------------------------
        out_dir = Path(o.output_dir) if o.output_dir else self.paths.exports
        out_path = unique_path(out_dir / f"{stem}.mp4")
        encoder = choose_encoder(o.encoder)
        zoom_at = None
        if profile.get("zoom") and peak_rel is not None:
            z = peak_rel - trim_start
            if keep:
                from dogsapka_clipper.processing.subtitles import remap_time
                z = remap_time(peak_rel - trim_start, keep)
            if z is not None and 0 <= z <= out_len:
                zoom_at = z
        fc = edit.get("facecam")
        spec = RenderSpec(
            src=src, out=out_path, width=W, height=H, fps=o.fps, encoder=encoder,
            crf=max(10, o.crf + int(profile.get("quality_crf_offset", 0))), video_bitrate_k=o.video_bitrate_k,
            audio_bitrate_k=o.audio_bitrate_k, trim_start=trim_start, trim_end=trim_end, layout=layout,
            aspect=profile["aspect"], analysis=analysis,
            crop_center_x=edit.get("crop_center_x"), facecam=Rect(**fc) if fc else None,
            facecam_ratio=float(edit.get("facecam_ratio") or profile["facecam_ratio"]),
            dynamic_focus=bool(profile.get("dynamic_focus", False)), keep_ranges=keep,
            ass_path=ass_path if (profile.get("burn_subtitles", True) and edit.get("burn_subtitles", True)) else None,
            fonts_dir=Path(__file__).resolve().parents[1] / "resources" / "fonts",
            zoom_at=zoom_at, zoom_strength=float(profile.get("zoom_strength", 0.08)),
            normalize_audio=bool(edit.get("normalize_audio", profile["normalize_audio"])),
            volume=float(edit.get("volume", 1.0)), fade=profile.get("transition") == "fade")
        with self.db.session() as s:
            c = s.get(Clip, ctx.clip_id)
            c.export_status = "rendering"
            c.layout = layout
            c.edit_state = edit
        self.bus.emit("render.started", "", clip_id=ctx.clip_id)

        def rprog(f: float) -> None:
            ctx.progress(0.4 + 0.5 * f, f"Rendering ({encoder})")
        try:
            render(spec, progress=rprog, cancel=ctx.cancel, pause=ctx.pause)
        except FFmpegError as e:
            if encoder != "libx264" and "Cancelled" not in str(e):
                log.warning("GPU encoding failed (%s); retrying on CPU", e)
                spec.encoder = encoder = "libx264"
                render(spec, progress=rprog, cancel=ctx.cancel, pause=ctx.pause)
            else:
                raise

        # 7. QC ------------------------------------------------------------------
        ctx.progress(0.92, "Quality check")
        exp_w, exp_h = (W, H)
        if layout == "source":
            from dogsapka_clipper.processing.render import output_size
            exp_w, exp_h = output_size(spec, info)
        qc = check_video(out_path, exp_w, exp_h, out_len, expect_audio=info.has_audio)

        # 8. metadata & ranking ----------------------------------------------------
        ctx.progress(0.97, "Generating metadata")
        meta = generate_metadata(sname, slogin, sess_cat or "", reasons, transcript,
                                 lang=st.ai.metadata_language, style=st.ai.title_style, stream_title=stream_title)
        dup = self._find_duplicate(ctx.clip_id)
        q, parts = quality_score(det_score, signals, transcript, out_len, info.width, info.height, duplicate=dup is not None)
        with self.db.session() as s:
            c = s.get(Clip, ctx.clip_id)
            ev = ExportedVideo(clip_id=c.id, path=str(out_path), srt_path=str(srt_path or ""),
                               ass_path=str(ass_path or "") if profile.get("export_ass") or ass_path else "",
                               profile=profile.get("name", ""), width=qc.width, height=qc.height, fps=qc.fps,
                               encoder=encoder, size_bytes=qc.size_bytes, qc_passed=qc.passed,
                               qc_report=qc.to_dict())
            s.add(ev)
            s.flush()
            export_id = ev.id
            c.edit_status = "done"
            c.export_status = "done" if qc.passed else "error"
            c.quality_score = q
            c.duplicate_of = dup
            ed = dict(c.edit_state or {})
            ed["quality_parts"] = parts
            ed["metadata"] = meta
            c.edit_state = ed
            if not c.title or p.get("refresh_metadata", True):
                c.title, c.description, c.hashtags = meta["title"], meta["description"], meta["hashtags"]
        if qc.passed:
            self.bus.emit("video.exported", f"Vertical video exported: {out_path.name}", "success",
                          clip_id=ctx.clip_id, path=str(out_path), export_id=export_id, duplicate=dup is not None)
        else:
            self.bus.emit("video.qc_failed", f"Quality check found problems in {out_path.name}: "
                                             f"{'; '.join(qc.issues)}", "warning", clip_id=ctx.clip_id)
        ctx.progress(1.0, "Completed")

    # ------------------------------------------------------------------
    def _transcribe(self, clip_id: int, ctx: JobContext, force: bool = False) -> dict | None:
        st = self.settings()
        with self.db.session() as s:
            c = s.get(Clip, clip_id)
            existing = dict(c.transcript or {})
            src = Path(c.source_path)
        if existing.get("segments") is not None and not force:
            return existing
        if not st.ai.transcription:
            return existing or None
        ctx.progress(0.12, "Transcribing speech")
        tr = Transcriber(st.ai.whisper_model, st.ai.whisper_device, self.paths.models)
        try:
            result = tr.transcribe(src, st.ai.subtitle_language, cancel=ctx.cancel,
                                   progress=lambda f: ctx.progress(0.12 + 0.22 * f, "Transcribing speech"))
        except TranscriptionUnavailable as e:
            self.bus.emit("ai.unavailable", f"Subtitles skipped: {e}", "warning", clip_id=clip_id)
            return existing or None
        with self.db.session() as s:
            s.get(Clip, clip_id).transcript = result
        low = sum(1 for sg in result["segments"] if sg.get("confidence", 1) < 0.55)
        self.bus.emit("ai.subtitles", f"AI subtitles generated ({result['language']}, {len(result['segments'])} lines"
                                      + (f", {low} uncertain" if low else "") + ")", "success", clip_id=clip_id)
        return result

    def _find_duplicate(self, clip_id: int) -> int | None:
        """Return the id of an earlier clip covering the same moment, if any."""
        with self.db.session() as s:
            c = s.get(Clip, clip_id)
            if c is None or c.highlight is None:
                return None
            r = (c.highlight.start_ts, c.highlight.end_ts)
            others = s.execute(select(Clip.id, Highlight.start_ts, Highlight.end_ts)
                               .join(Highlight, Clip.highlight_id == Highlight.id)
                               .where(Clip.streamer_id == c.streamer_id, Clip.id < clip_id,
                                      Highlight.session_id == c.highlight.session_id)).all()
        for oid, a, b in others:
            if clip_analysis.is_duplicate(r, (a, b)):
                return oid
        return None
