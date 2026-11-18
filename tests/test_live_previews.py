"""Late preview results must not access widgets removed during a grid rebuild."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from dogsapka_clipper.core.events import EventBus  # noqa: E402
from dogsapka_clipper.ui.bridge import QtBridge  # noqa: E402
from dogsapka_clipper.ui.pages import live  # noqa: E402


@pytest.fixture
def previews(monkeypatch, db, tmp_path):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(live, "run_async", lambda fn, done, err: pending.append((done, err)))
    backend = SimpleNamespace(db=db, sessions={}, paths=SimpleNamespace(temp=tmp_path))
    bridge = QtBridge(EventBus())
    page = live.LivePage(SimpleNamespace(backend=backend, bridge=bridge))
    page.timer.stop()
    backend.sessions[1] = SimpleNamespace(buffer=SimpleNamespace(
        segments=[SimpleNamespace(path=tmp_path / "segment.ts", duration=5)]))
    streamers = [(1, "First")]
    monkeypatch.setattr(page, "_live_streamers", lambda: list(streamers))
    page.rebuild_tiles()
    frame = tmp_path / "frame.png"
    pm = QPixmap(16, 9)
    pm.fill(QColor("red"))
    assert pm.save(str(frame))
    yield page, pending, streamers, frame
    if isValid(page):
        page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    bridge.close()


@pytest.mark.parametrize("delete_first", [False, True])
def test_late_preview_after_grid_rebuild(previews, delete_first):
    page, pending, streamers, frame = previews
    old_tile = page.tiles[1]
    old_done = pending[0][0]
    streamers.append((2, "Second"))
    page.rebuild_tiles()
    replacement = page.tiles[1]
    assert replacement is not old_tile
    if delete_first:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(old_tile.preview)
    old_done(frame)
    assert replacement.busy
    assert replacement.preview.pixmap().isNull()
    if not delete_first:
        assert old_tile.preview.pixmap().isNull()
    pending[-1][0](frame)
    assert not replacement.busy
    assert not replacement.preview.pixmap().isNull()


def test_preview_after_page_deleted(previews):
    page, pending, _, frame = previews
    preview = page.tiles[1].preview
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(preview)
    pending[0][0](frame)


def test_current_preview_failure_allows_retry(previews):
    page, pending, _, _ = previews
    pending[0][1](RuntimeError("extraction failed"))
    assert not page.tiles[1].busy
    page._refresh_tile_previews()
    assert len(pending) == 2
