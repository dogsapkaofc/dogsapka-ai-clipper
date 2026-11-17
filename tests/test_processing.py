import numpy as np
import pytest

from dogsapka_clipper.processing.analysis import (dhash, hamming, is_duplicate, keep_ranges_without_silence, time_overlap)
from dogsapka_clipper.processing.ffmpeg_utils import find_tool, probe, run, tool_cmd
from dogsapka_clipper.processing.layout import LayoutAnalysis, Rect, analyze_layout
from dogsapka_clipper.processing.metadata import generate_metadata
from dogsapka_clipper.processing.qc import check_video
from dogsapka_clipper.processing.ranking import quality_score
from dogsapka_clipper.processing.render import RenderSpec, render
from dogsapka_clipper.processing.subtitles import (build_captions, remap_time, shift_words, transcript_words, write_ass,
                                               write_srt)

WORDS = [{"start": 0.5 + i * 0.4, "end": 0.85 + i * 0.4, "word": w, "prob": 0.9}
         for i, w in enumerate("no way that was absolutely insane chat clip it".split())]


def test_captions_and_srt(tmp_path):
    caps = build_captions(WORDS, max_words=3)
    assert all(len(c.words) <= 3 for c in caps)
    assert caps[0].start == 0.5
    p = write_srt(caps, tmp_path / "a.srt")
    txt = p.read_text()
    assert "00:00:00,500 --> " in txt and "no way that" in txt


def test_ass_styles(tmp_path):
    for style in ("viral", "clean", "dynamic", "minimal"):
        p = write_ass(WORDS, tmp_path / f"{style}.ass", 1080, 1920, style=style)
        t = p.read_text()
        assert "PlayResY: 1920" in t and "Dialogue:" in t
    viral = (tmp_path / "viral.ass").read_text()
    assert "&H0000E6FF" in viral   # yellow accent (BGR) highlights the active word
    assert "INSANE" in viral


def test_remap_time_with_silence_cut():
    keep = [(0, 5), (8, 12)]
    assert remap_time(3, keep) == 3
    assert remap_time(6, keep) is None
    assert remap_time(9, keep) == 6
    words = shift_words([{"start": 9, "end": 9.5, "word": "x"}], 0, 20, keep)
    assert words[0]["start"] == 6


def test_transcript_words_without_timing():
    w = transcript_words({"segments": [{"start": 0, "end": 2, "text": "hello there"}]})
    assert [x["word"] for x in w] == ["hello", "there"] and w[1]["start"] == 1.0


def test_silence_ranges():
    sr = 16000
    loud = 0.3 * np.sin(np.arange(sr * 3) / sr * 2 * np.pi * 200)
    quiet = np.zeros(sr * 4)
    pcm = np.concatenate([loud, quiet, loud]).astype(np.float32)
    keep = keep_ranges_without_silence(pcm, 10.0)
    assert keep and len(keep) == 2 and 2.9 <= keep[0][1] <= 3.4
    assert keep_ranges_without_silence(pcm, 10.0, protect=(2.0, 8.0)) is None


def test_duplicates():
    assert time_overlap((0, 10), (5, 15)) == 0.5
    assert is_duplicate((0, 30), (5, 30))
    assert not is_duplicate((0, 30), (40, 70))
    img = np.random.default_rng(0).integers(0, 255, (36, 64)).astype(np.uint8)
    assert hamming(dhash(img), dhash(img)) == 0


def test_ranking_and_duplicate_penalty():
    q, parts = quality_score(90, {"audio": 1.0}, {"segments": [{"start": 0, "end": 20, "confidence": 0.9}]}, 30,
                             1920, 1080)
    q2, _ = quality_score(90, {"audio": 1.0}, None, 30, 1920, 1080, duplicate=True)
    assert 0 <= q2 < q <= 100


def test_metadata_grounded_and_bilingual():
    tr = {"segments": [{"start": 0, "end": 2, "text": "No way, that was insane!", "confidence": 0.9}]}
    m = generate_metadata("Streamer", "streamer", "Valorant", ["Laughter / excited voice"], tr, "en")
    assert "Streamer" in m["title"] and "twitch.tv/streamer" in m["description"]
    assert "#valorant" in m["hashtags"]
    h = generate_metadata("Streamer", "streamer", "", ["Chat spike"], None, "hu")
    assert "Forrás" in h["description"]
    # no quote is fabricated when there is no transcript
    assert '"' not in h["title"]


@pytest.mark.parametrize("layout", ["crop", "blur", "facecam_top", "source"])
def test_render_layouts_and_qc(sample_video, tmp_path, layout):
    out = tmp_path / f"{layout}.mp4"
    an = LayoutAnalysis("facecam", 640, 360, facecam=Rect(0, 0, 160, 120), crop_track=[(0, 0.3), (10, 0.7)])
    ass = write_ass(WORDS, tmp_path / "s.ass", 360, 640) if layout != "source" else None
    spec = RenderSpec(src=sample_video.path, out=out, width=360, height=640, crf=30, trim_start=2, trim_end=12,
                      layout=layout, analysis=an, ass_path=ass, zoom_at=3.0 if layout == "crop" else None,
                      fade=layout == "blur", keep_ranges=[(0, 4), (6, 10)] if layout == "facecam_top" else None)
    prog = []
    render(spec, progress=prog.append)
    info = probe(out)
    exp = (640, 360) if layout == "source" else (360, 640)
    assert (info.width, info.height) == exp
    expected_dur = 8.0 if layout == "facecam_top" else 10.0
    rep = check_video(out, *exp, expected_duration=expected_dur)
    assert rep.passed, rep.issues
    assert prog and prog[-1] == 1.0


def test_render_does_not_invent_frames(sample_video, tmp_path):
    out = tmp_path / "o.mp4"
    render(RenderSpec(src=sample_video.path, out=out, width=360, height=640, fps=60, crf=32, trim_end=3))
    assert probe(out).fps <= 30.5   # 30 fps source is not "upgraded" to 60 fps


@pytest.mark.skipif(not find_tool("ffmpeg"), reason="FFmpeg not installed")
def test_qc_detects_problems(tmp_path):
    p = tmp_path / "black.mp4"
    run(tool_cmd("ffmpeg") + ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=3",
                              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
    rep = check_video(p, 1080, 1920, expected_duration=10)
    joined = " ".join(rep.issues)
    assert not rep.passed
    assert "black" in joined and "dimensions" in joined and "audio" in joined and "duration" in joined


def test_layout_analysis_runs(sample_video):
    a = analyze_layout(sample_video.path)
    assert a.width == 640 and a.kind in ("gameplay", "desktop", "facecam", "camera")
    assert a.recommended_layout() in ("crop", "blur", "facecam_top", "camera")


def test_transcriber_falls_back_to_cpu_when_cuda_libs_missing(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from dogsapka_clipper.processing.subtitles import Transcriber, TranscriptionUnavailable

    class FakeModel:
        def __init__(self, device):
            self.device = device

        def transcribe(self, *a, **k):
            if self.device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return iter([]), SimpleNamespace(duration=1.0, language="en", language_probability=0.9)

    def fake_model(self, device=None):
        d = device or self.device
        if d == "auto":
            d = "cpu" if Transcriber._cuda_broken else "cuda"
        return FakeModel(d)

    monkeypatch.setattr(Transcriber, "_model", fake_model)
    monkeypatch.setattr(Transcriber, "_cuda_broken", None)
    assert Transcriber("tiny", "auto").transcribe(tmp_path / "x.wav")["language"] == "en"
    assert Transcriber._cuda_broken  # remembered: later "auto" jobs go straight to CPU
    assert Transcriber("tiny", "auto").transcribe(tmp_path / "x.wav")["language"] == "en"
    with pytest.raises(TranscriptionUnavailable):
        Transcriber("tiny", "cuda").transcribe(tmp_path / "x.wav")
