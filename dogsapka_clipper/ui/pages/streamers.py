from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)
from sqlalchemy import func, select

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Clip, Streamer
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.pages.base import Page
from dogsapka_clipper.ui.widgets.common import Avatar, Card, FlowLayout, Pill, clear_layout, label


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
            tog.clicked.connect(lambda: (b.set_monitoring(st.id, not st.monitoring_enabled), page.refresh()))
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
        win.bridge.event.connect(self.on_event)
        self.refresh()

    def on_event(self, ev) -> None:
        if ev.kind.startswith("streamer.") or ev.kind in ("session.started", "session.ended", "clip.saved",
                                                           "highlight.detected"):
            if self.isVisible():
                self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.flow)
        st = self.backend.twitch_status()
        msgs = {"not_configured": "⚠ Twitch is not configured. Open Settings → Twitch to enter your application's "
                                  "Client ID and connect your account. Simulation mode works without it.",
                "not_connected": "⚠ Twitch account not connected - live status cannot be checked yet (Settings → Twitch).",
                "connected": "", "app_token": ""}
        with self.backend.db.session() as s:
            rows = s.scalars(select(Streamer).order_by(Streamer.is_live.desc(), Streamer.login)).all()
        has_tw = any(r.platform == "twitch" and r.login != "dogsapka_simulation" for r in rows)
        has_yt = any(r.platform == "youtube" for r in rows)
        text = msgs[st] if (has_tw or not has_yt) else ""
        if has_yt and not self.backend.youtube_api_key():
            text = (text + "\n" if text else "") + tr("str.yt_no_key")
        self.status.setText(text)
        self.status.setVisible(bool(text))
        with self.backend.db.session() as s:
            counts = dict(s.execute(select(Clip.streamer_id, func.count()).group_by(Clip.streamer_id)).all())
        if not rows:
            empty = Card()
            empty.setFixedSize(520, 120)
            empty.lay.addWidget(label(tr("str.empty"), "Muted", wrap=True))
            self.flow.addWidget(empty)
        for r in rows:
            self.flow.addWidget(StreamerCard(self, r, counts.get(r.id, 0)))

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
            self.backend.remove_streamer(sid)
            self.refresh()
