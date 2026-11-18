"""Library loads do not block page transitions or spawn detached card windows."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QScrollArea
from shiboken6 import isValid
from sqlalchemy import event

from dogsapka_clipper.core.events import Event, EventBus
from dogsapka_clipper.db.models import AppSetting, Clip, ExportedVideo, Streamer
from dogsapka_clipper.detection.preferences import feedback_key
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages import library


@pytest.fixture
def page(db, monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(library, "run_async", lambda fn, done, error: pending.append((fn, done, error)))
    bridge = QtBridge(EventBus())
    backend = SimpleNamespace(db=db, feedback_count=0)
    widget = library.LibraryPage(SimpleNamespace(backend=backend, bridge=bridge, open_editor=lambda _: None))
    yield widget, pending
    if isValid(widget):
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    bridge.close()


def seed(db, count=2, thumbnail=""):
    with db.session() as s:
        streamer = Streamer(login="channel", display_name="Channel")
        s.add(streamer)
        s.flush()
        rows = [Clip(streamer_id=streamer.id, title=f"Clip {i}", source_path="source.mp4",
                     thumbnail_path=thumbnail, duration=21, detection_score=70) for i in range(count)]
        s.add_all(rows)
        s.flush()
        return [c.id for c in rows]


def drain_batch(page):
    end = time.monotonic() + 5
    while page._batch is not None and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert page._batch is None


def finish(page, pending, index=-1):
    fn, done, _ = pending[index]
    done(fn())
    drain_batch(page)


def test_on_show_defers_database_and_reuses_clean_page(db, page):
    widget, pending = page
    ids = seed(db)
    queries = []
    def query(*args):
        queries.append(args)
    event.listen(db.engine, "before_cursor_execute", query)
    try:
        widget.on_show()
        assert len(pending) == 1 and not queries  # no DB/card/file work in the navigation call
        finish(widget, pending)
        assert set(widget._cards) == set(ids)
        before = dict(widget._cards)
        widget.on_show()
        assert len(pending) == 1 and widget._cards == before
    finally:
        event.remove(db.engine, "before_cursor_execute", query)


def test_only_latest_filter_is_applied_with_one_query_in_flight(db, page):
    widget, pending = page
    seed(db, 3)
    widget.on_show()
    widget.search.setText("Clip 0")
    widget.refresh()
    widget.search.setText("Clip 2")
    widget.refresh()
    assert len(pending) == 1
    first_fn, first_done, _ = pending[0]
    first_done(first_fn())
    assert not widget._cards and len(pending) == 2
    finish(widget, pending)
    assert len(widget._cards) == 1
    assert widget._applied_filters[0] == "Clip 2"
    assert widget.search.text() == "Clip 2"
    widget.on_show()
    assert len(pending) == 2


def test_refresh_reuses_unchanged_cards_and_replaces_changed_card(db, page):
    widget, pending = page
    ids = seed(db)
    widget.on_show()
    finish(widget, pending)
    before = dict(widget._cards)
    widget.on_event(Event("video.exported"))
    widget.on_show()
    finish(widget, pending)
    assert all(widget._cards[cid] is before[cid] for cid in ids)
    with db.session() as s:
        s.get(Clip, ids[0]).title = "Updated title"
    widget.on_event(Event("video.exported"))
    widget.on_show()
    finish(widget, pending)
    assert widget._cards[ids[0]] is not before[ids[0]]
    assert widget._cards[ids[1]] is before[ids[1]]


def test_unchanged_worker_result_does_not_hide_or_relayout_cards(db, page, monkeypatch):
    widget, pending = page
    seed(db, 8)
    widget.on_show()
    finish(widget, pending)
    before = dict(widget._cards)
    for card in before.values():
        monkeypatch.setattr(card, "hide", lambda: pytest.fail("Unchanged card was hidden"))
    monkeypatch.setattr(widget.flow, "takeAt", lambda _: pytest.fail("Unchanged layout was rebuilt"))
    widget.on_event(Event("streamer.updated"))
    widget.on_show()
    finish(widget, pending)
    assert widget._cards == before and widget._batch is None


def test_single_feedback_change_keeps_other_cards_visible_and_scroll_position(db, page, monkeypatch):
    widget, pending = page
    ids = seed(db, 12)
    widget.resize(900, 800)
    widget.show()
    widget.on_show()
    finish(widget, pending)
    QCoreApplication.processEvents()
    before = dict(widget._cards)
    bar = widget.findChild(QScrollArea).verticalScrollBar()
    bar.setValue(100)
    scroll = bar.value()
    assert scroll > 0
    for cid, card in before.items():
        if cid != ids[0]:
            monkeypatch.setattr(card, "hide", lambda: pytest.fail("Unchanged feedback card was hidden"))
    with db.session() as s:
        s.add(AppSetting(key=feedback_key(ids[0]), value={"clip_id": ids[0], "rating": 1}))
    widget.backend.feedback_count = 1
    widget.refresh()
    monkeypatch.setattr(widget._batch_timer, "start", lambda _: pytest.fail("Small update started batched rebuild"))
    finish(widget, pending)
    QCoreApplication.processEvents()
    assert widget._cards[ids[0]] is not before[ids[0]]
    assert all(widget._cards[cid] is card and not card.isHidden() for cid, card in before.items() if cid != ids[0])
    assert bar.value() == scroll
    assert [widget.flow.itemAt(i).widget() for i in range(widget.flow.count())] == list(widget._cards.values())


def test_card_creation_yields_between_bounded_batches_and_keeps_parents(db, page):
    widget, pending = page
    seed(db, 50)
    widget.on_show()
    fn, done, _ = pending[0]
    done(fn())
    assert widget._batch is not None
    assert 0 < len(widget._batch_new) <= 6
    assert all(card.parentWidget() is widget._holder and not card.isWindow()
               for card in widget._batch_new.values())
    pulse = []
    QTimer.singleShot(0, lambda: pulse.append(True))
    QCoreApplication.processEvents()
    assert pulse and widget._batch is not None
    drain_batch(widget)
    assert len(widget._cards) == 50


def test_deleted_page_ignores_late_load_completion_and_error(db, page):
    widget, pending = page
    seed(db)
    widget.on_show()
    fn, done, error = pending[0]
    result = fn()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(widget)
    done(result)
    error(RuntimeError("late failure"))


def test_worker_decodes_images_and_gui_card_build_has_no_file_queries(db, page, tmp_path, monkeypatch):
    widget, pending = page
    path = tmp_path / "thumb.png"
    image = QImage(100, 50, QImage.Format_RGB32)
    image.fill(0xff442288)
    assert image.save(str(path))
    ids = seed(db, 1, str(path))
    export = tmp_path / "export.mp4"
    export.touch()
    with db.session() as s:
        s.add(ExportedVideo(clip_id=ids[0], path=str(export)))
    threads = []
    def query(*args):
        threads.append(threading.get_ident())
    event.listen(db.engine, "before_cursor_execute", query)
    try:
        widget.on_show()
        fn, done, _ = pending[0]
        with ThreadPoolExecutor(1) as pool:
            data = pool.submit(fn).result(timeout=5)
        assert threads and all(t != threading.get_ident() for t in threads)
        assert isinstance(data[0][0][3], QImage) and not data[0][0][3].isNull()
        threads.clear()
        with monkeypatch.context() as patch:
            patch.setattr(library.Path, "exists", lambda _: pytest.fail("GUI thumbnail/export file query"))
            done(data)
            drain_batch(widget)
        assert not threads
    finally:
        event.remove(db.engine, "before_cursor_execute", query)


def test_load_failure_is_inline_and_retryable(page, monkeypatch):
    widget, pending = page
    monkeypatch.setattr(library.QMessageBox, "warning", lambda *args: pytest.fail("Loading failure popup"))
    widget.on_show()
    pending[0][2](RuntimeError("database unavailable"))
    assert "database unavailable" in widget.count.text()
    assert not widget._query_active and widget._dirty
    widget.on_show()
    assert len(pending) == 2
