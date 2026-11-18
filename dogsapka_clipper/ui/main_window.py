"""Frameless main window with custom title bar and sidebar navigation."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, Qt,
                            QTimer)
from PySide6.QtGui import QAction, QColor
from shiboken6 import isValid
from PySide6.QtWidgets import (QApplication, QButtonGroup, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QSizeGrip, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from dogsapka_clipper import __version__
from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.services.backend import Backend
from dogsapka_clipper.ui import theme
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.icons import app_icon, icon, pixmap


class TitleBar(QWidget):
    def __init__(self, win: QMainWindow) -> None:
        super().__init__()
        self.win = win
        self._drag: QPoint | None = None
        self.setFixedHeight(40)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 6, 0)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        h.addWidget(self.status)
        h.addStretch()
        for name, slot, obj in (("min", win.showMinimized, "TitleBtn"), ("max", self.toggle_max, "TitleBtn"),
                                ("x", win.close, "CloseBtn")):
            b = QPushButton()
            b.setObjectName(obj)
            b.setIcon(icon(name, theme.MUTED, 14))
            b.setFixedSize(38, 28)
            b.clicked.connect(slot)
            h.addWidget(b)

    def toggle_max(self):
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            wh = self.win.windowHandle()
            if wh and wh.startSystemMove():   # native drag (Aero snap on Windows)
                return
            self._drag = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.toggle_max()


class MainWindow(QMainWindow):
    PAGES = [("dashboard", "nav.dashboard", "dashboard"), ("streamers", "nav.streamers", "users"),
             ("live", "nav.live", "activity"), ("library", "nav.library", "film"),
             ("editor", "nav.editor", "scissors"), ("queue", "nav.queue", "layers"),
             ("settings", "nav.settings", "settings")]

    def __init__(self, backend: Backend) -> None:
        super().__init__()
        self.backend = backend
        self.bridge = QtBridge(backend.bus)
        self.setWindowTitle("DOGSAPKA AI CLIPPER")
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QWidget()
        side.setObjectName("Sidebar")
        self.side = side
        # sliding highlight behind the active menu item (glides between items when navigating)
        self.nav_indicator = QWidget(side)
        self.nav_indicator.setObjectName("NavIndicator")
        self.nav_indicator.setAttribute(Qt.WA_StyledBackground, True)
        self.nav_bar = QWidget(self.nav_indicator)
        self.nav_bar.setObjectName("NavBar")
        self.nav_bar.setAttribute(Qt.WA_StyledBackground, True)
        self.nav_indicator.hide()
        self._nav_anim = QPropertyAnimation(self.nav_indicator, b"geometry", self)
        self._nav_anim.setDuration(320)
        self._nav_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._current = ""
        self._indicator_pending = False
        self._page_anim = None
        self._page_overlay = None
        self._win_anim = None
        self._shown_once = False
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 18, 14, 14)
        sv.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        brand.addWidget(logo)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        b1 = QLabel("DOGSAPKA")
        b1.setObjectName("Brand")
        b2 = QLabel("AI CLIPPER")
        b2.setStyleSheet(f"color: {theme.CYAN}; font-size: 8pt; font-weight: 700; letter-spacing: 3px;")
        bt.addWidget(b1)
        bt.addWidget(b2)
        brand.addLayout(bt)
        brand.addStretch()
        sv.addLayout(brand)
        sv.addSpacing(22)

        from dogsapka_clipper.ui.pages.dashboard import DashboardPage
        from dogsapka_clipper.ui.pages.editor import EditorPage
        from dogsapka_clipper.ui.pages.library import LibraryPage
        from dogsapka_clipper.ui.pages.live import LivePage
        from dogsapka_clipper.ui.pages.queue import QueuePage
        from dogsapka_clipper.ui.pages.settings import SettingsPage
        from dogsapka_clipper.ui.pages.streamers import StreamersPage

        self.stack = QStackedWidget()
        self.stack.installEventFilter(self)
        self.pages = {
            "dashboard": DashboardPage(self), "streamers": StreamersPage(self), "live": LivePage(self),
            "library": LibraryPage(self), "editor": EditorPage(self), "queue": QueuePage(self),
            "settings": SettingsPage(self)}
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, tkey, ic in self.PAGES:
            b = QPushButton("   " + tr(tkey))
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setIcon(icon(ic, theme.MUTED, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            sv.addWidget(b)
            self.stack.addWidget(self.pages[key])
        self.nav_indicator.lower()
        side.installEventFilter(self)
        sv.addStretch()
        self.queue_label = QLabel("")
        self.queue_label.setObjectName("Muted")
        self.queue_label.setWordWrap(True)
        sv.addWidget(self.queue_label)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("Muted")
        ver.setStyleSheet("font-size: 8pt;")
        sv.addWidget(ver)
        outer.addWidget(side)

        right = QWidget()
        right.setObjectName("Content")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.titlebar = TitleBar(self)
        rv.addWidget(self.titlebar)
        rv.addWidget(self.stack, 1)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        rv.addLayout(grip_row)
        outer.addWidget(right, 1)

        # System tray: the app keeps running there; the window's X only hides it.
        # Exiting is done from the tray icon's context menu ("Close").
        self._quitting = False
        self._hint_shown = False
        self._styled_hwnd = 0
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("DOGSAPKA AI CLIPPER")
            menu = QMenu(self)
            act_open = QAction(tr("tray.open"), menu)
            act_open.triggered.connect(self.restore)
            menu.addAction(act_open)
            menu.addSeparator()
            act_quit = QAction(tr("tray.quit"), menu)
            act_quit.triggered.connect(self.quit_app)
            menu.addAction(act_quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
        from dogsapka_clipper.ui.widgets.common import Toast
        self._toast = Toast(self.centralWidget())
        self.bridge.event.connect(self._on_event)
        self.navigate("dashboard")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(3000)
        self._tick()

    def navigate(self, key: str) -> None:
        if key not in self.pages:
            raise KeyError(key)
        if key == self._current and self.stack.currentWidget() is self.pages[key]:
            return  # keep the page's scroll/selection and do not reload it on repeated clicks
        # Capture the current composite before stopping an interrupted transition. This also
        # handles rapid sidebar clicks without flashing an older page back on screen.
        snapshot = self.stack.grab() if self.isVisible() and not self.isMinimized() else None
        self._finish_page_transition()
        for k, b in self.nav_buttons.items():
            ic = next(i for kk, _, i in self.PAGES if kk == k)
            b.setChecked(k == key)
            b.setIcon(icon(ic, theme.PURPLE2 if k == key else theme.MUTED, 18))
        self._current = key
        self._move_indicator(animate=True)
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()
        if snapshot is not None and not snapshot.isNull():
            self._page_transition(snapshot)

    def _indicator_rect(self) -> QRect | None:
        b = self.nav_buttons.get(self._current)
        if b is None or not b.isVisible() or b.height() < 10:
            return None
        return QRect(b.mapTo(self.side, QPoint(0, 0)), b.size())

    def _move_indicator(self, animate: bool) -> None:
        r = self._indicator_rect()
        if r is None:
            return
        self.nav_bar.setGeometry(0, 10, 3, r.height() - 20)
        if not animate or not self.nav_indicator.isVisible():
            self._nav_anim.stop()
            self.nav_indicator.setGeometry(r)
            self.nav_indicator.show()
            return
        self._nav_anim.stop()
        self._nav_anim.setStartValue(self.nav_indicator.geometry())
        self._nav_anim.setEndValue(r)
        self._nav_anim.start()

    def eventFilter(self, obj, ev):
        if obj is self.side and ev.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            self._queue_indicator_update()
        if obj is getattr(self, "stack", None) and ev.type() == QEvent.Resize:
            self._finish_page_transition()
        return super().eventFilter(obj, ev)

    def _queue_indicator_update(self) -> None:
        if self._indicator_pending or not self.isVisible():
            return
        self._indicator_pending = True
        QTimer.singleShot(0, self._sync_indicator)

    def _sync_indicator(self) -> None:
        if not isValid(self):
            return
        self._indicator_pending = False
        if self.isVisible():
            if (self._nav_anim.state() == QPropertyAnimation.Running and
                    self._nav_anim.endValue() == self._indicator_rect()):
                return  # a redundant layout request must not snap a running sidebar animation
            self._move_indicator(False)

    def _page_transition(self, snapshot) -> None:
        """Crossfade the outgoing image over the live page, without moving layout widgets.

        Applying opacity to a whole page conflicts with its child card shadows and
        animating its position fights QStackedWidget's layout. Only this flat overlay
        is animated; the selected page remains fully painted below it.
        """
        overlay = QLabel(self.stack)
        overlay.setObjectName("PageTransitionOverlay")
        # Do not send clicks on still-visible outgoing controls to different incoming
        # controls. Sidebar clicks remain available for interrupting the transition.
        overlay.setAttribute(Qt.WA_NoMousePropagation)
        overlay.setPixmap(snapshot)
        overlay.setScaledContents(True)
        overlay.setGeometry(self.stack.rect())
        eff = QGraphicsOpacityEffect(overlay)
        eff.setOpacity(1.0)
        overlay.setGraphicsEffect(eff)
        overlay.show()
        overlay.raise_()
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(eff, b"opacity")
        fade.setDuration(240)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(fade)
        slide = QPropertyAnimation(overlay, b"pos")
        slide.setDuration(240)
        slide.setStartValue(QPoint(0, 0))
        slide.setEndValue(QPoint(0, -10))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(slide)
        group.finished.connect(self._finish_page_transition)
        self._page_overlay = overlay
        self._page_anim = group
        group.start()

    def _finish_page_transition(self) -> None:
        group, self._page_anim = self._page_anim, None
        overlay, self._page_overlay = self._page_overlay, None
        if group is not None:
            group.stop()
            group.deleteLater()
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def rebuild_page(self, key: str) -> None:
        """Recreate a page from the current settings (e.g. after resetting them to defaults)."""
        old = self.pages[key]
        was_current = self.stack.currentWidget() is old
        self._finish_page_transition()
        cls = type(old)
        idx = self.stack.indexOf(old)
        new = cls(self)
        self.pages[key] = new
        self.stack.insertWidget(idx, new)
        self.stack.removeWidget(old)
        old.deleteLater()
        if was_current:
            self.stack.setCurrentWidget(new)
            if hasattr(new, "on_show"):
                new.on_show()

    def open_editor(self, clip_id: int) -> None:
        self.pages["editor"].load_clip(clip_id)
        self.navigate("editor")

    def open_live(self, streamer_id: int) -> None:
        self.pages["live"].select_streamer(streamer_id)
        self.navigate("live")

    def _tick(self) -> None:
        n = self.backend.jobs.pending_count()
        live = len(self.backend.sessions)
        self.titlebar.status.setText(
            f"●  {live} live session{'s' if live != 1 else ''}   ·   {n} job{'s' if n != 1 else ''} in queue"
            + ("   ·   queue paused" if self.backend.jobs.globally_paused else ""))

    def _on_event(self, ev) -> None:
        notify = {"video.exported", "highlight.detected", "streamer.live", "job.failed", "twitch.auth_error",
                  "recording.blocked"}
        if ev.kind in notify and self.tray and self.backend.settings.interface.notifications and not self.isActiveWindow():
            level = {"error": QSystemTrayIcon.Critical, "warning": QSystemTrayIcon.Warning}.get(ev.level,
                                                                                                 QSystemTrayIcon.Information)
            self.tray.showMessage("DOGSAPKA AI CLIPPER", ev.message, level, 5000)

    # -- tray / taskbar ------------------------------------------------------------
    def toast(self, text: str, color: str | None = None) -> None:
        """Short confirmation in the top-right corner (instead of a pop-up dialog)."""
        self._toast.show_message(text, color or theme.GREEN)

    def restore(self) -> None:
        self._finish_window_fade()
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and not self.isMinimized() and reason == QSystemTrayIcon.Trigger:
                self.hide()
            else:
                self.restore()

    def showEvent(self, e):
        super().showEvent(e)
        self._enable_taskbar_minimize()
        if not self._shown_once:
            self._shown_once = True
            self._fade_in()
        else:
            self._finish_window_fade()
        self._queue_indicator_update()

    def hideEvent(self, e):
        self._finish_page_transition()
        self._finish_window_fade()
        super().hideEvent(e)

    def _fade_in(self) -> None:
        """Fade only the first opening; restoring the existing window preserves its frame."""
        if self.isMinimized():
            return
        self.setWindowOpacity(0.0)
        a = QPropertyAnimation(self, b"windowOpacity", self)
        a.setDuration(220)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.finished.connect(self._finish_window_fade)
        self._win_anim = a
        a.start()

    def _finish_window_fade(self) -> None:
        animation, self._win_anim = self._win_anim, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        self.setWindowOpacity(1.0)

    def _enable_taskbar_minimize(self) -> None:
        """Frameless windows lack the Win32 minimize style, so clicking the app's taskbar button did not
        minimize it. Adding WS_MINIMIZEBOX/WS_MAXIMIZEBOX/WS_SYSMENU restores the normal taskbar behaviour
        (click = minimize / restore) without bringing back the native title bar."""
        import sys
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            if not hwnd or hwnd == self._styled_hwnd:
                return
            GWL_STYLE, WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = -16, 0x00020000, 0x00010000, 0x00080000
            user32 = ctypes.windll.user32
            user32.GetWindowLongW.restype = ctypes.c_long
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
            self._styled_hwnd = hwnd
        except Exception:
            pass

    def restart_app(self, force: bool = False) -> None:
        """Start a fresh instance, then quit this one (e.g. after changing the data folder)."""
        import sys
        from PySide6.QtCore import QProcess
        import os
        args = sys.argv[1:] if getattr(sys, "frozen", False) else ["-m", "dogsapka_clipper"] + sys.argv[1:]
        args += ["--wait-pid", str(os.getpid())]     # new instance waits until this one has fully exited
        self.quit_app(force=force)
        if self._quitting:
            QProcess.startDetached(sys.executable, args)

    def quit_app(self, force: bool = False) -> None:
        """Real exit (tray menu "Close"): stops live sessions safely, then quits the application."""
        if self.backend.sessions and not force:
            self.restore()
            r = QMessageBox.question(self, "DOGSAPKA AI CLIPPER",
                                     "Live sessions are running. Closing will stop recording safely and save "
                                     "pending clips. Close now?")
            if r != QMessageBox.Yes:
                return
        self._quitting = True
        self._timer.stop()
        self.bridge.close()
        if self.tray:
            self.tray.hide()
        self.hide()
        QApplication.instance().quit()

    def closeEvent(self, e):
        if self._quitting or self.tray is None:
            if not self._quitting:      # no tray available: closing the window exits as before
                self._quitting = True
                if self.backend.sessions and QMessageBox.question(
                        self, "DOGSAPKA AI CLIPPER", "Live sessions are running. Closing will stop recording "
                        "safely and save pending clips. Close now?") != QMessageBox.Yes:
                    self._quitting = False
                    e.ignore()
                    return
                self._timer.stop()
                self.bridge.close()
                QApplication.instance().quit()
            e.accept()
            return
        # X button: keep running in the tray
        e.ignore()
        self.hide()
        if not self._hint_shown:
            self._hint_shown = True
            self.tray.showMessage("DOGSAPKA AI CLIPPER", tr("tray.hidden"), QSystemTrayIcon.Information, 6000)
