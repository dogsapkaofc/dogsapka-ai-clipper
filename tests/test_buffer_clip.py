from pathlib import Path

import pytest

from dogsapka_clipper.recording.buffer import Segment, SegmentRingBuffer, WallClockMapper
from dogsapka_clipper.recording.clipper import extract_clip, plan_window
from dogsapka_clipper.processing.ffmpeg_utils import FFmpegError, probe, run, tool_cmd


def seg(tmp_path, i, start, dur=4.0):
    p = tmp_path / f"s{i}.ts"
    p.write_bytes(b"x")
    return Segment(p, start, dur, i)


def test_ring_buffer_eviction(tmp_path):
    deleted = []
    b = SegmentRingBuffer(20, on_delete=deleted.append)
    for i in range(20):
        b.add(seg(tmp_path, i, i * 4.0))
    assert b.covered_seconds() <= 28
    assert b.latest_end() == 80
    assert len(deleted) == 20 - len(b)
    assert not deleted[0].path.exists()


def test_ring_buffer_protection(tmp_path):
    b = SegmentRingBuffer(12)
    tok = b.protect(0, 10)
    for i in range(15):
        b.add(seg(tmp_path, i, i * 4.0))
    assert b.range(0, 10) and b.range(0, 10)[0].start == 0
    b.release(tok)
    assert not b.range(0, 10)


def test_wallclock_mapper_uses_min_delay():
    m = WallClockMapper()
    m.observe(4.0, 1004.5)
    m.observe(8.0, 1008.2)
    m.observe(12.0, 1013.0)
    assert abs(m.to_wall(0) - 1000.2) < 1e-6


@pytest.mark.parametrize("pre,post,mn,mx", [(25, 25, 15, 90), (5, 2, 30, 60), (60, 60, 15, 40), (0, 0, 15, 180)])
def test_plan_window_never_starts_after_event(pre, post, mn, mx):
    s, e = plan_window(1000.0, pre, post, mn, mx)
    assert s < 1000.0 < e
    assert mn - 1e-6 <= e - s <= mx + 1e-6


def test_plan_window_example_from_spec():
    s, e = plan_window(9340.0, 25, 25, 15, 90)
    assert (s, e) == (9315.0, 9365.0)


@pytest.fixture
def segments(sample_video, tmp_path):
    out = tmp_path / "segs"
    out.mkdir()
    run(tool_cmd("ffmpeg") + ["-y", "-loglevel", "error", "-i", str(sample_video.path), "-c", "copy", "-f", "segment",
                              "-segment_time", "4", "-segment_list", str(out / "l.csv"), "-segment_list_type", "csv",
                              str(out / "seg_%03d.ts")])
    segs = []
    for i, line in enumerate((out / "l.csv").read_text().splitlines()):
        name, a, b = line.split(",")
        segs.append(Segment(out / name, 1000 + float(a), float(b) - float(a), i))
    return segs


def test_extract_clip_accurate(segments, tmp_path):
    res = extract_clip(segments, 1010.3, 1027.1, tmp_path / "c.mp4", tmp_path / "tmp")
    info = probe(res.path)
    assert abs(info.duration - 16.8) < 0.2
    assert info.has_audio and info.has_video and res.reencoded


def test_extract_clip_skips_corrupt_segment(segments, tmp_path):
    segments[3].path.write_bytes(b"\x00" * 4096)   # simulate an interrupted/corrupt segment
    res = extract_clip(segments, 1000, 1030, tmp_path / "c.mp4", tmp_path / "tmp")
    assert res.duration > 20


def test_extract_clip_no_segments(tmp_path):
    with pytest.raises(FFmpegError):
        extract_clip([], 0, 10, tmp_path / "c.mp4", tmp_path / "tmp")


def test_preview_frame_from_ts_segment(segments, tmp_path):
    from dogsapka_clipper.processing.ffmpeg_utils import extract_frame
    seg = segments[-2]   # TS segment whose timestamps do not start at zero
    out = extract_frame(seg.path, seg.duration - 0.5, tmp_path / "f.jpg")
    assert out.stat().st_size > 1000


def test_clip_keeps_audio_video_sync(segments, tmp_path):
    """Regression: joining TS segments with the concat demuxer drifted audio by ~0.5 s."""
    from av_sync import av_offsets_ms, flash_times
    res = extract_clip(segments, 1012.4, 1036.0, tmp_path / "c.mp4", tmp_path / "tmp")
    offs = av_offsets_ms(res.path)
    assert offs and all(abs(o) < 45 for o in offs), offs
    assert abs(flash_times(res.path)[0] - (22.0 - 12.4)) < 0.1   # the moment lands at the right time


def test_render_with_silence_cuts_keeps_sync(segments, tmp_path):
    from av_sync import av_offsets_ms
    from dogsapka_clipper.processing.render import RenderSpec, render
    clip = extract_clip(segments, 1010, 1034, tmp_path / "c.mp4", tmp_path / "tmp").path
    out = tmp_path / "r.mp4"
    render(RenderSpec(src=clip, out=out, width=360, height=640, crf=32,
                      keep_ranges=[(0, 3), (4, 6), (7, 9), (10, 22)]))
    offs = av_offsets_ms(out)
    assert offs and all(abs(o) < 45 for o in offs), offs
