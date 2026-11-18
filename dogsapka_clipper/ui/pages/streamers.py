from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)
from sqlalchemy import func, select
from sqlalchemy.orm import load_only
from shiboken6 import isValid

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Clip, Streamer
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Avatar, Card, FlowLayout, Pill, label


class AddStreamerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("str.add"))
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 22, 22, 22)
        v.setSpacing(12)
        v.addWidget(label(tr("str.add"), "H2"))
        self.platform = QComboBox()
        self.platform.addItem("Twitch", "twitch")
        self.platform.addItem("YouTube", "youtube")
        self.platform.currentIndexChanged.connect(self._platform_changed)
        v.addWidget(self.platform)
        self.login = QLineEdit()
        v.addWidget(self.login)
        self.hint = label("", "Muted", wrap=True)
        v.addWidget(self.hint)
        self._platform_changed()
        self.auth = QCheckBox(tr("str.authorized"))
        v.addWidget(self.auth)
        note = label("Only add channels you own or have written permission from. Recording and redistribution must "
                     "respect the streamer's rights, Twitch's Terms of Service and the destination platform's rules.",
                     "Muted", wrap=True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _platform_changed(self) -> None:
        yt = self.platform.currentData() == "youtube"
        self.login.setPlaceholderText(tr("str.yt_channel") if yt else tr("str.username") + "  (e.g. mychannel)")
        self.hint.setText(tr("str.yt_hint") if yt else "")
        self.hint.setVisible(yt)


class StreamerCard(Card):
    def __init__(self, page, st: Streamer, clips: int) -> None:
        super().__init__(padding=16)
        self.setFixedSize(340, 230)
        b = page.backend
        top = QHBoxLayout()
        av = Avatar(52)
        av.set(st.profile_image_path, st.display_name or st.login)
        top.addWidget(av)
        nv = QVBoxLayout()
        nv.setSpacing(2)
        nv.addWidget(label(st.display_name or st.login, "H2"))
        if st.platform == "youtube":
            nv.addWidget(label(f"YouTube · {st.youtube_handle or st.youtube_channel_id}", "Muted"))
        else:
            nv.addWidget(label(f"twitch.tv/{st.login}", "Muted"))
        top.addLayout(nv, 1)
        live = st.is_live
        top.addWidget(Pill(tr("str.live") if live else tr("str.offline"), theme.RED if live else theme.MUTED),
                      0, Qt.AlignTop)
        self.lay.addLayout(top)
        title = label(st.stream_title or "—", wrap=True)
        title.setMaximumHeight(38)
        self.lay.addWidget(title)
        meta = QHBoxLayout()
        meta.addWidget(Pill(st.category or "No category", theme.PURPLE2))
        sess = b.sessions.get(st.id)
        if sess and sess.recorder.running:
            meta.addWidget(Pill("● " + tr("str.recording"), theme.RED))
        elif st.monitoring_enabled:
            meta.addWidget(Pill("Monitoring", theme.CYAN))
        else:
            meta.addWidget(Pill("Paused", theme.MUTED))
        hl = sess.highlight_count if sess else 0
        meta.addWidget(Pill(f"{clips} clips · {hl} {tr('str.highlights')}", theme.GREEN))
        meta.addStretch()
        self.lay.addLayout(meta)
        self.lay.addStretch()
        btns = QHBoxLayout()
        if st.login == "dogsapka_simulation":
            tog = QPushButton("  Stop simulation" if sess else "  Simulation idle")
            tog.setEnabled(bool(sess))
            tog.clicked.connect(lambda: b.stop_session(st.id))
        else:
            tog = QPushButton("  " + (tr("str.stop") if st.monitoring_enabled else tr("str.start")))
            tog.setObjectName("Primary" if not st.monitoring_enabled else "")
            tog.setIcon(icon("pause" if st.monitoring_enabled else "play"))
            tog.clicked.connect(lambda: page.set_monitoring(st.id, not st.monitoring_enabled))
        self.monitor_button = tog
        self.monitor_text = tog.text()
        self.monitor_enabled = tog.isEnabled()
        btns.addWidget(tog)
        mon = QPushButton()
        mon.setIcon(icon("activity", theme.CYAN))
        mon.setToolTip(tr("nav.live"))
        mon.clicked.connect(lambda: page.win.open_live(st.id))
        btns.addWidget(mon)
        rm = QPushButton()
        rm.setObjectName("Danger")
        rm.setIcon(icon("trash", theme.RED))
        rm.setToolTip(tr("str.remove"))
        rm.clicked.connect(lambda: page.remove(st.id, st.login))
        btns.addWidget(rm)
        self.lay.addLayout(btns)


class StreamersPage(Page):
    def __init__(self, win) -> None:
        super().__init__(win, tr("nav.streamers"), "Authorized Twitch and YouTube channels monitored for highlights")
        # one button for all streamers: "Start all" while any is paused, otherwise "Stop all"
        self.all_btn = QPushButton()
        self.all_btn.clicked.connect(self.toggle_all)
        self.actions.addWidget(self.all_btn)
        add = QPushButton("  " + tr("str.add"))
        add.setObjectName("Primary")
        add.setIcon(icon("plus"))
        add.clicked.connect(self.add_dialog)
        self.actions.addWidget(add)
        self.status = label("", "Muted", wrap=True)
        self.body.addWidget(self.status)
        holder = QWidget()
        self.flow = FlowLayout(holder)
        self.body.addWidget(holder)
        self.body.addStretch()
        self._dirty = True
        self._pending_ids = set()
        self._bulk_pending = False
        self._action_error = ""
        self._monitoring_counts = (0, 0)
        self._cards = {}
        self._card_keys = {}
        self._empty = Card()
        self._empty.setParent(holder)
        self._empty.setFixedSize(520, 120)
        self._empty.lay.addWidget(label(tr("str.empty"), "Muted", wrap=True))
        self._empty.hide()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        win.bridge.event.connect(self.on_event)
        self.refresh()

    def on_event(self, ev) -> None:
        if ev.kind.startswith("streamer.") or ev.kind in ("session.started", "session.ended", "clip.saved",
                                                           "highlight.detected", "settings.changed", "recording.status"):
            self._dirty = True
            if self.isVisible() and not self._refresh_timer.isActive():
                self._refresh_timer.start(100)

    def on_show(self) -> None:
        if self._dirty:
            self.refresh()

    def _update_all_button(self, on: int, total: int) -> None:
        start = on < total or total == 0
        self.all_btn.setText("  " + (tr("str.start_all") if start else tr("str.stop_all")) + f"  ({on}/{total})")
        self.all_btn.setIcon(icon("play" if start else "pause", theme.CYAN if start else theme.RED))
        self.all_btn.setObjectName("Accent" if start else "Danger")
        self.all_btn.style().unpolish(self.all_btn)
        self.all_btn.style().polish(self.all_btn)
        self.all_btn.setEnabled(total > 0 and not self._bulk_pending)
        self.all_btn.setToolTip(tr("str.all_tip"))

    def toggle_all(self) -> None:
        if self._bulk_pending:
            return
        on, total = self._monitoring_counts
        start = on < total
        if not start and self.backend.sessions and QMessageBox.question(
                self, tr("str.stop_all"), tr("str.stop_all_q")) != QMessageBox.Yes:
            return
        self._run_monitor_action(None, lambda: self.backend.set_monitoring_all(start))

    def set_monitoring(self, sid: int, enabled: bool) -> None:
        if sid not in self._pending_ids and not self._bulk_pending:
            self._run_monitor_action(sid, lambda: self.backend.set_monitoring(sid, enabled))

    def _run_monitor_action(self, sid, operation) -> None:
        if sid is None:
            self._bulk_pending = True
        else:
            self._pending_ids.add(sid)
        self._action_error = ""
        self.refresh()

        def finished(error=None):
            if not isValid(self):
                return
            if sid is None:
                self._bulk_pending = False
            else:
                self._pending_ids.discard(sid)
            self._action_error = f"Could not update streamer: {error}" if error else ""
            self.refresh()
        run_async(operation, lambda _: finished(), finished)

    def refresh(self) -> None:
        self._refresh_timer.stop()
        st = self.backend.twitch_status()
        msgs = {"not_configured": "⚠ Twitch is not configured. Open Settings → Twitch to enter your application's "
                                  "Client ID and connect your account. Simulation mode works without it.",
                "not_connected": "⚠ Twitch account not connected - live status cannot be checked yet (Settings → Twitch).",
                "connected": "", "app_token": ""}
        with self.backend.db.session() as s:
            rows = s.scalars(select(Streamer).options(load_only(Streamer.id, Streamer.login, Streamer.display_name,
                             Streamer.platform, Streamer.youtube_handle, Streamer.youtube_channel_id,
                             Streamer.profile_image_path, Streamer.is_live, Streamer.stream_title,
                             Streamer.category, Streamer.monitoring_enabled))
                             .order_by(Streamer.is_live.desc(), Streamer.login)).all()
        real = [r for r in rows if r.login != "dogsapka_simulation"]
        self._monitoring_counts = sum(r.monitoring_enabled for r in real), len(real)
        self._update_all_button(*self._monitoring_counts)
        has_tw = any(r.platform == "twitch" and r.login != "dogsapka_simulation" for r in rows)
        has_yt = any(r.platform == "youtube" for r in rows)
        text = msgs[st] if (has_tw or not has_yt) else ""
        if has_yt and not self.backend.youtube_api_key():
            text = (text + "\n" if text else "") + tr("str.yt_no_key")
        if self._bulk_pending or self._pending_ids:
            text += ("\n" if text else "") + "Updating monitoring…"
        elif self._action_error:
            text += ("\n" if text else "") + self._action_error
        self.status.setText(text)
        self.status.setVisible(bool(text))
        with self.backend.db.session() as s:
            counts = dict(s.execute(select(Clip.streamer_id, func.count()).group_by(Clip.streamer_id)).all())
        cards, keys = {}, {}
        for r in rows:
            sess = self.backend.sessions.get(r.id)
            key = (r.login, r.display_name, r.platform, r.youtube_handle, r.youtube_channel_id,
                   r.profile_image_path, r.is_live, r.stream_title, r.category, r.monitoring_enabled,
                   counts.get(r.id, 0), bool(sess), bool(sess and sess.recorder.running), sess.highlight_count if sess else 0)
            card = self._cards.get(r.id)
            if card is None or self._card_keys.get(r.id) != key:
                card = StreamerCard(self, r, counts.get(r.id, 0))
            busy = self._bulk_pending or r.id in self._pending_ids
            card.monitor_button.setEnabled(card.monitor_enabled and not busy)
            card.monitor_button.setText("Working…" if busy else card.monitor_text)
            cards[r.id], keys[r.id] = card, key
        if list(cards) != list(self._cards) or keys != self._card_keys:
            while self.flow.count():
                self.flow.takeAt(0)
            for sid, old in self._cards.items():
                if cards.get(sid) is not old:
                    old.hide()
                    old.deleteLater()
            for card in cards.values():
                self.flow.addWidget(card)
        self._empty.setVisible(not rows)
        if not rows and not self.flow.count():
            self.flow.addWidget(self._empty)
        self._cards, self._card_keys = cards, keys
        self._dirty = False

    def add_dialog(self) -> None:
        d = AddStreamerDialog(self)
        if d.exec() != QDialog.Accepted:
            return
        if d.platform.currentData() == "youtube":
            text, ok = d.login.text(), d.auth.isChecked()
            self.status.setText(tr("str.yt_resolving"))
            self.status.setVisible(True)
            run_async(lambda: self.backend.add_youtube_streamer(text, ok), lambda _: self.refresh(),
                      lambda e: (QMessageBox.warning(self, tr("error"), f"{e} {getattr(e, 'hint', '')}".strip()),
                                 self.refresh()))
            return
        try:
            self.backend.add_streamer(d.login.text(), d.auth.isChecked())
        except ValueError as e:
            QMessageBox.warning(self, tr("error"), str(e))
            return
        self.refresh()

    def remove(self, sid: int, login: str) -> None:
        if QMessageBox.question(self, tr("str.remove"), f"Remove {login}? Its clips are removed from the library; "
                                "exported videos stay on disk.") == QMessageBox.Yes:
            if sid not in self._pending_ids:
                self._run_monitor_action(sid, lambda: self.backend.remove_streamer(sid))
