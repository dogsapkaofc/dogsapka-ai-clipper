import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QProgressBar, QVBoxLayout, QWidget
from shiboken6 import isValid

from dogsapka_clipper.core.events import Event, EventBus
from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.processing.jobs import JobQueue
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages.queue import QueuePage
from dogsapka_clipper.ui.pages.streamers import StreamersPage
from dogsapka_clipper.ui.widgets.common import clear_layout


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_removing_progress_controls_never_turns_them_into_windows(app):
    holder = QWidget()
    layout = QVBoxLayout(holder)
    controls = [QProgressBar() for _ in range(30)]
    for control in controls:
        layout.addWidget(control)
    clear_layout(layout)
    assert not layout.count()
    assert all(c.parentWidget() is holder and not c.isWindow() for c in controls)
    assert all(c not in QApplication.topLevelWidgets() for c in controls)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert all(not isValid(c) for c in controls)


def test_empty_queue_message_is_parented_before_becoming_visible(app, db):
    bus = EventBus()
    bridge = QtBridge(bus)
    page = QueuePage(SimpleNamespace(backend=SimpleNamespace(db=db, jobs=JobQueue(db, bus)), bridge=bridge))
    try:
        assert not page._empty.isWindow()
        page.refresh()
        assert not page._empty.isWindow()
        assert page._empty not in QApplication.topLevelWidgets()
    finally:
        page.deleteLater()
        bridge.close()


def test_streamer_revisit_and_toggle_only_replace_changed_card(app, db):
    with db.session() as s:
        s.add_all([Streamer(login="first"), Streamer(login="second")])
    bus = EventBus()
    bridge = QtBridge(bus)
    b = SimpleNamespace(db=db, sessions={}, twitch_status=lambda: "not_configured", youtube_api_key=lambda: None)
    page = StreamersPage(SimpleNamespace(backend=b, bridge=bridge))
    try:
        first, second = page._cards[1], page._cards[2]
        page.on_show()
        assert page._cards[1] is first and page._cards[2] is second
        with db.session() as s:
            s.get(Streamer, 1).monitoring_enabled = False
        page.on_event(Event("streamer.updated", data={"streamer_id": 1}))
        page.on_show()
        assert page._cards[1] is not first and page._cards[2] is second
        assert not first.isWindow() and first.parentWidget() is not None
        assert first not in QApplication.topLevelWidgets()
    finally:
        page.deleteLater()
        bridge.close()
