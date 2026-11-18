"""Navigation animates snapshots while live page widgets keep stable layout and state."""
import importlib
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QLabel, QLineEdit, QVBoxLayout, QWidget
from shiboken6 import isValid

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.ui.main_window import MainWindow


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    colors = ["#762738", "#286842", "#383877", "#765322", "#337777", "#552266", "#666666"]

    def page_type(color):
        class TestPage(QWidget):
            def __init__(self, win):
                super().__init__()
                self.setAttribute(Qt.WA_StyledBackground)
                self.setStyleSheet(f"background:{color}")
                self.shown = 0
                layout = QVBoxLayout(self)
                self.edit = QLineEdit("Keep this selection")
                self.shadow_label = QLabel("Card shadow stays attached")
                self.shadow = QGraphicsDropShadowEffect(self.shadow_label)
                self.shadow_label.setGraphicsEffect(self.shadow)
                layout.addWidget(self.edit)
                layout.addWidget(self.shadow_label)
                layout.addStretch()

            def on_show(self):
                self.shown += 1
        return TestPage

    names = ["DashboardPage", "StreamersPage", "LivePage", "LibraryPage", "EditorPage", "QueuePage", "SettingsPage"]
    for (key, _, _), class_name, color in zip(MainWindow.PAGES, names, colors):
        module = importlib.import_module(f"dogsapka_clipper.ui.pages.{key}")
        monkeypatch.setattr(module, class_name, page_type(color))
    monkeypatch.setattr("dogsapka_clipper.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable", lambda: False)
    def count_initial_fade(self):
        self._test_fade_requests = getattr(self, "_test_fade_requests", 0) + 1
    monkeypatch.setattr(MainWindow, "_fade_in", count_initial_fade)
    monkeypatch.setattr(MainWindow, "_enable_taskbar_minimize", lambda self: None)
    backend = SimpleNamespace(bus=EventBus(), sessions={}, jobs=SimpleNamespace(pending_count=lambda: 0,
                              globally_paused=False), settings=SimpleNamespace(interface=SimpleNamespace(notifications=False)))
    win = MainWindow(backend)
    win.show()
    app.processEvents()
    yield win, app
    win._timer.stop()
    win.bridge.close()
    win.hide()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_repeated_current_page_click_does_not_reload_or_lose_selection(window):
    win, app = window
    page = win.pages["dashboard"]
    page.edit.setSelection(5, 4)
    shown = page.shown
    for _ in range(5):
        win.navigate("dashboard")
    assert page.shown == shown
    assert page.edit.selectedText() == "this"
    assert win._page_overlay is None


def test_transition_has_old_image_over_live_page_without_page_effect_or_movement(window):
    win, app = window
    outgoing = win.pages["dashboard"]
    incoming = win.pages["library"]
    win.navigate("library")
    overlay = win._page_overlay
    assert overlay is not None and not overlay.pixmap().isNull()
    assert win.stack.currentWidget() is incoming
    assert incoming.graphicsEffect() is None and outgoing.graphicsEffect() is None
    assert incoming.shadow_label.graphicsEffect() is incoming.shadow
    assert incoming.pos() == QPoint(0, 0)
    image = overlay.pixmap().toImage()
    color = image.pixelColor(image.width()//2, image.height()-20)
    assert color.red() > color.green()  # outgoing dark-red page, not an empty transparent frame
    animation = win._page_anim
    animation.setCurrentTime(animation.duration()//2)
    assert 0 < overlay.graphicsEffect().opacity() < 1
    assert incoming.pos() == QPoint(0, 0)
    animation.setCurrentTime(animation.duration())
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert win._page_overlay is None and win._page_anim is None and not isValid(overlay)
    assert incoming.pos() == QPoint(0, 0) and incoming.isVisible()


def test_rapid_switches_replace_overlay_and_finish_on_selected_page(window):
    win, app = window
    overlays = []
    for key in ("library", "live", "queue", "settings", "dashboard"):
        win.navigate(key)
        overlays.append(win._page_overlay)
        assert win.stack.currentWidget() is win.pages[key]
        assert win.pages[key].pos() == QPoint(0, 0)
    win._page_anim.setCurrentTime(win._page_anim.duration())
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert all(not isValid(overlay) for overlay in overlays)
    assert win._current == "dashboard" and win.pages["dashboard"].isVisible()


def test_resize_and_hide_cancel_transition_cleanly(window):
    win, app = window
    win.navigate("live")
    win.resize(win.width()+100, win.height()+50)
    app.processEvents()
    assert win._page_overlay is None and win._page_anim is None
    assert win.pages["live"].geometry() == win.stack.rect()
    win.navigate("queue")
    win.hide()
    assert win._page_overlay is None and win._page_anim is None
    assert win.pages["queue"].pos() == QPoint(0, 0)


def test_hidden_indicator_does_not_schedule_zero_delay_retry_loop(window):
    win, app = window
    win.hide()
    app.processEvents()
    for _ in range(10):
        win._move_indicator(False)
        win._queue_indicator_update()
    assert not win._indicator_pending


def test_redundant_sidebar_layout_does_not_snap_indicator_animation(window):
    win, app = window
    win.navigate("library")
    assert win._nav_anim.state() == QPropertyAnimation.Running
    win._sync_indicator()
    assert win._nav_anim.state() == QPropertyAnimation.Running


def test_rebuild_noncurrent_page_keeps_visible_page_and_cleans_transition(window):
    win, app = window
    win.navigate("library")
    old = win.pages["settings"]
    win.rebuild_page("settings")
    assert win.stack.currentWidget() is win.pages["library"]
    assert win._current == "library" and win._page_overlay is None
    assert win.pages["settings"] is not old
    win.rebuild_page("library")
    assert win.stack.currentWidget() is win.pages["library"]
    assert win.pages["library"].shown == 1


def test_hide_restore_and_minimize_do_not_restart_full_window_fade_or_reload(window):
    win, app = window
    win.navigate("library")
    page = win.pages["library"]
    page.edit.setSelection(5, 4)
    assert win._test_fade_requests == 1
    partial = QPropertyAnimation(win, b"windowOpacity", win)
    partial.setDuration(1000)
    partial.setStartValue(.3)
    partial.setEndValue(.8)
    win._win_anim = partial
    partial.start()
    win.hide()
    assert win._win_anim is None and win.windowOpacity() == 1.0
    assert partial.state() == QPropertyAnimation.Stopped
    win.restore()
    app.processEvents()
    assert win._test_fade_requests == 1 and win.windowOpacity() == 1.0
    assert win.stack.currentWidget() is page and page.shown == 1
    assert page.edit.selectedText() == "this"
    win.showMinimized()
    app.processEvents()
    win.restore()
    app.processEvents()
    assert win._test_fade_requests == 1 and win.windowOpacity() == 1.0
    assert win.stack.currentWidget() is page and page.shown == 1
