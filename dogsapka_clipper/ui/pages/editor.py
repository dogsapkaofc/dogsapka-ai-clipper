from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QHeaderView)
from sqlalchemy import select

from dogsapka_clipper.core.config import apply_preset
from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Clip, EditingProfile
from dogsapka_clipper.processing.layout import LayoutAnalysis, Rect
from dogsapka_clipper.processing.metadata import generate_metadata
from dogsapka_clipper.processing.pipeline import load_profile
from dogsapka_clipper.processing.render import RenderSpec, render_preview_frame
from dogsapka_clipper.processing.subtitles import LOW_CONFIDENCE, shift_words, transcript_words, write_ass
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.pages.library import open_path
from dogsapka_clipper.ui.widgets.common import Card, Pill, TimelineBar, label

LAYOUTS = [("auto", "Auto (facecam → facecam top + game below)"), ("facecam_top", "Facecam top + gameplay"), ("crop", "Smart crop"),
           ("camera", "Streamer camera focus"), ("blur", "Blurred background (fit)"), ("source", "Original aspect")]
STYLES = [("viral", "A · Viral Gaming"), ("clean", "B · Clean Professional"), ("dynamic", "C · Dynamic TikTok"),
          ("minimal", "D · Minimal"), ("boxed", "E · Boxed"), ("neon", "F · Neon"), ("none", "No subtitles")]


def _player():
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        return QMediaPlayer, QAudioOutput, QVideoWidget
    except Exception:
        return None


class EditorPage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.editor"), "Trim, re-frame, subtitle and export", scroll=False)
        self.clip_id: int | None = None
        self.duration = 0.0
        self.analysis: LayoutAnalysis | None = None
        self.transcript: dict = {}
        self.src: Path | None = None
        self.active_job: int | None = None

        self.empty = label(tr("ed.no_clip"), "Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.empty)
        self.main = QWidget()
        self.body.addWidget(self.main, 1)
        h = QHBoxLayout(self.main)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)

        # ---- left: preview + timeline ------------------------------------------------
        left = QVBoxLayout()
        pc = Card()
        self.player = None
        mm = _player()
        if mm:
            QMediaPlayer, QAudioOutput, QVideoWidget = mm
            try:
                self.video = QVideoWidget()
                self.video.setMinimumSize(560, 315)
                self.video.setStyleSheet("background: black; border-radius: 10px;")
                self.player = QMediaPlayer(self)
                self.audio_out = QAudioOutput(self)
                self.player.setAudioOutput(self.audio_out)
                self.player.setVideoOutput(self.video)
                self.player.positionChanged.connect(self._pos_changed)
                pc.lay.addWidget(self.video, 1)
            except Exception:
                self.player = None
        if self.player is None:
            self.video = QLabel("Video playback unavailable - frame preview only")
            self.video.setAlignment(Qt.AlignCenter)
            self.video.setMinimumSize(560, 315)
            pc.lay.addWidget(self.video, 1)
        ctr = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(icon("play"))
        self.play_btn.clicked.connect(self.toggle_play)
        ctr.addWidget(self.play_btn)
        self.pos_lbl = label("0:00.0 / 0:00.0", "Muted")
        ctr.addWidget(self.pos_lbl)
        ctr.addStretch()
        self.reason_pill = Pill("", theme.PURPLE2)
        ctr.addWidget(self.reason_pill)
        pc.lay.addLayout(ctr)
        self.timeline = TimelineBar(editable=True)
        self.timeline.rangeChanged.connect(self._range_from_bar)
        self.timeline.seek.connect(self.seek)
        pc.lay.addWidget(self.timeline)
        trim = QHBoxLayout()
        trim.addWidget(label(tr("ed.start"), "Muted"))
        self.t_start = QDoubleSpinBox()
        self.t_start.setDecimals(1)
        self.t_start.setSuffix(" s")
        self.t_start.valueChanged.connect(self._range_from_spin)
        trim.addWidget(self.t_start)
        trim.addWidget(label(tr("ed.end"), "Muted"))
        self.t_end = QDoubleSpinBox()
        self.t_end.setDecimals(1)
        self.t_end.setSuffix(" s")
        self.t_end.valueChanged.connect(self._range_from_spin)
        trim.addWidget(self.t_end)
        self.len_lbl = label("", "Muted")
        trim.addWidget(self.len_lbl)
        trim.addStretch()
        set_in = QPushButton("Set start here")
        set_in.clicked.connect(lambda: self.t_start.setValue(self._cur_pos()))
        set_out = QPushButton("Set end here")
        set_out.clicked.connect(lambda: self.t_end.setValue(self._cur_pos()))
        trim.addWidget(set_in)
        trim.addWidget(set_out)
        pc.lay.addLayout(trim)
        left.addWidget(pc, 3)

        ba = Card()
        bh = QHBoxLayout()
        bh.addWidget(label("Before / after", "H2"))
        bh.addStretch()
        prev_btn = QPushButton("  " + tr("ed.preview_frame"))
        prev_btn.setIcon(icon("refresh", theme.CYAN))
        prev_btn.clicked.connect(self.update_preview)
        bh.addWidget(prev_btn)
        ba.lay.addLayout(bh)
        pr = QHBoxLayout()
        self.before = QLabel(tr("ed.before"))
        self.after = QLabel(tr("ed.after"))
        for w in (self.before, self.after):
            w.setAlignment(Qt.AlignCenter)
            w.setMinimumHeight(220)
            w.setStyleSheet(f"background: {theme.BG2}; border-radius: 10px; color: {theme.MUTED};")
        pr.addWidget(self.before, 16)
        pr.addWidget(self.after, 9)
        ba.lay.addLayout(pr)
        left.addWidget(ba, 2)
        h.addLayout(left, 3)

        # ---- right: tabs ------------------------------------------------------------------
        right = Card()
        right.setMinimumWidth(420)
        right.setMaximumWidth(520)
        tabs = QTabWidget()
        right.lay.addWidget(tabs, 1)
        tabs.addTab(self._tab_layout(), tr("ed.layout"))
        tabs.addTab(self._tab_subs(), tr("ed.subtitles"))
        tabs.addTab(self._tab_audio(), "Audio")
        tabs.addTab(self._tab_export(), "Export")
        tabs.addTab(self._tab_meta(), "Metadata")
        h.addWidget(right, 2)
        self.main.setVisible(False)
        win.bridge.event.connect(self.on_event)

    # ---------------------------------------------------------------- tabs
    def _form(self) -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(4, 12, 4, 4)
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignLeft)
        return w, f

    def _tab_layout(self) -> QWidget:
        w, f = self._form()
        self.profile = QComboBox()
        f.addRow(tr("ed.profile"), self.profile)
        self.layout_cb = QComboBox()
        for k, t in LAYOUTS:
            self.layout_cb.addItem(t, k)
        f.addRow(tr("ed.layout"), self.layout_cb)
        self.detected = label("", "Muted", wrap=True)
        f.addRow("", self.detected)
        self.crop_auto = QCheckBox("Automatic focus (follow action / face)")
        self.crop_auto.setChecked(True)
        f.addRow("", self.crop_auto)
        self.crop_x = QSlider(Qt.Horizontal)
        self.crop_x.setRange(0, 100)
        self.crop_x.setValue(50)
        self.crop_x.valueChanged.connect(lambda: self.crop_auto.setChecked(False))
        f.addRow(tr("ed.crop_x"), self.crop_x)
        self.fc = [QSpinBox() for _ in range(4)]
        fr = QHBoxLayout()
        for sb, name in zip(self.fc, "xywh"):
            sb.setRange(0, 7680)
            sb.setPrefix(f"{name} ")
            fr.addWidget(sb)
        f.addRow(tr("ed.facecam"), fr)
        self.fc_ratio = QSlider(Qt.Horizontal)
        self.fc_ratio.setRange(20, 60)
        self.fc_ratio.setValue(38)
        f.addRow("Facecam height %", self.fc_ratio)
        re = QPushButton("  Re-analyze layout")
        re.setIcon(icon("sparkles", theme.CYAN))
        re.clicked.connect(self.reanalyze)
        f.addRow("", re)
        return w

    def _tab_subs(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 12, 4, 4)
        f = QFormLayout()
        self.sub_style = QComboBox()
        for k, t in STYLES:
            self.sub_style.addItem(t, k)
        f.addRow(tr("ed.sub_style"), self.sub_style)
        self.sub_pos = QComboBox()
        for k, t in (("lower", "Lower (above platform UI)"), ("middle", "Middle"), ("upper", "Upper")):
            self.sub_pos.addItem(t, k)
        f.addRow("Position", self.sub_pos)
        self.accent = QPushButton("#FFE600")
        self.accent.clicked.connect(self.pick_color)
        f.addRow("Accent colour", self.accent)
        self.burn = QCheckBox("Burn subtitles into video (SRT is always exported)")
        self.burn.setChecked(True)
        f.addRow("", self.burn)
        v.addLayout(f)
        th = QHBoxLayout()
        self.lang_lbl = label("", "Muted")
        th.addWidget(self.lang_lbl)
        th.addStretch()
        tb = QPushButton("  " + tr("ed.transcribe"))
        tb.setIcon(icon("sparkles", theme.CYAN))
        tb.clicked.connect(self.transcribe)
        th.addWidget(tb)
        v.addLayout(th)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Text", "Conf."])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        v.addWidget(self.table, 1)
        v.addWidget(label("Amber rows = low recognition confidence; please check them. Edit text directly.",
                          "Muted", wrap=True))
        save = QPushButton("Save subtitle corrections")
        save.clicked.connect(self.save_transcript)
        v.addWidget(save)
        return w

    def _tab_audio(self) -> QWidget:
        w, f = self._form()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 200)
        self.volume.setValue(100)
        self.vol_lbl = label("100%", "Muted")
        self.volume.valueChanged.connect(lambda v: self.vol_lbl.setText(f"{v}%"))
        r = QHBoxLayout()
        r.addWidget(self.volume)
        r.addWidget(self.vol_lbl)
        f.addRow(tr("ed.volume"), r)
        self.normalize = QCheckBox("EBU R128 loudness normalization (-14 LUFS)")
        self.normalize.setChecked(True)
        f.addRow(tr("ed.normalize"), self.normalize)
        self.trim_sil = QCheckBox("Shorten long silences (never cuts the highlight itself)")
        f.addRow("Silence", self.trim_sil)
        self.zoom = QCheckBox("Subtle zoom on the detected peak")
        f.addRow("Zoom", self.zoom)
        self.fade = QCheckBox("Fade in / out")
        f.addRow("Transition", self.fade)
        return w

    def _tab_export(self) -> QWidget:
        w, f = self._form()
        self.preset = QComboBox()
        for k, t in (("standard", "Standard · 720×1280 · 30 fps"), ("high", "High · 1080×1920 · 30 fps"),
                     ("ultra", "Ultra · 1080×1920 · 60 fps"), ("custom", "Custom (Settings → Video output)")):
            self.preset.addItem(t, k)
        f.addRow("Preset", self.preset)
        self.enc_lbl = label("", "Muted", wrap=True)
        f.addRow("Encoder", self.enc_lbl)
        f.addRow("", label("Frame rate never exceeds the source - missing motion or detail is not invented.",
                           "Muted", wrap=True))
        rb = QPushButton("  " + tr("ed.render"))
        rb.setObjectName("Primary")
        rb.setIcon(icon("film"))
        rb.clicked.connect(self.render_now)
        f.addRow("", rb)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        f.addRow("", self.progress)
        self.stage = label("", "Muted", wrap=True)
        f.addRow("", self.stage)
        self.exports_lbl = label("", "Muted", wrap=True)
        f.addRow("Exports", self.exports_lbl)
        ob = QPushButton("  " + tr("ed.export_folder"))
        ob.setIcon(icon("folder"))
        ob.clicked.connect(lambda: open_path(str(Path(self.backend.settings.output.output_dir or self.backend.paths.exports))))
        f.addRow("", ob)
        return w

    def _tab_meta(self) -> QWidget:
        w, f = self._form()
        self.title_style = QComboBox()
        for k in ("auto", "funny", "dramatic", "gaming", "reaction", "professional"):
            self.title_style.addItem(k.capitalize(), k)
        self.meta_lang = QComboBox()
        self.meta_lang.addItem("English", "en")
        self.meta_lang.addItem("Magyar", "hu")
        r = QHBoxLayout()
        r.addWidget(self.title_style)
        r.addWidget(self.meta_lang)
        gen = QPushButton("Suggest")
        gen.clicked.connect(self.suggest_meta)
        r.addWidget(gen)
        f.addRow("Style", r)
        self.title_alts = QComboBox()
        self.title_alts.activated.connect(lambda i: self.title_edit.setText(self.title_alts.itemText(i)))
        f.addRow("Suggestions", self.title_alts)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(100)
        f.addRow("Title", self.title_edit)
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMinimumHeight(140)
        f.addRow("Description", self.desc_edit)
        self.tags_edit = QLineEdit()
        f.addRow("Hashtags", self.tags_edit)
        f.addRow("", label("Generated only from the transcript and detected events - review before publishing.",
                           "Muted", wrap=True))
        row = QHBoxLayout()
        sv = QPushButton("Save")
        sv.clicked.connect(self.save_meta)
        row.addWidget(sv)
        pb = QPushButton("  " + tr("ed.publish"))
        pb.setObjectName("Primary")
        pb.setIcon(icon("upload"))
        pb.clicked.connect(self.publish)
        row.addWidget(pb)
        f.addRow("", row)
        return w

    # ---------------------------------------------------------------- loading
    def load_clip(self, clip_id: int) -> None:
        with self.backend.db.session() as s:
            c = s.get(Clip, clip_id)
            if c is None:
                return
            self.clip_id = clip_id
            self.src = Path(c.source_path)
            self.duration = c.duration
            ed = dict(c.edit_state or {})
            self.transcript = dict(c.transcript or {})
            self.analysis = LayoutAnalysis.from_dict(ed["analysis"]) if ed.get("analysis") else None
            self.reason_pill.set(f"{c.detection_score:.0f}/100 · {c.reason[:60]}", theme.PURPLE2)
            self.title_edit.setText(c.title)
            self.desc_edit.setPlainText(c.description)
            self.tags_edit.setText(" ".join(c.hashtags or []))
            meta = ed.get("metadata") or {}
            self.title_alts.clear()
            self.title_alts.addItems(meta.get("titles", []))
            exports = [(e.path, e.qc_passed, e.profile) for e in c.exports]
            profiles = [(p.key, p.name) for p in s.scalars(select(EditingProfile).order_by(EditingProfile.id))]
        self.empty.setVisible(False)
        self.main.setVisible(True)
        st = self.backend.settings
        self.profile.blockSignals(True)
        self.profile.clear()
        for k, n in profiles:
            self.profile.addItem(n, k)
        self.profile.setCurrentIndex(max(0, self.profile.findData(ed.get("profile") or st.output.profile)))
        self.profile.blockSignals(False)
        prof = load_profile(self.backend.db, self.profile.currentData())
        self._set_combo(self.layout_cb, ed.get("layout") or prof["layout"])
        self._set_combo(self.sub_style, ed.get("subtitle_style") or prof["subtitle_style"])
        self._set_combo(self.sub_pos, ed.get("subtitle_position") or prof["subtitle_position"])
        self._set_accent(ed.get("accent_color") or prof["accent_color"])
        self.burn.setChecked(ed.get("burn_subtitles", True))
        self.crop_auto.setChecked(ed.get("crop_center_x") is None)
        self.crop_x.blockSignals(True)
        self.crop_x.setValue(int(100 * (ed.get("crop_center_x") or 0.5)))
        self.crop_x.blockSignals(False)
        fc = ed.get("facecam") or (self.analysis.facecam.__dict__ if self.analysis and self.analysis.facecam else None)
        for sb, k in zip(self.fc, "xywh"):
            sb.setValue(int(fc[k]) if fc else 0)
        self.fc_ratio.setValue(int(100 * float(ed.get("facecam_ratio") or prof["facecam_ratio"])))
        self.volume.setValue(int(100 * float(ed.get("volume", 1.0))))
        self.normalize.setChecked(bool(ed.get("normalize_audio", prof["normalize_audio"])))
        self.trim_sil.setChecked(bool(ed.get("profile_overrides", {}).get("trim_silence", prof["trim_silence"])))
        self.zoom.setChecked(bool(ed.get("profile_overrides", {}).get("zoom", prof["zoom"])))
        self.fade.setChecked(ed.get("profile_overrides", {}).get("transition", prof["transition"]) == "fade")
        self._set_combo(self.preset, st.output.preset)
        self._set_combo(self.title_style, st.ai.title_style)
        self._set_combo(self.meta_lang, st.ai.metadata_language)
        from dogsapka_clipper.processing.ffmpeg_utils import ENCODER_LABELS, choose_encoder
        run_async(lambda: choose_encoder(st.output.encoder),
                  lambda e: self.enc_lbl.setText(ENCODER_LABELS.get(e, e)))
        for sb in (self.t_start, self.t_end):
            sb.blockSignals(True)
            sb.setRange(0, self.duration)
        self.t_start.setValue(float(ed.get("trim_start", 0.0)))
        self.t_end.setValue(float(ed.get("trim_end") or self.duration))
        for sb in (self.t_start, self.t_end):
            sb.blockSignals(False)
        self.timeline.set_duration(self.duration)
        self.timeline.set_range(self.t_start.value(), self.t_end.value())
        peak = ed.get("peak_rel")
        self.timeline.markers = [(peak, 100)] if peak is not None else []
        self._update_len()
        self._fill_transcript()
        self._fill_exports(exports)
        if self.analysis:
            self.detected.setText(f"Detected: {self.analysis.kind} · {self.analysis.faces_found} face detections · "
                                  f"recommended '{self.analysis.recommended_layout()}'")
        else:
            self.detected.setText("Layout not analysed yet - it is analysed automatically on render.")
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(str(self.src)))
            self.player.setPosition(int(self.t_start.value() * 1000))
        self._load_waveform()
        QTimer.singleShot(50, self.update_preview)
        if ed.get("analysis", {}).get("version") != 3 and not ed.get("facecam"):
            self.reanalyze()

    def _load_waveform(self) -> None:
        src = self.src

        def job():
            import numpy as np
            from dogsapka_clipper.processing.ffmpeg_utils import decode_audio_pcm
            pcm = decode_audio_pcm(src, sample_rate=8000)
            if pcm.size == 0:
                return []
            n = 300
            k = max(1, pcm.size // n)
            env = np.sqrt(np.mean(pcm[: k * n].reshape(-1, k) ** 2, axis=1))
            return list(env / (env.max() + 1e-9))
        run_async(job, lambda w: (setattr(self.timeline, "waveform", w), self.timeline.update()))

    def _set_combo(self, cb: QComboBox, key) -> None:
        i = cb.findData(key)
        if i >= 0:
            cb.setCurrentIndex(i)

    def _set_accent(self, c: str) -> None:
        self.accent.setText(c)
        self.accent.setStyleSheet(f"border: 2px solid {c};")

    def pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.accent.text()), self)
        if c.isValid():
            self._set_accent(c.name().upper())

    def _fill_transcript(self) -> None:
        segs = self.transcript.get("segments", [])
        self.lang_lbl.setText(f"Language: {self.transcript.get('language', '—')} · {len(segs)} lines"
                              if segs else "No transcript yet")
        self.table.setRowCount(len(segs))
        for i, s in enumerate(segs):
            vals = [f"{s['start']:.2f}", f"{s['end']:.2f}", s.get("text", ""), f"{s.get('confidence', 1):.0%}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 3:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if s.get("confidence", 1) < LOW_CONFIDENCE:
                    it.setBackground(QColor(90, 70, 10))
                self.table.setItem(i, j, it)

    def _fill_exports(self, exports) -> None:
        lines = [f"{'✔' if ok else '⚠'} {Path(p).name}  ({prof})" for p, ok, prof in exports[-5:]]
        self.exports_lbl.setText("\n".join(lines) or "Not exported yet")

    # ---------------------------------------------------------------- playback/trim
    def toggle_play(self) -> None:
        if not self.player:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setIcon(icon("play"))
        else:
            if self._cur_pos() >= self.t_end.value() - 0.1:
                self.seek(self.t_start.value())
            self.player.play()
            self.play_btn.setIcon(icon("pause"))

    def _cur_pos(self) -> float:
        if self.player:
            return self.player.position() / 1000.0
        return self.timeline.position or self.t_start.value()

    def seek(self, t: float) -> None:
        if self.player:
            self.player.setPosition(int(t * 1000))
        self.timeline.position = t
        self.timeline.update()
        self.pos_lbl.setText(f"{t:.1f}s / {self.duration:.1f}s")

    def _pos_changed(self, ms: int) -> None:
        t = ms / 1000.0
        self.timeline.position = t
        self.timeline.update()
        self.pos_lbl.setText(f"{t:.1f}s / {self.duration:.1f}s")
        if t >= self.t_end.value() and self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setIcon(icon("play"))

    def _range_from_bar(self, a: float, b: float) -> None:
        for sb, v in ((self.t_start, a), (self.t_end, b)):
            sb.blockSignals(True)
            sb.setValue(v)
            sb.blockSignals(False)
        self._update_len()

    def _range_from_spin(self) -> None:
        if self.t_end.value() - self.t_start.value() < 1:
            return
        self.timeline.set_range(self.t_start.value(), self.t_end.value())
        self._update_len()

    def _update_len(self) -> None:
        self.len_lbl.setText(f"→ {self.t_end.value() - self.t_start.value():.1f}s")

    # ---------------------------------------------------------------- edit state
    def edit_state(self) -> dict:
        ed = {"trim_start": round(self.t_start.value(), 2), "trim_end": round(self.t_end.value(), 2),
              "layout": self.layout_cb.currentData(), "subtitle_style": self.sub_style.currentData(),
              "subtitle_position": self.sub_pos.currentData(), "accent_color": self.accent.text(),
              "burn_subtitles": self.burn.isChecked(), "subtitles": self.sub_style.currentData() != "none",
              "volume": self.volume.value() / 100.0, "normalize_audio": self.normalize.isChecked(),
              "facecam_ratio": self.fc_ratio.value() / 100.0, "profile": self.profile.currentData(),
              "crop_center_x": None if self.crop_auto.isChecked() else self.crop_x.value() / 100.0,
              "profile_overrides": {"trim_silence": self.trim_sil.isChecked(), "zoom": self.zoom.isChecked(),
                                    "transition": "fade" if self.fade.isChecked() else "none"}}
        vals = [sb.value() for sb in self.fc]
        if vals[2] > 0 and vals[3] > 0:
            ed["facecam"] = dict(zip("xywh", vals))
        return ed

    def _spec_for_preview(self, ass: Path | None) -> RenderSpec:
        ed = self.edit_state()
        st = self.backend.settings.output
        layout = ed["layout"]
        an = self.analysis or LayoutAnalysis("gameplay", 0, 0)
        if layout == "auto":
            layout = an.recommended_layout(load_profile(self.backend.db, ed["profile"]).get("fallback_layout", "crop"))
        fc = Rect(**ed["facecam"]) if ed.get("facecam") else None
        if layout == "facecam_top" and not (fc or an.facecam):
            layout = "crop"
        w = 360
        return RenderSpec(src=self.src, out=Path("x"), width=w, height=w * st.height // st.width, layout=layout,
                          aspect="source" if layout == "source" else "9:16", analysis=an,
                          crop_center_x=ed["crop_center_x"], facecam=fc, facecam_ratio=ed["facecam_ratio"], ass_path=ass)

    def update_preview(self) -> None:
        if not self.src:
            return
        t = self._cur_pos() if self.player else (self.t_start.value() + self.t_end.value()) / 2
        if t <= 0.05:
            t = min(self.duration * 0.5, self.t_start.value() + 2)
        tmp = self.backend.paths.temp
        ass = None
        if self.transcript.get("segments") and self.sub_style.currentData() != "none":
            spec0 = self._spec_for_preview(None)
            words = shift_words(transcript_words(self.transcript), 0.0, self.duration)
            if self.backend.settings.ai.censor_profanity:
                from dogsapka_clipper.processing.profanity import mask_words
                words = mask_words(words)
            ass = write_ass(words, tmp / "preview.ass", spec0.width, spec0.height, self.sub_style.currentData(),
                            self.accent.text(), self.sub_pos.currentData())
        spec = self._spec_for_preview(ass)
        from dogsapka_clipper.processing.ffmpeg_utils import extract_frame

        def job():
            b = extract_frame(self.src, t, tmp / "before.jpg", width=640)
            a = render_preview_frame(spec, t, tmp / "after.png")
            return str(b), str(a)

        def done(r):
            for lbl, p in zip((self.before, self.after), r):
                pm = QPixmap(p)
                lbl.setPixmap(pm.scaled(lbl.width() - 8, lbl.height() - 8, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        run_async(job, done, lambda e: self.after.setText(f"Preview failed:\n{e}"))

    def reanalyze(self) -> None:
        if not self.src:
            return
        from dogsapka_clipper.processing.layout import analyze_layout
        self.detected.setText("Analysing…")
        src, cid = self.src, self.clip_id

        def done(an):
            if self.clip_id != cid:
                return
            self.analysis = an
            with self.backend.db.session() as s:
                c = s.get(Clip, cid)
                if c is None:
                    return
                ed = dict(c.edit_state or {})
                ed["analysis"] = {**an.to_dict(), "version": 3}
                c.edit_state = ed
            self.detected.setText(f"Detected: {an.kind} · {an.faces_found} face detections · "
                                  f"recommended '{an.recommended_layout()}'")
            if an.facecam:
                for sb, k in zip(self.fc, "xywh"):
                    sb.setValue(getattr(an.facecam, k))
            self.update_preview()
        run_async(lambda: analyze_layout(src), done, lambda e: self.detected.setText(f"Analysis failed: {e}"))

    def transcribe(self) -> None:
        if self.clip_id:
            self.active_job = self.backend.jobs.submit("transcribe", self.clip_id)
            self.progress.setVisible(True)
            self.stage.setText("Transcription queued…")

    def save_transcript(self) -> None:
        segs = self.transcript.get("segments", [])
        for i, s in enumerate(segs):
            try:
                a = float(self.table.item(i, 0).text())
                b = float(self.table.item(i, 1).text())
            except (ValueError, AttributeError):
                continue
            text = self.table.item(i, 2).text().strip()
            if text != s.get("text") or abs(a - s["start"]) > 1e-3 or abs(b - s["end"]) > 1e-3:
                s.update(start=a, end=max(a + 0.1, b), text=text, confidence=1.0, edited=True)
                s.pop("words", None)   # manual text -> even word timing within the line
        with self.backend.db.session() as ss:
            ss.get(Clip, self.clip_id).transcript = self.transcript
        self._fill_transcript()
        self.update_preview()

    def render_now(self) -> None:
        if not self.clip_id:
            return
        st = self.backend.settings
        preset = self.preset.currentData()
        if preset != "custom" and preset != st.output.preset:
            apply_preset(st.output, preset)
            self.backend.update_settings(st)
        self.active_job = self.backend.render_clip(self.clip_id, self.edit_state(), self.profile.currentData(),
                                                   self.edit_state()["profile_overrides"])
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.stage.setText("Queued…")

    def on_event(self, ev) -> None:
        if ev.data.get("job_id") == self.active_job and self.active_job is not None:
            if ev.kind == "job.progress":
                self.progress.setValue(int(ev.data.get("progress", 0) * 100))
                if ev.data.get("stage"):
                    self.stage.setText(ev.data["stage"])
            elif ev.kind == "job.done":
                self.progress.setValue(100)
                self.stage.setText("Done ✔")
                self.active_job = None
                if self.clip_id:
                    self.load_clip(self.clip_id)
            elif ev.kind == "job.failed":
                self.stage.setText(f"Failed: {ev.message}")
                self.active_job = None

    # ---------------------------------------------------------------- metadata / publishing
    def suggest_meta(self) -> None:
        with self.backend.db.session() as s:
            c = s.get(Clip, self.clip_id)
            st = c.streamer
            hl = c.highlight
            m = generate_metadata(st.display_name or st.login, st.login,
                                  (hl.session.category if hl and hl.session else st.category) or "",
                                  hl.reasons if hl else [c.reason], c.transcript, self.meta_lang.currentData(),
                                  self.title_style.currentData())
        self.title_alts.clear()
        self.title_alts.addItems(m["titles"])
        self.title_edit.setText(m["title"])
        self.desc_edit.setPlainText(m["description"])
        self.tags_edit.setText(" ".join(m["hashtags"]))

    def save_meta(self) -> None:
        with self.backend.db.session() as s:
            c = s.get(Clip, self.clip_id)
            c.title = self.title_edit.text().strip()
            c.description = self.desc_edit.toPlainText()
            c.hashtags = [t if t.startswith("#") else "#" + t for t in self.tags_edit.text().split() if t.strip("#")]

    def publish(self) -> None:
        self.save_meta()
        from dogsapka_clipper.ui.publish_dialog import PublishDialog
        with self.backend.db.session() as s:
            c = s.get(Clip, self.clip_id)
            exports = [e for e in c.exports if e.qc_passed and Path(e.path).exists()]
            if not exports:
                QMessageBox.information(self, "Publish", "Render the video first (Export tab).")
                return
            eid = exports[-1].id
        PublishDialog(self.backend, eid, self).exec()
