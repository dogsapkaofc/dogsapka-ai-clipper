import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QTime
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTimeEdit

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.core.paths import AppPaths
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.models import PublishAccount
from dogsapka_clipper.services.backend import Backend
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages import settings


def test_schedule_controls_collect_save_and_reject_duplicates(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    b = Backend(AppPaths.create(tmp_path / "data"), secrets=SecretStore(tmp_path / "tokens", use_keyring=False))
    bridge = QtBridge(b.bus)
    with b.db.session() as s:
        s.add(PublishAccount(platform="youtube", name="Demo", token_key="demo"))
    monkeypatch.setattr(settings, "run_async", lambda fn, done, *a: done(None))
    page = settings.SettingsPage(SimpleNamespace(backend=b, bridge=bridge, toast=lambda _: None))
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: errors.append(a))
    try:
        assert len(page.findChildren(QTimeEdit)) == 3
        assert not page.w["yt_schedule"].isChecked()
        assert page.w["yt_timezone"].text() == "Europe/Budapest"
        assert any(btn.text() == tr("yt.reauthorize") for btn in page.findChildren(QPushButton))
        page.w["yt_schedule"].setChecked(True)
        page.w["yt_time_0"].setTime(QTime(8, 30))
        assert page.save(quiet=True)
        assert b.db.load_settings().publishing.youtube_schedule_times == ["08:30", "15:00", "21:00"]
        page.w["yt_time_1"].setTime(QTime(8, 30))
        assert page.save(quiet=True) is False
        assert errors and b.settings.publishing.youtube_schedule_times == ["08:30", "15:00", "21:00"]
    finally:
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        bridge.close()
        b.loop.close()
        b.db.close()
