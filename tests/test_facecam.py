"""Facecam detection -> facecam-top / gameplay-bottom layout."""
import random

import pytest

from dogsapka_clipper.processing.ffmpeg_utils import probe, run, tool_cmd
from dogsapka_clipper.processing.layout import LayoutAnalysis, Rect, analyze_layout
from dogsapka_clipper.processing.profiles import builtin

CAM = Rect(0, 400, 427, 320)          # facecam rectangle in the 1280x720 source


def make_stream(path, border: bool, cam: Rect | None = None):
    global CAM
    if cam is not None:
        CAM = cam
    """Moving 'gameplay' with a facecam overlay (different moving content) in the bottom-left corner."""
    cam = f"[1:v]scale={CAM.w}:{CAM.h}" + (",drawbox=x=0:y=0:w=iw:h=ih:color=white:t=4" if border else "") + "[cam]"
    run(tool_cmd("ffmpeg") + ["-y", "-loglevel", "error",
                              "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8",
                              "-f", "lavfi", "-i", "life=s=160x120:r=30:mold=10:ratio=0.1:seed=1:death_color=#202060:life_color=#c0a080",
                              "-filter_complex", f"{cam};[0:v][cam]overlay={CAM.x}:{CAM.y}:shortest=1",
                              "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)])
    return path


class FakeFaceDetector:
    """Stands in for the face model: the streamer's face inside the cam in most frames, plus
    occasional false positives elsewhere (e.g. a game character)."""
    available = True

    def __init__(self):
        self.rng = random.Random(3)

    def detect(self, rgb):
        h, w = rgb.shape[:2]
        k = w / 1280
        out = []
        if self.rng.random() < 0.8:
            j = self.rng.uniform(-4, 4)
            out.append((int((CAM.x + 170 + j) * k), int((CAM.y + 110 + j) * k), int(90 * k), int(90 * k)))
        if self.rng.random() < 0.3:
            out.append((int(self.rng.uniform(500, 1100) * k), int(self.rng.uniform(50, 300) * k), int(60 * k), int(60 * k)))
        return out


def iou(a: Rect, b: Rect) -> float:
    ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    return inter / (a.w * a.h + b.w * b.h - inter)


@pytest.mark.parametrize("cam", [Rect(0, 400, 427, 320), Rect(60, 40, 384, 288), Rect(840, 380, 400, 300)],
                         ids=["corner", "floating", "right"])
@pytest.mark.parametrize("border", [True, False], ids=["border", "borderless"])
def test_facecam_detected_and_rect_found(tmp_path, border, cam):
    src = make_stream(tmp_path / "s.mp4", border, cam)
    an = analyze_layout(src, FakeFaceDetector())
    assert an.kind == "facecam"
    assert an.recommended_layout() == "facecam_top"
    assert iou(an.facecam, CAM) > 0.8, an.facecam


def test_no_face_falls_back_to_profile_layout():
    an = LayoutAnalysis("gameplay", 1280, 720)
    assert an.recommended_layout("crop") == "crop"
    assert an.recommended_layout("blur") == "blur"


def test_all_vertical_profiles_use_auto_layout():
    for key in ("tiktok_gaming", "youtube_shorts", "streamer_reactions", "cinematic"):
        assert builtin(key)["layout"] == "auto"


def test_facecam_top_render(tmp_path):
    from dogsapka_clipper.processing.render import RenderSpec, render
    src = make_stream(tmp_path / "s.mp4", True, Rect(0, 400, 427, 320))
    an = analyze_layout(src, FakeFaceDetector())
    out = tmp_path / "o.mp4"
    render(RenderSpec(src=src, out=out, width=360, height=640, crf=32, layout="facecam_top", analysis=an))
    i = probe(out)
    assert (i.width, i.height) == (360, 640)


def test_yunet_face_model_is_bundled_and_used():
    from dogsapka_clipper.processing.layout import YUNET_MODEL, FaceDetector
    assert YUNET_MODEL.exists()
    assert FaceDetector().kind in ("yunet", "mediapipe")   # never the frontal-only Haar fallback here


@pytest.mark.parametrize("face_cx,chair_x", [(60, 12), (68, 25), (75, 25)])
def test_cam_flush_with_edge_ignores_static_line_inside_cam(face_cx, chair_x):
    """Regression (real TenZ stream): a static vertical edge inside the cam (gaming chair) with the
    moving streamer next to it was taken as the cam's left border, cutting off part of the cam. The
    strip between that line and the frame edge is static cam background, not moving game, so the cam
    must reach the frame edge - also when the border search range stops just short of the edge."""
    import numpy as np

    from dogsapka_clipper.processing.layout import refine_facecam
    rng = np.random.default_rng(0)
    h, w = 270, 480
    x, y, cw, ch = 0, 85, 94, 62               # facecam, flush with the left edge
    grays = []
    for _ in range(20):
        # 'game': large moving blocks -> high temporal activity, few continuous edges
        g = np.kron(rng.uniform(30, 230, (h // 15 + 1, w // 15 + 1)), np.ones((15, 15)))[:h, :w]
        g[y:y + ch, x:x + cw] = 70.0                                   # static room background
        g[y:y + ch, chair_x:chair_x + 3] = 200.0                       # static chair edge
        g[y + 10:y + ch, chair_x + 3:86] = rng.uniform(60, 220, (ch - 10, 83 - chair_x))  # moving streamer
        grays.append(g)
    box = refine_facecam(grays, (face_cx - 8.5, 104.0, 17.0, 17.0))
    assert box is not None
    L, T, bw, bh = box
    assert L == 0, box
    assert abs((L + bw) - 94) <= 3 and abs(T - 85) <= 3 and abs((T + bh) - 147) <= 3, box
