import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dogsapka_clipper.processing import ffmpeg_utils as media
from dogsapka_clipper.processing import layout
from dogsapka_clipper.processing.render import RenderSpec, build_video_filter, render_preview_frame
from dogsapka_clipper.processing.subtitles import write_ass


@pytest.fixture
def fake_probe(monkeypatch):
    media._probe_cached.cache_clear()
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"format": {"duration": "3"},
            "streams": [{"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "30/1"}]}).encode(), b"")
    monkeypatch.setattr(media, "run", run)
    monkeypatch.setattr(media, "tool_cmd", lambda _: ["ffprobe"])
    yield calls
    media._probe_cached.cache_clear()


def test_probe_reuses_metadata_but_not_mutable_objects(fake_probe, tmp_path):
    path = tmp_path / "clip.mp4"
    path.touch()
    first = media.probe(path)
    first.width = 1
    first.raw["streams"][0]["height"] = 1
    second = media.probe(str(path))
    assert len(fake_probe) == 1
    assert second.width == 640 and second.raw["streams"][0]["height"] == 360


def test_probe_invalidated_when_file_changes_or_is_replaced(fake_probe, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"old")
    media.probe(path)
    stamp = path.stat().st_mtime_ns
    path.write_bytes(b"new")
    os.utime(path, ns=(stamp, stamp + 1_000_000))
    media.probe(path)
    path.unlink()
    path.write_bytes(b"replacement")
    media.probe(path)
    assert len(fake_probe) == 3


def test_probe_cache_is_bounded(fake_probe, tmp_path):
    for i in range(130):
        path = tmp_path / f"{i}.mp4"
        path.touch()
        media.probe(path)
    assert media._probe_cached.cache_info().currsize == 128
    media.probe(tmp_path / "0.mp4")
    assert len(fake_probe) == 131


def test_non_file_probe_results_are_not_cached(fake_probe, tmp_path):
    media.probe(tmp_path)
    media.probe(tmp_path)
    assert len(fake_probe) == 2
    assert media._probe_cached.cache_info().currsize == 0


def test_disabled_face_detection_never_loads_model(monkeypatch):
    frames = [(i, np.zeros((90, 160, 3), np.uint8)) for i in range(3)]
    monkeypatch.setattr(layout, "sample_frames", lambda *a, **k: (frames, 640, 360))
    def unexpected():
        pytest.fail("Disabled face detection must not construct the face model")
    monkeypatch.setattr(layout, "FaceDetector", unexpected)
    result = layout.analyze_layout(Path("clip.mp4"), detect_faces=False)
    assert result.faces_found == 0 and result.facecam is None


@pytest.fixture(scope="module", params=["cfr", "vfr"])
def preview_source(tmp_path_factory, request):
    path = tmp_path_factory.mktemp("preview-timing") / "source.mp4"
    args = media.tool_cmd("ffmpeg") + ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=60:d=10"]
    if request.param == "vfr":
        args += ["-vf", "select='if(lt(t,5),1,not(mod(n,10)))'", "-fps_mode", "vfr"]
    media.run(args + ["-c:v", "libx264", "-preset", "ultrafast", str(path)])
    return path


@pytest.mark.parametrize("t", [0.0, 2.0, 2.4, 2.417, 8.001])
def test_fast_preview_matches_full_decode_with_caption_and_moving_crop(preview_source, tmp_path, t):
    pytest.importorskip("cv2")
    import cv2
    ass = write_ass([{"start": 1.5, "end": 3.0, "word": "TIMING"}], tmp_path / "captions.ass", 360, 640)
    an = layout.LayoutAnalysis("gameplay", 640, 360, crop_track=[(0, .2), (3, .8)])
    spec = RenderSpec(src=preview_source, out=tmp_path/"unused.mp4", width=360, height=640,
                      analysis=an, dynamic_focus=True, ass_path=ass)
    info = media.probe(spec.src)
    reference = tmp_path / "reference.png"
    media.run(media.tool_cmd("ffmpeg") + ["-y", "-loglevel", "error", "-i", str(spec.src),
              "-filter_complex", build_video_filter(spec, info, info.fps), "-map", "[vout]", "-ss", str(t),
              "-frames:v", "1", str(reference)])
    actual = render_preview_frame(spec, t, tmp_path / "actual.png")
    a, b = cv2.imread(str(reference)), cv2.imread(str(actual))
    assert a.shape == b.shape == (640, 360, 3)
    assert np.abs(a.astype(float)-b).mean() < 1.0
