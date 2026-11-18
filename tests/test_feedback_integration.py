import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from dogsapka_clipper.core.i18n import tr
from dogsapka_clipper.core.paths import AppPaths
from dogsapka_clipper.core.secrets import SecretStore
from dogsapka_clipper.db.models import AppSetting, Clip, Highlight, MonitoringSession, Streamer
from dogsapka_clipper.detection.preferences import feedback_key
from dogsapka_clipper.services.backend import Backend
from dogsapka_clipper.ui.bridge import QtBridge
from dogsapka_clipper.ui.pages.library import LibraryPage

FUNNY = {"audio": .8, "chat_reactions": .9, "chat_activity": .6, "visual": .3,
         "laughter": .8, "voice_reaction": .8, "sustained_reaction": .8}


@pytest.fixture
def backend(tmp_path):
    b = Backend(AppPaths.create(tmp_path / "data"), secrets=SecretStore(tmp_path/"secrets", use_keyring=False))
    yield b
    b.loop.close()
    b.db.close()


def clips(b, count=4, simulation=False, duplicate=None, signals=None):
    with b.db.session() as s:
        streamer = Streamer(login=f"channel-{b.db.count(Streamer)}", display_name="Channel")
        s.add(streamer)
        s.flush()
        session = MonitoringSession(streamer_id=streamer.id, is_simulation=simulation)
        s.add(session)
        s.flush()
        ids = []
        for _ in range(count):
            highlight = Highlight(session_id=session.id, event_ts=10, start_ts=0, end_ts=21,
                                  score=75, signals=FUNNY if signals is None else signals)
            s.add(highlight)
            s.flush()
            clip = Clip(streamer_id=streamer.id, highlight_id=highlight.id, title="A funny moment",
                        source_path=str(b.paths.clips / "missing.mp4"), duration=21, duplicate_of=duplicate)
            s.add(clip)
            s.flush()
            ids.append(clip.id)
    return ids


def test_latest_rating_is_saved_and_rebuilt_without_double_training(backend):
    ids = clips(backend)
    for cid in ids:
        backend.set_clip_feedback(cid, 1)
    assert backend.feedback_count == 4
    liked = backend._preference_adjustment(FUNNY)
    assert liked > 0
    backend.set_clip_feedback(ids[0], 1)
    assert backend.feedback_count == 4 and backend._preference_adjustment(FUNNY) == liked
    backend.set_clip_feedback(ids[0], -1)
    assert backend.feedback_count == 4 and backend._preference_adjustment(FUNNY) < liked
    backend.set_clip_feedback(ids[0], 0)
    assert backend.feedback_count == 3 and backend._preference_adjustment(FUNNY) == 0


def test_learning_survives_restart_and_render_edits(backend, tmp_path):
    ids = clips(backend)
    for cid in ids:
        backend.set_clip_feedback(cid, 1)
    with backend.db.session() as s:
        s.get(Clip, ids[0]).edit_state = {"analysis": {"version": 3}}  # render worker replaces edits
    other = Backend(backend.paths, secrets=SecretStore(tmp_path/"other-secrets", use_keyring=False))
    try:
        assert other.feedback_count == 4
        assert other._preference_adjustment(FUNNY) == backend._preference_adjustment(FUNNY)
    finally:
        other.loop.close()
        other.db.close()
    backend.delete_clip(ids[0])
    assert backend.feedback_count == 3
    with backend.db.session() as s:
        assert s.get(AppSetting, feedback_key(ids[0])) is None


def test_render_duplicate_discovery_removes_old_learning_snapshot(backend):
    ids = clips(backend)
    for cid in ids:
        backend.set_clip_feedback(cid, 1)
    assert backend.feedback_count == 4
    with backend.db.session() as s:
        s.get(Clip, ids[0]).duplicate_of = ids[1]
    backend.bus.emit("video.qc_failed", "", clip_id=ids[0])
    assert backend.feedback_count == 3
    assert backend._preference_adjustment(FUNNY) == 0


def test_rating_and_deletion_share_lock_so_labels_cannot_outlive_clip(backend, monkeypatch):
    cid = clips(backend, 1)[0]
    rating_committed, release_rating, deletion_entered = threading.Event(), threading.Event(), threading.Event()
    deleting_thread = []
    original_session, original_reload = backend.db.session, backend._reload_preferences

    @contextmanager
    def observed_session():
        if deleting_thread and threading.get_ident() == deleting_thread[0]:
            deletion_entered.set()
        with original_session() as s:
            yield s

    def paused_reload():
        rating_committed.set()
        assert release_rating.wait(5)
        original_reload()

    def delete():
        deleting_thread.append(threading.get_ident())
        backend.delete_clip(cid)

    monkeypatch.setattr(backend.db, "session", observed_session)
    monkeypatch.setattr(backend, "_reload_preferences", paused_reload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rated = pool.submit(backend.set_clip_feedback, cid, 1)
        assert rating_committed.wait(5)
        removed = pool.submit(delete)
        try:
            assert not deletion_entered.wait(.1)
        finally:
            release_rating.set()
        rated.result(timeout=5)
        removed.result(timeout=5)
    assert backend.feedback_count == 0
    with original_session() as s:
        assert s.get(Clip, cid) is None and s.get(AppSetting, feedback_key(cid)) is None


@pytest.mark.parametrize("opts", [{"simulation": True}, {"duplicate": 99}, {"signals": {}}])
def test_simulation_duplicates_and_missing_evidence_do_not_train(backend, opts):
    cid = clips(backend, 1, **opts)[0]
    with pytest.raises(ValueError):
        backend.set_clip_feedback(cid, 1)
    assert backend.feedback_count == 0


@pytest.mark.parametrize("rating", [2, "1", True, None])
def test_invalid_rating_is_rejected(backend, rating):
    with pytest.raises(ValueError):
        backend.set_clip_feedback(123, rating)


def test_library_feedback_buttons_rate_and_clear_without_publishing(backend):
    app = QApplication.instance() or QApplication([])
    cid = clips(backend, 1)[0]
    bridge = QtBridge(backend.bus)
    page = LibraryPage(SimpleNamespace(backend=backend, bridge=bridge, open_editor=lambda _: None))
    try:
        page.on_show()
        good = next(b for b in page.findChildren(QPushButton) if b.text() == tr("lib.good_clip"))
        assert good.isEnabled()
        good.click()
        assert backend.feedback_count == 1
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        good = next(b for b in page.findChildren(QPushButton) if b.text() == tr("lib.good_clip"))
        assert good.isChecked()
        good.click()
        assert backend.feedback_count == 0
        assert backend.jobs.pending_count() == 0
    finally:
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        bridge.close()
