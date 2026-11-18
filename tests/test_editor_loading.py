"""Editor background loads stay bounded and cannot update a stale/deleted selection."""
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from shiboken6 import isValid

from dogsapka_clipper.core.config import AppSettings
from dogsapka_clipper.processing import ffmpeg_utils
from dogsapka_clipper.processing.render import RenderSpec
from dogsapka_clipper.ui.pages import editor


@pytest.fixture
def loading(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    # Minimal widget-backed editor: no multimedia devices, database, or real FFmpeg processes.
    page = editor.EditorPage.__new__(editor.EditorPage)
    QWidget.__init__(page)
    page.backend = SimpleNamespace(paths=SimpleNamespace(temp=tmp_path / "work"), settings=AppSettings())
    page.clip_id, page.src, page.duration, page.position = 1, tmp_path / "first.mp4", 20, 1
    page.player = object()
    page.transcript = {"segments": [{"start": 0, "end": 2, "text": "old words"}]}
    page.sub_style = SimpleNamespace(currentData=lambda: "viral")
    page.sub_pos = SimpleNamespace(currentData=lambda: "lower")
    page.accent = SimpleNamespace(text=lambda: "#FFE600")
    page.before, page.after = QLabel(page), QLabel(page)
    page.timeline = QWidget(page)
    page.timeline.waveform = []
    page._preview_generation, page._waveform_generation = 0, 0
    page._preview_active = page._preview_pending = None
    page._waveform_active = page._waveform_pending = None
    page._cur_pos = lambda: page.position
    page._spec_for_preview = lambda ass: RenderSpec(src=page.src, out=tmp_path / "unused.mp4", width=360,
                                                  height=640, ass_path=ass)
    pending, frames, renders = [], [], []
    monkeypatch.setattr(editor, "run_async", lambda fn, done, err=None: pending.append((fn, done, err)))
    image = QPixmap(16, 9)
    image.fill(QColor("red"))
    picture = tmp_path / "image.png"
    assert image.save(str(picture))
    data = picture.read_bytes()

    def frame(src, time, out, **kwargs):
        frames.append((src, time, out))
        out.write_bytes(data)
        return out

    def render(spec, time, out):
        renders.append((spec.src, time, out, spec.ass_path.read_text() if spec.ass_path else ""))
        out.write_bytes(data)
        return out

    monkeypatch.setattr(ffmpeg_utils, "extract_frame", frame)
    monkeypatch.setattr(editor, "render_preview_frame", render)
    yield SimpleNamespace(page=page, pending=pending, frames=frames, renders=renders, data=data, tmp=tmp_path)
    if isValid(page):
        page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_preview_keeps_one_active_job_and_only_latest_pending_request(loading, monkeypatch):
    p = loading.page
    applied = []
    monkeypatch.setattr(p.after, "setPixmap", lambda image: applied.append(image))
    p.update_preview()
    for position in (2, 3, 4):
        p.position = position
        p.update_preview()
    assert len(loading.pending) == 1
    old = loading.pending[0]
    old[1](old[0]())
    assert not applied
    assert len(loading.pending) == 2
    latest = loading.pending[-1]
    latest[1](latest[0]())
    assert len(applied) == 1
    assert [time for _, time, _ in loading.frames] == [1, 4]
    assert p._preview_active is None and p._preview_pending is None
    assert len({out.parent for _, _, out in loading.frames}) == 2
    assert not list((loading.tmp / "work").iterdir())


def test_preview_captures_source_and_caption_snapshot_before_worker_runs(loading):
    p = loading.page
    original = p.src
    p.update_preview()
    p.src, p.clip_id = loading.tmp / "second.mp4", 2
    p.transcript["segments"][0]["text"] = "new words"
    p.update_preview()
    old = loading.pending[0]
    old[1](old[0]())
    assert loading.frames[0][0] == loading.renders[0][0] == original
    assert "OLD" in loading.renders[0][3] and "WORDS" in loading.renders[0][3] and "NEW" not in loading.renders[0][3]
    latest = loading.pending[-1]
    latest[1](latest[0]())
    assert loading.frames[-1][0] == p.src
    assert "NEW" in loading.renders[-1][3] and "OLD" not in loading.renders[-1][3]


def test_failed_preview_cleans_its_files_and_reports_inline(loading, monkeypatch):
    def fail(*args):
        raise RuntimeError("render failed")
    monkeypatch.setattr(editor, "render_preview_frame", fail)
    loading.page.update_preview()
    work = loading.pending[0]
    with pytest.raises(RuntimeError, match="render failed") as error:
        work[0]()
    work[2](error.value)
    assert loading.page.after.text() == "Preview failed:\nrender failed"
    assert loading.page._preview_active is None
    assert not list((loading.tmp / "work").iterdir())


def test_waveform_loads_only_latest_selection_and_cannot_apply_old_result(loading, monkeypatch):
    sources = []
    def decode(src, **kwargs):
        sources.append(src)
        return np.ones(8000, dtype=np.float32)
    monkeypatch.setattr(ffmpeg_utils, "decode_audio_pcm", decode)
    p = loading.page
    original = p.src
    p._load_waveform()
    for cid in (2, 3, 4):
        p.clip_id, p.src = cid, loading.tmp / f"clip-{cid}.mp4"
        p._load_waveform()
    assert len(loading.pending) == 1
    old = loading.pending[0]
    old[1](old[0]())
    assert p.timeline.waveform == []
    assert len(loading.pending) == 2
    latest = loading.pending[-1]
    latest[1](latest[0]())
    assert sources == [original, p.src]
    assert all(v > .99 for v in p.timeline.waveform)


@pytest.mark.parametrize("kind,error", [("preview", False), ("preview", True), ("waveform", False), ("waveform", True)])
def test_late_background_callbacks_ignore_deleted_editor(loading, kind, error):
    p = loading.page
    if kind == "preview":
        p.update_preview()
    else:
        p._load_waveform()
    work = loading.pending[0]
    p.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(p)
    if error:
        work[2](RuntimeError("late failure"))
    else:
        work[1]((loading.data, loading.data) if kind == "preview" else [1.0])
    assert len(loading.pending) == 1


def test_scheduled_load_methods_ignore_deleted_editor(loading):
    p = loading.page
    p.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    p.update_preview()
    p._load_waveform()
    p.reanalyze()
    assert loading.pending == []
