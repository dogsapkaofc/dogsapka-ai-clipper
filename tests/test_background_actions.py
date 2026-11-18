import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton)
from shiboken6 import isValid

from dogsapka_clipper.core.events import EventBus
from dogsapka_clipper.db.models import Streamer
from dogsapka_clipper.ui import publish_dialog
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages import streamers


@pytest.fixture
def upload(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending, calls, questions = [], [], []
    dialog = publish_dialog.PublishDialog.__new__(publish_dialog.PublishDialog)
    QDialog.__init__(dialog)
    def upload_now(*args, **kwargs):
        calls.append(args)
        args[5](.5)
        return dict(platform="youtube", account="Demo", id="123", privacy="public")
    dialog.backend = SimpleNamespace(accounts=SimpleNamespace(upload_now=upload_now), bus=EventBus())
    dialog.export_id, dialog._uploading = 7, False
    cb, mode = QCheckBox(dialog), QComboBox(dialog)
    cb.setChecked(True)
    mode.addItem("public", "public")
    dialog.targets = [(3, cb, mode)]
    dialog.confirm = QCheckBox(dialog)
    dialog.confirm.setChecked(True)
    dialog.title, dialog.desc, dialog.tags = QLineEdit("Title", dialog), QPlainTextEdit(dialog), QLineEdit(dialog)
    dialog.go, dialog.bar, dialog.status = QPushButton(dialog), QProgressBar(dialog), QLabel(dialog)
    dialog.prog = publish_dialog._Prog()
    dialog.prog.value.connect(dialog.bar.setValue)
    monkeypatch.setattr(publish_dialog, "run_async", lambda fn, done, error: pending.append((fn, done, error)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: questions.append(a) or QMessageBox.Yes)
    yield dialog, pending, calls, questions
    if isValid(dialog):
        dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_upload_has_one_task_and_inline_progress_not_repeated_windows(upload):
    dialog, pending, calls, questions = upload
    dialog.upload()
    dialog.upload()
    assert len(pending) == len(questions) == 1
    assert not dialog.go.isEnabled() and not dialog.bar.isHidden()
    result = pending[0][0]()
    pending[0][1](result)
    assert len(calls) == 1 and dialog.go.isEnabled() and dialog.bar.isHidden()
    assert "uploaded" in dialog.status.text()


def test_upload_completion_does_not_touch_destroyed_dialog(upload):
    dialog, pending, _, _ = upload
    dialog.upload()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    result = pending[0][0]()
    pending[0][1](result)
    pending[0][2](RuntimeError("late error"))
    assert any(ev.kind == "video.published" for ev in dialog.backend.bus.history)


def test_monitoring_toggle_runs_once_in_background_and_keeps_other_cards(db, monkeypatch):
    app = QApplication.instance() or QApplication([])
    with db.session() as s:
        s.add_all([Streamer(login="first"), Streamer(login="second")])
    bus, pending, calls = EventBus(), [], []
    def change(sid, enabled):
        calls.append((sid, enabled))
        with db.session() as s:
            s.get(Streamer, sid).monitoring_enabled = enabled
    b = SimpleNamespace(db=db, sessions={}, twitch_status=lambda: "not_configured",
                        youtube_api_key=lambda: None, set_monitoring=change)
    bridge = QtBridge(bus)
    monkeypatch.setattr(streamers, "run_async", lambda fn, done, error: pending.append((fn, done, error)))
    page = streamers.StreamersPage(SimpleNamespace(backend=b, bridge=bridge))
    try:
        first, second = page._cards[1], page._cards[2]
        page.set_monitoring(1, False)
        page.set_monitoring(1, False)
        assert len(pending) == 1 and not calls
        assert page._cards[1] is first and page._cards[2] is second
        assert not first.monitor_button.isEnabled()
        pending[0][1](pending[0][0]())
        assert calls == [(1, False)]
        assert page._cards[2] is second and page._cards[1].monitor_button.isEnabled()
    finally:
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        bridge.close()
