from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from dogsapka_clipper.core.config import AppSettings, apply_preset
from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.processing.profiles import BUILTIN_PROFILES
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Card, label


def _section(title: str, desc: str = "") -> tuple[Card, QFormLayout]:
    c = Card()
    c.lay.addWidget(label(title, "H2"))
    if desc:
        c.lay.addWidget(label(desc, "Muted", wrap=True))
    f = QFormLayout()
    f.setSpacing(10)
    f.setLabelAlignment(Qt.AlignLeft)
    c.lay.addLayout(f)
    return c, f


def spin(lo, hi, val, suffix="", step=1, dbl=False):
    s = QDoubleSpinBox() if dbl else QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class DeviceCodeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect Twitch")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.addWidget(label("Connect your Twitch account", "H2"))
        self.msg = label("Requesting a login code from Twitch…", wrap=True)
        v.addWidget(self.msg)
        self.code = QLabel("")
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setStyleSheet(f"font-size: 26pt; font-weight: 800; letter-spacing: 6px; color: {theme.CYAN};")
        v.addWidget(self.code)
        self.open_btn = QPushButton("Open Twitch activation page")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setEnabled(False)
        v.addWidget(self.open_btn)
        v.addWidget(label("DOGSAPKA only requests the 'chat:read' permission. Your password is never seen by DOGSAPKA.",
                          "Muted", wrap=True))
        c = QPushButton(tr("cancel"))
        c.clicked.connect(self.reject)
        v.addWidget(c)


class SettingsPage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.settings"), "Everything is stored locally on this computer")
        save = QPushButton("  " + tr("set.save"))
        save.setObjectName("Primary")
        save.setIcon(icon("check"))
        save.clicked.connect(self.save)
        self.actions.addWidget(save)
        self.w: dict[str, QWidget] = {}
        s = win.backend.settings

        c, f = _section(tr("set.twitch"), "Register a free application at dev.twitch.tv/console (category: "
                                          "Application Integration, client type: Public) and paste its Client ID. "
                                          "Only official Twitch APIs are used.")
        self.w["client_id"] = QLineEdit(s.twitch.client_id)
        f.addRow("Client ID", self.w["client_id"])
        self.w["client_secret"] = QLineEdit()
        self.w["client_secret"].setEchoMode(QLineEdit.Password)
        self.w["client_secret"].setPlaceholderText("optional - stored encrypted, only for app-only access")
        f.addRow("Client Secret", self.w["client_secret"])
        self.w["poll"] = spin(15, 600, s.twitch.poll_interval_s, " s")
        f.addRow("Live check interval", self.w["poll"])
        self.w["use_chat"] = QCheckBox("Read chat (aggregate statistics only, no chat logs are stored)")
        self.w["use_chat"].setChecked(s.twitch.use_chat)
        f.addRow("", self.w["use_chat"])
        row = QHBoxLayout()
        self.tw_status = label("", "Muted")
        row.addWidget(self.tw_status, 1)
        con = QPushButton("  " + tr("set.connect"))
        con.setIcon(icon("radio", theme.PURPLE2))
        con.clicked.connect(self.connect_twitch)
        row.addWidget(con)
        dis = QPushButton("Disconnect")
        dis.clicked.connect(self.disconnect_twitch)
        row.addWidget(dis)
        f.addRow("Account", row)
        self.body.addWidget(c)

        c, f = _section(tr("set.youtube_live"), tr("set.youtube_live_desc"))
        y = s.youtube_live
        self.w["yt_api_key"] = QLineEdit()
        self.w["yt_api_key"].setEchoMode(QLineEdit.Password)
        self.w["yt_api_key"].setPlaceholderText(tr("set.yt_key_set") if win.backend.youtube_api_key()
                                                else "AIza…  (stored encrypted)")
        f.addRow(tr("set.yt_api_key"), self.w["yt_api_key"])
        self.w["yt_poll"] = spin(30, 3600, y.poll_interval_s, " s")
        f.addRow("Live check interval", self.w["yt_poll"])
        self.w["yt_chat"] = QCheckBox(tr("set.yt_chat"))
        self.w["yt_chat"].setChecked(y.use_chat)
        f.addRow("", self.w["yt_chat"])
        self.w["yt_chat_poll"] = spin(10, 120, y.chat_poll_s, " s")
        f.addRow(tr("set.yt_chat_poll"), self.w["yt_chat_poll"])
        self.body.addWidget(c)

        c, f = _section(tr("set.recording"))
        r = s.recording
        self.w["rec_enabled"] = QCheckBox("Record a rolling buffer while authorized streamers are live")
        self.w["rec_enabled"].setChecked(r.enabled)
        f.addRow("", self.w["rec_enabled"])
        self.w["quality"] = QComboBox()
        for q in ("best", "1080p60", "1080p", "720p60", "720p", "480p"):
            self.w["quality"].addItem(q)
        self.w["quality"].setCurrentText(r.quality)
        f.addRow("Recording quality", self.w["quality"])
        self.w["buffer"] = spin(30, 300, r.buffer_seconds, " s")
        f.addRow("Rolling buffer", self.w["buffer"])
        self.w["pre"] = spin(0, 120, r.pre_event_s, " s")
        f.addRow("Before event", self.w["pre"])
        self.w["post"] = spin(0, 120, r.post_event_s, " s")
        f.addRow("After event", self.w["post"])
        self.w["min"] = spin(5, 180, r.min_clip_s, " s")
        f.addRow("Minimum clip", self.w["min"])
        self.w["max"] = spin(15, 180, r.max_clip_s, " s")
        f.addRow("Maximum clip", self.w["max"])
        self.w["storage"] = spin(5, 5000, r.max_storage_gb, " GB", dbl=True)
        f.addRow("Storage warning limit", self.w["storage"])
        self.w["cleanup"] = spin(1, 60, r.cleanup_recordings_days, " days")
        f.addRow("Delete leftover recordings after", self.w["cleanup"])
        f.addRow("Data folder", label(str(win.backend.paths.root), "Muted"))
        self.body.addWidget(c)

        c, f = _section(tr("set.detection"), "Weights are normalised over the enabled methods. Baselines adapt to "
                                              "each streamer automatically.")
        d = s.detection
        self.w["sens"] = QSlider(Qt.Horizontal)
        self.w["sens"].setRange(0, 100)
        self.w["sens"].setValue(int(d.sensitivity * 100))
        f.addRow("AI sensitivity", self.w["sens"])
        for key, text, on, wt in (("chat_activity", "Chat activity spikes", d.use_chat_activity, d.weight_chat_activity),
                                  ("chat_reactions", "Chat reactions (EN/HU)", d.use_chat_reactions, d.weight_chat_reactions),
                                  ("audio", "Audio excitement", d.use_audio, d.weight_audio),
                                  ("visual", "Visual changes (more CPU)", d.use_visual, d.weight_visual)):
            h = QHBoxLayout()
            cb = QCheckBox(text)
            cb.setChecked(on)
            sp = spin(0, 100, wt, " %", dbl=True)
            h.addWidget(cb, 1)
            h.addWidget(sp)
            self.w[f"use_{key}"], self.w[f"w_{key}"] = cb, sp
            f.addRow("", h)
        self.w["threshold"] = spin(30, 99, d.threshold, dbl=True)
        f.addRow("Highlight threshold (0-100)", self.w["threshold"])
        self.w["auto_thr"] = QCheckBox("Automatically raise the threshold when too many highlights are detected")
        self.w["auto_thr"].setChecked(d.auto_threshold)
        f.addRow("", self.w["auto_thr"])
        self.w["cooldown"] = spin(0, 300, d.cooldown_s, " s")
        f.addRow("Cooldown", self.w["cooldown"])
        self.w["merge"] = spin(0, 120, d.merge_window_s, " s")
        f.addRow("Merge window", self.w["merge"])
        self.body.addWidget(c)

        c, f = _section(tr("set.output"))
        o = s.output
        self.w["preset"] = QComboBox()
        for k, t in (("standard", "Standard · 720×1280 · 30 fps"), ("high", "High · 1080×1920 · 30 fps"),
                     ("ultra", "Ultra · 1080×1920 · 60 fps"), ("custom", "Custom")):
            self.w["preset"].addItem(t, k)
        self.w["preset"].setCurrentIndex(self.w["preset"].findData(o.preset))
        f.addRow("Export preset", self.w["preset"])
        wh = QHBoxLayout()
        self.w["width"] = spin(240, 3840, o.width)
        self.w["height"] = spin(240, 3840, o.height)
        wh.addWidget(self.w["width"])
        wh.addWidget(QLabel("×"))
        wh.addWidget(self.w["height"])
        f.addRow("Custom resolution", wh)
        self.w["fps"] = QComboBox()
        self.w["fps"].addItems(["30", "60"])
        self.w["fps"].setCurrentText(str(o.fps))
        f.addRow("Frame rate", self.w["fps"])
        self.w["crf"] = spin(10, 40, o.crf)
        f.addRow("Quality (CRF/CQ, lower = better)", self.w["crf"])
        self.w["vbit"] = spin(0, 50000, o.video_bitrate_k, " kbps")
        self.w["vbit"].setSpecialValueText("Quality-based")
        f.addRow("Video bitrate", self.w["vbit"])
        self.w["abit"] = spin(64, 320, o.audio_bitrate_k, " kbps")
        f.addRow("Audio bitrate", self.w["abit"])
        self.w["encoder"] = QComboBox()
        import sys as _sys
        encs = [("auto", "Automatic (GPU if usable)")]
        encs += [("h264_videotoolbox", "Apple VideoToolbox")] if _sys.platform == "darwin" else [("h264_nvenc", "NVIDIA NVENC")]
        encs += [("libx264", "CPU (libx264)")]
        for k, t in encs:
            self.w["encoder"].addItem(t, k)
        self.w["encoder"].setCurrentIndex(self.w["encoder"].findData(o.encoder))
        f.addRow("GPU acceleration", self.w["encoder"])
        self.enc_status = label("Checking GPU…", "Muted")
        f.addRow("", self.enc_status)
        from dogsapka_clipper.processing.ffmpeg_utils import ENCODER_LABELS, hardware_encoder
        run_async(hardware_encoder, lambda e: self.enc_status.setText(
            f"✔ {ENCODER_LABELS[e]} hardware encoding is available" if e
            else "No usable hardware encoder on this computer - CPU encoding is used"))
        od = QHBoxLayout()
        self.w["outdir"] = QLineEdit(o.output_dir)
        self.w["outdir"].setPlaceholderText(str(win.backend.paths.exports))
        od.addWidget(self.w["outdir"])
        br = QPushButton()
        br.setIcon(icon("folder"))
        br.clicked.connect(lambda: self.w["outdir"].setText(QFileDialog.getExistingDirectory(self) or self.w["outdir"].text()))
        od.addWidget(br)
        f.addRow("Output folder", od)
        self.w["profile"] = QComboBox()
        for k, cfg in BUILTIN_PROFILES.items():
            self.w["profile"].addItem(cfg["name"], k)
        self.w["profile"].setCurrentIndex(max(0, self.w["profile"].findData(o.profile)))
        f.addRow("Default editing profile", self.w["profile"])
        self.w["auto_render"] = QCheckBox("Automatically edit and export new clips")
        self.w["auto_render"].setChecked(o.auto_render)
        f.addRow("", self.w["auto_render"])
        self.w["workers"] = spin(1, 8, o.workers)
        f.addRow("Parallel processing jobs", self.w["workers"])
        self.body.addWidget(c)

        c, f = _section(tr("set.ai"), "All AI runs locally. Models are only loaded when the feature is enabled.")
        a = s.ai
        self.w["transcription"] = QCheckBox("Automatic subtitles (faster-whisper)")
        self.w["transcription"].setChecked(a.transcription)
        f.addRow("", self.w["transcription"])
        self.w["model"] = QComboBox()
        for m in ("tiny", "base", "small", "medium", "large-v3"):
            self.w["model"].addItem(m)
        self.w["model"].setCurrentText(a.whisper_model)
        f.addRow("Whisper model", self.w["model"])
        f.addRow("", label("tiny/base: fast, lower accuracy · small: recommended · medium/large: best, needs a strong "
                           "CPU or NVIDIA GPU. Downloaded once into the models folder.", "Muted", wrap=True))
        self.w["device"] = QComboBox()
        for k in ("auto", "cpu", "cuda"):
            self.w["device"].addItem(k)
        self.w["device"].setCurrentText(a.whisper_device)
        f.addRow("AI device", self.w["device"])
        self.w["sub_lang"] = QComboBox()
        for k, t in (("auto", "Automatic detection"), ("hu", "Magyar"), ("en", "English")):
            self.w["sub_lang"].addItem(t, k)
        self.w["sub_lang"].setCurrentIndex(self.w["sub_lang"].findData(a.subtitle_language))
        f.addRow("Subtitle language", self.w["sub_lang"])
        self.w["face"] = QCheckBox("Face / facecam detection for smart layouts")
        self.w["face"].setChecked(a.face_detection)
        f.addRow("", self.w["face"])
        self.w["meta_lang"] = QComboBox()
        self.w["meta_lang"].addItem("English", "en")
        self.w["meta_lang"].addItem("Magyar", "hu")
        self.w["meta_lang"].setCurrentIndex(self.w["meta_lang"].findData(a.metadata_language))
        f.addRow("Title language", self.w["meta_lang"])
        self.body.addWidget(c)

        c, f = _section(tr("set.interface"))
        self.w["lang"] = QComboBox()
        self.w["lang"].addItem("English", "en")
        self.w["lang"].addItem("Magyar", "hu")
        self.w["lang"].setCurrentIndex(self.w["lang"].findData(s.interface.language))
        f.addRow("Interface language", self.w["lang"])
        f.addRow("", label(tr("set.restart"), "Muted"))
        self.w["notify"] = QCheckBox("Desktop notifications")
        self.w["notify"].setChecked(s.interface.notifications)
        f.addRow("", self.w["notify"])
        wiz = QPushButton("Run setup wizard again")
        wiz.clicked.connect(self.run_wizard)
        f.addRow("", wiz)
        clean = QPushButton("Clean up old recordings now")
        clean.clicked.connect(lambda: run_async(win.backend.cleanup, lambda r: QMessageBox.information(
            self, "Cleanup", f"Removed {r['removed']} temporary files. Data folder uses {r['used_gb']:.1f} GB. "
                             "Clips and exports were not touched.")))
        f.addRow("", clean)
        drow = QHBoxLayout()
        self.data_dir_label = label(str(win.backend.paths.root), "Muted", wrap=True)
        drow.addWidget(self.data_dir_label, 1)
        chg = QPushButton("  " + tr("set.data_change"))
        chg.setIcon(icon("folder"))
        chg.clicked.connect(self.change_data_folder)
        drow.addWidget(chg)
        f.addRow(tr("set.data_folder"), drow)
        self.body.addWidget(c)

        c, f = _section(tr("set.publishing"), "Optional. Uses only the official YouTube Data API and TikTok Content "
                                              "Posting API. Uploads need your confirmation unless you turn on "
                                              "automatic upload below.")
        p = s.publishing
        self.w["yt_on"] = QCheckBox("Enable YouTube upload")
        self.w["yt_on"].setChecked(p.youtube_enabled)
        f.addRow("", self.w["yt_on"])
        self.w["yt_id"] = QLineEdit(p.youtube_client_id)
        self.w["yt_id"].setPlaceholderText("Google OAuth client ID (Desktop app)")
        f.addRow("YouTube client ID", self.w["yt_id"])
        self.w["yt_secret"] = QLineEdit()
        self.w["yt_secret"].setEchoMode(QLineEdit.Password)
        self.w["yt_secret"].setPlaceholderText("stored encrypted")
        f.addRow("YouTube client secret", self.w["yt_secret"])
        self.w["yt_priv"] = QComboBox()
        self.w["yt_priv"].addItems(["private", "unlisted", "public"])
        self.w["yt_priv"].setCurrentText(p.youtube_privacy)
        f.addRow("Default privacy", self.w["yt_priv"])
        self.w["tt_on"] = QCheckBox("Enable TikTok upload (requires an approved TikTok developer app)")
        self.w["tt_on"].setChecked(p.tiktok_enabled)
        f.addRow("", self.w["tt_on"])
        self.w["tt_key"] = QLineEdit(p.tiktok_client_key)
        f.addRow("TikTok client key", self.w["tt_key"])
        self.w["tt_secret"] = QLineEdit()
        self.w["tt_secret"].setEchoMode(QLineEdit.Password)
        self.w["tt_secret"].setPlaceholderText("stored encrypted")
        f.addRow("TikTok client secret", self.w["tt_secret"])
        from dogsapka_clipper.ui.accounts_panel import AccountsPanel
        self.accounts_panel = AccountsPanel(self)
        c.lay.addWidget(self.accounts_panel)
        self.body.addWidget(c)
        self.body.addStretch()
        self._update_status()

    def on_show(self):
        self._update_status()

    def _update_status(self):
        st = self.backend.twitch_status()
        self.tw_status.setText({"connected": "✔ Connected", "app_token": "✔ App access (public data)",
                                "not_connected": "Not connected", "not_configured": "Enter a Client ID first"}[st])

    def collect(self) -> AppSettings:
        s = copy.deepcopy(self.backend.settings)
        w = self.w
        s.twitch.client_id = w["client_id"].text().strip()
        s.twitch.poll_interval_s = w["poll"].value()
        s.twitch.use_chat = w["use_chat"].isChecked()
        s.youtube_live.poll_interval_s = w["yt_poll"].value()
        s.youtube_live.use_chat = w["yt_chat"].isChecked()
        s.youtube_live.chat_poll_s = w["yt_chat_poll"].value()
        r = s.recording
        r.enabled, r.quality = w["rec_enabled"].isChecked(), w["quality"].currentText()
        r.buffer_seconds, r.pre_event_s, r.post_event_s = w["buffer"].value(), w["pre"].value(), w["post"].value()
        r.min_clip_s, r.max_clip_s = w["min"].value(), w["max"].value()
        r.max_storage_gb, r.cleanup_recordings_days = w["storage"].value(), w["cleanup"].value()
        d = s.detection
        d.sensitivity = w["sens"].value() / 100
        for k in ("chat_activity", "chat_reactions", "audio", "visual"):
            setattr(d, f"use_{k}", w[f"use_{k}"].isChecked())
            setattr(d, f"weight_{k}", w[f"w_{k}"].value())
        d.threshold, d.auto_threshold = w["threshold"].value(), w["auto_thr"].isChecked()
        d.cooldown_s, d.merge_window_s = w["cooldown"].value(), w["merge"].value()
        o = s.output
        preset = w["preset"].currentData()
        if preset == "custom":
            o.preset, o.width, o.height = "custom", w["width"].value(), w["height"].value()
            o.fps, o.crf = int(w["fps"].currentText()), w["crf"].value()
        else:
            apply_preset(o, preset)
        o.video_bitrate_k, o.audio_bitrate_k = w["vbit"].value(), w["abit"].value()
        o.encoder, o.output_dir = w["encoder"].currentData(), w["outdir"].text().strip()
        o.profile, o.auto_render, o.workers = w["profile"].currentData(), w["auto_render"].isChecked(), w["workers"].value()
        a = s.ai
        a.transcription, a.whisper_model, a.whisper_device = (w["transcription"].isChecked(), w["model"].currentText(),
                                                              w["device"].currentText())
        a.subtitle_language, a.face_detection = w["sub_lang"].currentData(), w["face"].isChecked()
        a.metadata_language = w["meta_lang"].currentData()
        s.interface.language, s.interface.notifications = w["lang"].currentData(), w["notify"].isChecked()
        p = s.publishing
        p.youtube_enabled, p.youtube_client_id, p.youtube_privacy = (w["yt_on"].isChecked(), w["yt_id"].text().strip(),
                                                                     w["yt_priv"].currentText())
        p.tiktok_enabled, p.tiktok_client_key = w["tt_on"].isChecked(), w["tt_key"].text().strip()
        return s

    def save(self, quiet: bool = False) -> None:
        s = self.collect()
        if s.recording.min_clip_s > s.recording.max_clip_s:
            QMessageBox.warning(self, tr("error"), "Minimum clip length cannot exceed the maximum.")
            return
        lang_changed = s.interface.language != self.backend.settings.interface.language
        self.backend.update_settings(s)
        for key, store_key in (("client_secret", "twitch_client_secret"), ("yt_secret", "youtube_client_secret"),
                               ("tt_secret", "tiktok_client_secret")):
            if self.w[key].text():
                self.backend.secrets.set(store_key, self.w[key].text())
                self.w[key].clear()
        if self.w["yt_api_key"].text().strip():
            self.backend.set_youtube_api_key(self.w["yt_api_key"].text())
            self.w["yt_api_key"].clear()
            self.w["yt_api_key"].setPlaceholderText(tr("set.yt_key_set"))
        self._update_status()
        if quiet:
            return
        QMessageBox.information(self, tr("nav.settings"), tr("set.saved") + ("\n" + tr("set.restart") if lang_changed else ""))

    def connect_twitch(self) -> None:
        self.save()
        if not self.backend.settings.twitch.client_id:
            return
        dlg = DeviceCodeDialog(self)
        from PySide6.QtCore import QObject, Signal, QUrl
        from PySide6.QtGui import QDesktopServices

        class Sig(QObject):
            code = Signal(object)
        sig = Sig()

        def on_code(dc):
            dlg.msg.setText(f"Open {dc.verification_uri} and enter this code (valid for {dc.expires_in // 60} minutes):")
            dlg.code.setText(dc.user_code)
            dlg.open_btn.setEnabled(True)
            dlg.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(dc.verification_uri)))
            QDesktopServices.openUrl(QUrl(dc.verification_uri))
        sig.code.connect(on_code)
        fut = self.backend.twitch_device_login(sig.code.emit)

        def finished(f):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._login_done(dlg, f))
        fut.add_done_callback(finished)
        dlg.exec()
        if not fut.done():
            fut.cancel()

    def _login_done(self, dlg, fut) -> None:
        if fut.cancelled():
            return
        err = fut.exception()
        if err:
            dlg.msg.setText(f"Login failed: {err} {getattr(err, 'hint', '')}")
        else:
            dlg.accept()
            QMessageBox.information(self, "Twitch", "Twitch account connected.")
        self._update_status()

    def disconnect_twitch(self) -> None:
        if self.backend.auth:
            self.backend.auth.logout()
        self._update_status()

    def change_data_folder(self) -> None:
        """Pick another drive/folder for clips, recordings and exports; existing data can be moved along."""
        import shutil
        from pathlib import Path
        from dogsapka_clipper.core.paths import folder_size, normalize_data_folder, set_data_location
        old = self.backend.paths.root
        picked = QFileDialog.getExistingDirectory(self, tr("set.data_pick"), str(old.parent))
        if not picked:
            return
        new = normalize_data_folder(Path(picked))
        try:
            if new.resolve() == old.resolve():
                return
            if old.resolve() in new.resolve().parents or new.resolve() in old.resolve().parents:
                QMessageBox.warning(self, tr("error"), tr("set.data_nested"))
                return
        except OSError:
            pass
        size = folder_size(old)
        r = QMessageBox.question(self, tr("set.data_folder"),
                                 tr("set.data_move_q").format(new=new, size=size / 1e9),
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return
        move = r == QMessageBox.Yes
        try:
            new.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(new).free
        except OSError as e:
            QMessageBox.warning(self, tr("error"), f"{new}: {e}")
            return
        if move and free < size * 1.05:
            QMessageBox.warning(self, tr("error"), tr("set.data_nospace").format(free=free / 1e9, size=size / 1e9))
            return
        set_data_location(new, move_from=old if move else None)
        self.data_dir_label.setText(f"{old}  →  {new}  ({tr('set.data_after_restart')})")
        if QMessageBox.question(self, tr("set.data_folder"), tr("set.data_restart_q")) == QMessageBox.Yes:
            self.win.restart_app()

    def run_wizard(self) -> None:
        from dogsapka_clipper.ui.setup_wizard import SetupWizard
        SetupWizard(self.backend, self).exec()
