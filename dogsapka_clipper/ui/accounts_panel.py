"""Settings → Publishing: connected YouTube / TikTok accounts with per-account auto-upload."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)
from sqlalchemy import select

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.publishing.accounts import TT_MODES, YT_PRIVACY
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import run_async
from dogsapka_clipper.ui.icons import icon
from dogsapka_clipper.ui.widgets.common import Pill, clear_layout, label

TT_LABELS = {"inbox": "TikTok inbox (finish in app)", "SELF_ONLY": "Direct post: only me",
             "MUTUAL_FOLLOW_FRIENDS": "Direct post: friends", "FOLLOWER_OF_CREATOR": "Direct post: followers",
             "PUBLIC_TO_EVERYONE": "Direct post: public"}


def youtube_mode_label(mode: str) -> str:
    return tr("yt.schedule_mode") if mode == "scheduled" else mode


class StreamerFilterDialog(QDialog):
    """Choose which streamers' clips go to an account (none checked = all streamers)."""

    def __init__(self, backend, selected: list[int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("acc.streamers"))
        self.setMinimumWidth(380)
        v = QVBoxLayout(self)
        v.addWidget(label(tr("acc.streamers_hint"), "Muted", wrap=True))
        self.list = QListWidget()
        with backend.db.session() as s:
            rows = s.scalars(select(Streamer).where(Streamer.login != "dogsapka_simulation")
                             .order_by(Streamer.display_name)).all()
        for st in rows:
            it = QListWidgetItem(f"{st.display_name or st.login}  ({'YouTube' if st.platform == 'youtube' else 'Twitch'})")
            it.setData(Qt.UserRole, st.id)
            it.setCheckState(Qt.Checked if st.id in selected else Qt.Unchecked)
            self.list.addItem(it)
        v.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def selected(self) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class AccountsPanel(QWidget):
    def __init__(self, page) -> None:
        super().__init__()
        self.page = page
        self.backend = page.backend
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(label(tr("acc.title"), "H2"))
        v.addWidget(label(tr("acc.desc"), "Muted", wrap=True))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        v.addLayout(self.rows)
        btns = QHBoxLayout()
        add_yt = QPushButton("  " + tr("acc.add_yt"))
        add_yt.setIcon(icon("plus"))
        add_yt.clicked.connect(self.add_youtube)
        btns.addWidget(add_yt)
        add_tt = QPushButton("  " + tr("acc.add_tt"))
        add_tt.setIcon(icon("plus"))
        add_tt.clicked.connect(self.add_tiktok)
        btns.addWidget(add_tt)
        btns.addStretch()
        v.addLayout(btns)
        self.auto = QCheckBox(tr("acc.auto_global"))
        p = self.backend.settings.publishing
        self.auto.setChecked(p.auto_publish and p.auto_publish_confirmed)
        self.auto.toggled.connect(self.toggle_global_auto)
        v.addWidget(self.auto)
        v.addWidget(label(tr("acc.auto_note"), "Muted", wrap=True))
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        page.win.bridge.event.connect(self._on_event)
        self.refresh()

    def _on_event(self, ev) -> None:
        if ev.kind == "accounts.changed":
            self.refresh()

    def refresh(self) -> None:
        clear_layout(self.rows)
        accounts = self.backend.accounts.list()
        if not accounts:
            self.rows.addWidget(label(tr("acc.none"), "Muted", wrap=True))
        for acc in accounts:
            self.rows.addWidget(self._row(acc))

    def _row(self, acc) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        yt = acc.platform == "youtube"
        h.addWidget(Pill("YouTube" if yt else "TikTok", theme.RED if yt else theme.CYAN))
        h.addWidget(label(acc.name), 1)
        priv = QComboBox()
        for m in (YT_PRIVACY if yt else TT_MODES):
            priv.addItem(youtube_mode_label(m) if yt else TT_LABELS[m], m)
        priv.setCurrentIndex(max(0, priv.findData(acc.privacy)))
        priv.currentIndexChanged.connect(lambda _=0, a=acc.id, c=priv: self.backend.accounts.update(a, privacy=c.currentData()))
        h.addWidget(priv)
        n = len(acc.streamer_filter or [])
        flt = QPushButton(tr("acc.all_streamers") if not n else f"{n} streamer{'s' if n != 1 else ''}")
        flt.setToolTip(tr("acc.streamers"))
        flt.clicked.connect(lambda _=False, a=acc: self.edit_filter(a))
        h.addWidget(flt)
        auto = QCheckBox(tr("acc.auto"))
        auto.setChecked(acc.auto_upload)
        auto.toggled.connect(lambda on, a=acc.id: self.backend.accounts.update(a, auto_upload=on))
        h.addWidget(auto)
        if yt:
            reconnect = QPushButton(tr("yt.reauthorize"))
            reconnect.setToolTip(tr("yt.schedule_permission"))
            reconnect.clicked.connect(lambda _=False, a=acc.id: self.reauthorize_youtube(a))
            h.addWidget(reconnect)
        rm = QPushButton()
        rm.setObjectName("Danger")
        rm.setIcon(icon("trash", theme.RED))
        rm.setToolTip(tr("str.remove"))
        rm.clicked.connect(lambda _=False, a=acc: self.remove(a))
        h.addWidget(rm)
        return w

    def edit_filter(self, acc) -> None:
        d = StreamerFilterDialog(self.backend, list(acc.streamer_filter or []), self)
        if d.exec() == QDialog.Accepted:
            self.backend.accounts.update(acc.id, streamer_filter=d.selected())

    def remove(self, acc) -> None:
        if QMessageBox.question(self, tr("str.remove"), tr("acc.remove_q").format(name=acc.name)) == QMessageBox.Yes:
            self.backend.accounts.remove(acc.id)

    def _connect(self, fn, platform: str) -> None:
        if self.page.save(quiet=True) is False:
            return
        p = self.backend.settings.publishing
        if platform == "youtube" and not p.youtube_client_id:
            QMessageBox.warning(self, "YouTube", tr("acc.need_yt_creds"))
            return
        if platform == "tiktok" and not p.tiktok_client_key:
            QMessageBox.warning(self, "TikTok", tr("acc.need_tt_creds"))
            return
        self.status.setText(tr("acc.signing_in"))
        run_async(fn, lambda acc: self.status.setText(tr("acc.added").format(name=acc.name)),
                  lambda e: self.status.setText(f"{tr('error')}: {e}"))

    def add_youtube(self) -> None:
        self._connect(self.backend.accounts.add_youtube, "youtube")

    def reauthorize_youtube(self, acc_id: int) -> None:
        self._connect(lambda: self.backend.accounts.reconnect_youtube(acc_id), "youtube")

    def add_tiktok(self) -> None:
        direct = QMessageBox.question(self, "TikTok", tr("acc.tt_direct_q")) == QMessageBox.Yes
        self._connect(lambda: self.backend.accounts.add_tiktok(direct_post=direct), "tiktok")

    def toggle_global_auto(self, on: bool) -> None:
        s = self.backend.settings
        if on and not s.publishing.auto_publish_confirmed:
            if QMessageBox.question(self, tr("acc.auto_global"), tr("acc.confirm")) != QMessageBox.Yes:
                self.auto.blockSignals(True)
                self.auto.setChecked(False)
                self.auto.blockSignals(False)
                return
        import copy
        new = copy.deepcopy(s)
        new.publishing.auto_publish = on
        new.publishing.auto_publish_confirmed = on
        self.backend.update_settings(new)
