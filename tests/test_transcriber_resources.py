"""Model reuse, cache memory bounds and cancellation before expensive inference."""
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from dogsapka_clipper.processing.subtitles import Transcriber


@pytest.fixture
def models(monkeypatch):
    created = []

    class Model:
        def __init__(self, name, **kwargs):
            self.name, self.options = name, kwargs
            created.append(self)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(Transcriber, "_cache", OrderedDict())
    monkeypatch.setattr(Transcriber, "_cuda_broken", None)
    return created


def test_recent_model_reused_and_old_models_evicted(models, tmp_path):
    small = Transcriber("small", "cpu", tmp_path)._model()
    Transcriber("tiny", "cpu", tmp_path)._model()
    assert Transcriber("small", "cpu", tmp_path)._model() is small
    Transcriber("medium", "cpu", tmp_path)._model()
    assert len(Transcriber._cache) == 2
    assert Transcriber("small", "cpu", tmp_path)._model() is small
    Transcriber("tiny", "cpu", tmp_path)._model()
    assert [m.name for m in models] == ["small", "tiny", "medium", "tiny"]
    # References held by running work remain valid even when removed from the cache.
    assert small.name == "small"


def test_same_model_is_constructed_once_for_concurrent_workers(models, tmp_path):
    barrier = threading.Barrier(4)

    def get_model(_):
        barrier.wait(timeout=5)
        return Transcriber("small", "cpu", tmp_path)._model()

    with ThreadPoolExecutor(max_workers=4) as pool:
        loaded = list(pool.map(get_model, range(4)))
    assert len(models) == 1
    assert all(m is loaded[0] for m in loaded)


def test_model_directories_do_not_share_cached_model(models, tmp_path):
    first = Transcriber("small", "cpu", tmp_path / "first")._model()
    second = Transcriber("small", "cpu", tmp_path / "second")._model()
    assert first is not second
    assert first.options["download_root"] != second.options["download_root"]


def test_cancelled_job_does_not_load_or_download_model(models):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RuntimeError, match="Cancelled"):
        Transcriber("small", "cpu").transcribe(Path("unused.mp4"), cancel=cancel)
    assert not models


def test_cancel_between_segments_stops_generator_before_next_inference(monkeypatch):
    cancel = threading.Event()
    advanced = []
    closed = []

    def segments():
        try:
            advanced.append(1)
            yield SimpleNamespace(start=0, end=1, text="hello", words=[])
            advanced.append(2)
            yield SimpleNamespace(start=1, end=2, text="world", words=[])
        finally:
            closed.append(True)

    model = SimpleNamespace(transcribe=lambda *a, **kw: (
        segments(), SimpleNamespace(duration=2, language="en", language_probability=.9)))
    monkeypatch.setattr(Transcriber, "_model", lambda self: model)
    with pytest.raises(RuntimeError, match="Cancelled"):
        Transcriber().transcribe(Path("unused.mp4"), cancel=cancel, progress=lambda _: cancel.set())
    assert advanced == [1]
    assert closed == [True]


def test_face_detector_is_reused_per_worker_and_not_shared(monkeypatch):
    from dogsapka_clipper.core.config import AppSettings
    from dogsapka_clipper.processing import pipeline

    monkeypatch.setattr(pipeline, "FaceDetector", type("FakeDetector", (), {}))
    settings = AppSettings()
    settings.ai.face_detection = True
    processor = pipeline.ClipProcessor(None, None, None, lambda: settings)
    barrier = threading.Barrier(2)

    def get_detector(_):
        first = processor._detector()
        barrier.wait(timeout=5)
        assert processor._detector() is first
        return first

    with ThreadPoolExecutor(max_workers=2) as pool:
        detectors = list(pool.map(get_detector, range(2)))
    assert detectors[0] is not detectors[1]
