"""Smart layout analysis: facecam detection, layout selection and crop tracking.

Uses OpenCV's YuNet face detector (model bundled in ``dogsapka_clipper/assets``), with OpenCV's
Haar cascades as fallback. If MediaPipe is installed it is
preferred for face detection. Everything degrades gracefully: without OpenCV the layout falls
back to a centred crop.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from dogsapka_clipper.processing.ffmpeg_utils import probe, run, tool_cmd

log = logging.getLogger(__name__)


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def clamp(self, W: int, H: int) -> "Rect":
        w, h = min(self.w, W), min(self.h, H)
        x = max(0, min(self.x, W - w))
        y = max(0, min(self.y, H - h))
        return Rect(x, y, w // 2 * 2, h // 2 * 2)


@dataclass
class LayoutAnalysis:
    kind: str                              # gameplay | facecam | camera | desktop
    width: int
    height: int
    facecam: Rect | None = None
    face_center: tuple[float, float] | None = None
    crop_track: list[tuple[float, float]] = field(default_factory=list)   # (t, center_x 0..1)
    motion: float = 0.0
    faces_found: int = 0

    def recommended_layout(self, fallback: str = "crop") -> str:
        """facecam -> facecam on top + gameplay below; otherwise the profile's fallback layout."""
        if self.kind == "facecam" and self.facecam:
            return "facecam_top"
        if self.kind == "camera":
            return "camera"
        if self.kind == "desktop":
            return "blur"
        return fallback

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutAnalysis":
        fc = d.get("facecam")
        return cls(kind=d.get("kind", "gameplay"), width=d.get("width", 0), height=d.get("height", 0),
                   facecam=Rect(**fc) if fc else None,
                   face_center=tuple(d["face_center"]) if d.get("face_center") else None,
                   crop_track=[tuple(x) for x in d.get("crop_track", [])],
                   motion=d.get("motion", 0.0), faces_found=d.get("faces_found", 0))


YUNET_MODEL = Path(__file__).resolve().parent.parent / "assets" / "face_detection_yunet_2023mar.onnx"


class FaceDetector:
    """Face detector: MediaPipe if installed, else OpenCV YuNet (DNN), else OpenCV Haar cascades.

    YuNet is the important default: streamers usually look at their monitor (turned head, looking
    down, glasses), which the frontal Haar cascade misses completely - the facecam was then not
    found and the clip was cropped to gameplay only.
    """

    def __init__(self) -> None:
        self.kind = "none"
        self._mp = None
        self._yunet = None
        self._yunet_size: tuple[int, int] | None = None
        self._cv = None
        self._alt = None
        try:
            import mediapipe as mp  # type: ignore
            self._mp = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
            self.kind = "mediapipe"
            return
        except Exception:
            pass
        try:
            import cv2  # type: ignore
            if YUNET_MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
                self._yunet = cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (320, 320), 0.6, 0.3, 5000)
                self._cv = (cv2, None)
                self.kind = "yunet"
                return
        except Exception:
            log.warning("YuNet face detector unavailable - falling back to Haar cascades", exc_info=True)
            self._yunet = None
        try:
            import cv2  # type: ignore
            path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            casc = cv2.CascadeClassifier(str(path))
            if not casc.empty():
                self._cv = (cv2, casc)
                alt = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml"))
                self._alt = None if alt.empty() else alt
                self.kind = "opencv"
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self.kind != "none"

    def detect(self, rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
        h, w = rgb.shape[:2]
        if self._mp is not None:
            res = self._mp.process(rgb)
            out = []
            for d in res.detections or []:
                bb = d.location_data.relative_bounding_box
                out.append((int(bb.xmin * w), int(bb.ymin * h), int(bb.width * w), int(bb.height * h)))
            return out
        if self._yunet is not None:
            cv2 = self._cv[0]
            if self._yunet_size != (w, h):
                self._yunet.setInputSize((w, h))
                self._yunet_size = (w, h)
            _, det = self._yunet.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            # ignore tiny faces (kill-feed / scoreboard portraits); a usable facecam face is larger
            min_side = max(12, w // 64)
            return [(int(d[0]), int(d[1]), int(d[2]), int(d[3])) for d in (det if det is not None else [])
                    if min(d[2], d[3]) >= min_side]
        if self._cv is not None:
            cv2, casc = self._cv
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = list(casc.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(max(18, w // 50),) * 2))
            if not faces and self._alt is not None:   # second, differently trained cascade
                faces = list(self._alt.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4,
                                                        minSize=(max(18, w // 50),) * 2))
            return [tuple(int(v) for v in f) for f in faces]
        return []


def sample_frames(path: Path, n: int = 12, width: int = 640) -> tuple[list[tuple[float, np.ndarray]], int, int]:
    info = probe(path)
    if not info.has_video:
        return [], 0, 0
    W, H = info.width, info.height
    h = int(round(width * H / max(W, 1) / 2) * 2)
    dur = max(info.duration, 0.1)
    fps = n / dur
    res = run(tool_cmd("ffmpeg") + ["-hide_banner", "-loglevel", "error", "-i", str(path), "-an",
                                    "-vf", f"fps={fps:.5f},scale={width}:{h},format=rgb24", "-f", "rawvideo", "-"],
              timeout=300, check=False)
    buf = np.frombuffer(res.stdout, dtype=np.uint8)
    fsz = width * h * 3
    frames = [((i + 0.5) / fps, buf[i * fsz:(i + 1) * fsz].reshape(h, width, 3)) for i in range(buf.size // fsz)]
    return frames, W, H


def _cluster_faces(faces_per_frame: list[list[tuple]], radius: float) -> list[list[tuple]]:
    """Group face boxes by position across frames; returns clusters, largest first.

    A facecam produces many detections at one fixed spot, while false positives (game
    characters, posters) are scattered - so the biggest cluster is the streamer's face.
    """
    clusters: list[list[tuple]] = []
    centers: list[tuple[float, float]] = []
    for fl in faces_per_frame:
        for f in fl:
            c = (f[0] + f[2] / 2, f[1] + f[3] / 2)
            for i, cc in enumerate(centers):
                if abs(c[0] - cc[0]) < radius and abs(c[1] - cc[1]) < radius:
                    clusters[i].append(f)
                    n = len(clusters[i])
                    centers[i] = (cc[0] + (c[0] - cc[0]) / n, cc[1] + (c[1] - cc[1]) / n)
                    break
            else:
                clusters.append([f])
                centers.append(c)
    return sorted(clusters, key=len, reverse=True)


def _line_candidates(profile: np.ndarray, lo: int, hi: int, keep: int = 4, min_cont: float = 0.5) -> list[int]:
    """Indices in [lo, hi] that are local maxima of a continuity profile, strongest first."""
    lo, hi = max(0, lo), min(len(profile) - 1, hi)
    if hi < lo:
        return []
    seg = profile[lo:hi + 1]
    order = sorted((i for i in range(len(seg)) if seg[i] >= min_cont), key=lambda i: -seg[i])
    picked: list[int] = []
    for i in order:                       # non-maximum suppression: one candidate per line (lines are 2-4 px)
        if all(abs(i - j) > 4 for j in picked):
            picked.append(i)
        if len(picked) >= keep:
            break
    return [lo + i for i in picked]


EDGE_SCORE = 0.7    # a side flush with the frame edge: acceptable, but any real border line beats it


def find_cam_rectangle(grays: list[np.ndarray], face: tuple[float, float, float, float]
                       ) -> tuple[int, int, int, int] | None:
    """Find the facecam as the rectangle of *static straight lines* around the face (x, y, w, h).

    Facecams are overlays with a rectangular frame. Taking the median gradient over many frames removes the
    moving game; the cam's frame stays as long continuous lines. All combinations of candidate top/bottom/
    left/right lines are scored by how continuous each side is over its full length; a side may also sit on
    the frame edge (fixed lower score). Returns None when no convincing rectangle exists.
    """
    if len(grays) < 3:
        return None
    stack = np.stack(grays).astype(np.float32)
    h, w = stack.shape[1:]
    gx = np.median(np.abs(np.diff(stack, axis=2)), axis=0)        # (h, w-1): vertical lines
    gy = np.median(np.abs(np.diff(stack, axis=1)), axis=0)        # (h-1, w): horizontal lines
    gx = np.maximum(gx, np.maximum(np.roll(gx, 1, 1), np.roll(gx, -1, 1)))   # tolerate 1 px wobble
    gy = np.maximum(gy, np.maximum(np.roll(gy, 1, 0), np.roll(gy, -1, 0)))
    thr_x = max(8.0, 4.0 * float(np.median(gx)))
    thr_y = max(8.0, 4.0 * float(np.median(gy)))
    sx, sy = gx > thr_x, gy > thr_y
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    # candidate lines, measured over a span that is surely inside the cam (around the face)
    x0, x1 = int(max(0, cx - fw)), int(min(w - 1, cx + fw))
    y0, y1 = int(max(0, cy - fh)), int(min(h - 1, cy + fh))
    row = sy[:, x0:x1].mean(axis=1)
    col = sx[y0:y1, :].mean(axis=0)
    # borders lie outside the face box; streamers often sit low in the cam, so start right at the face edge
    tops = _line_candidates(row, int(cy - 6 * fh), int(cy - 0.5 * fh)) + [-1]            # -1 = frame top
    bottoms = _line_candidates(row, int(cy + 0.45 * fh), int(cy + 7 * fh)) + [h - 1]     # h-1 = frame bottom
    lefts = _line_candidates(col, int(cx - 7 * fw), int(cx - 0.5 * fw)) + [-1]
    rights = _line_candidates(col, int(cx + 0.5 * fw), int(cx + 7 * fw)) + [w - 1]

    def side_h(i: int, L: int, R: int) -> float:       # horizontal border after row i, spanning columns L..R
        return EDGE_SCORE if i in (-1, h - 1) else float(sy[i, L:R + 1].mean())

    def side_v(j: int, T: int, B: int) -> float:
        return EDGE_SCORE if j in (-1, w - 1) else float(sx[T:B + 1, j].mean())

    best, best_key = None, (-1.0, 0)
    for t in tops:
        for b in bottoms:
            T, B = t + 1, b                     # rectangle rows T..B
            if B - T < 2.0 * fh or B - T > 0.9 * h:
                continue
            for le in lefts:
                for r in rights:
                    L, R = le + 1, r
                    bw, bh = R - L + 1, B - T + 1
                    if bw < 3.0 * fw or bw > 0.85 * w or not 0.6 <= bw / bh <= 2.6:
                        continue
                    sides = (side_h(t, L, R), side_h(b, L, R), side_v(le, T, B), side_v(r, T, B))
                    if min(sides) < 0.55:
                        continue
                    key = (round(sum(sides) / 4, 2), bw * bh)
                    if key > best_key:
                        best, best_key = (L, T, bw, bh), key
    if best is None or best_key[0] < 0.72:
        return None
    # a side within ~1 % of the frame edge is flush with it (thin static lines near the edge are not borders)
    L, T, bw, bh = best
    R, B = L + bw, T + bh
    tol_x, tol_y = max(3, int(w * 0.012)), max(3, int(h * 0.012))
    L = 0 if L <= tol_x else L
    T = 0 if T <= tol_y else T
    R = w if w - R <= tol_x else R
    B = h if h - B <= tol_y else B
    return L, T, R - L, B - T


def _pick_border(cont: np.ndarray, strength: np.ndarray, act: np.ndarray, lo: int, hi: int, toward_low: bool,
                 edge: int, strip: int, min_cont: float = 0.75, ref_act: float = 0.0,
                 max_gap: int = 0) -> int | None:
    """Choose the facecam border among continuous lines in [lo, hi].

    Score = line strength (time-averaged gradient along the line) boosted by the activity contrast
    between both sides (static cam background vs moving game, or vice versa). If no continuous line
    exists but the search range reaches the frame edge, the cam is flush with that edge.
    """
    n = len(cont)
    lo_c, hi_c = max(0, lo), min(n - 1, hi)
    reaches_edge = (toward_low and lo <= 0) or (not toward_low and hi >= n - 1)
    best, best_score, best_contrast = None, 0.0, 0.0
    for i in range(lo_c, hi_c + 1):
        if cont[i] < min_cont:
            continue
        a = act[max(0, i - strip + 1):i + 1].mean()
        b = act[i + 1:i + 1 + strip].mean() if i + 1 < len(act) else a
        contrast = abs(a - b) / (a + b + 1.0)
        score = strength[i] * (1.0 + 2.0 * contrast)
        if score > best_score:
            best, best_score, best_contrast = i, score, contrast
    # When the cam may simply end at the frame edge, a line only wins if the regions on its two
    # sides really behave differently - otherwise it is texture inside the cam (or the game).
    if reaches_edge and (best is None or best_contrast < 0.15):
        return edge
    # A line inside the cam (chair, wall) with a nearly static strip between it and the frame edge:
    # that strip is cam background, not game (game content moves) -> the cam is flush with the edge.
    # Also applies when the search range stops just short of the edge (gap of at most `max_gap`).
    near_edge = best is not None and (best + 1 <= max_gap if toward_low else n - 1 - best <= max_gap)
    if (reaches_edge or near_edge) and best is not None and ref_act > 0:
        outer = act[:best + 1] if toward_low else act[best + 1:]
        if outer.size and float(outer.mean()) < 0.25 * ref_act:
            return edge
    return best


def refine_facecam(grays: list[np.ndarray], face: tuple[float, float, float, float]) -> tuple[int, int, int, int] | None:
    """Find the real facecam rectangle around a face (x, y, w, h in sample pixels).

    Overlay borders are static while the content on both sides changes, so after averaging the
    gradient over many frames they remain as long, *continuous* lines; texture edges are broken
    up. Among those lines, the border is the one where the motion behaviour changes.
    """
    if len(grays) < 3:
        return None
    h, w = grays[0].shape
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    stack = np.stack(grays)
    gx = np.abs(np.diff(stack, axis=2)).mean(axis=0)    # vertical lines, (h, w-1)
    gy = np.abs(np.diff(stack, axis=1)).mean(axis=0)    # horizontal lines, (h-1, w)
    act = stack.std(axis=0)                             # temporal activity per pixel
    thr_x = max(3.0 * float(np.median(gx)), 4.0)
    thr_y = max(3.0 * float(np.median(gy)), 4.0)
    ys = slice(int(max(0, cy - 1.3 * fh)), int(min(h - 1, cy + 1.3 * fh)))
    xs = slice(int(max(0, cx - 1.3 * fw)), int(min(w - 1, cx + 1.3 * fw)))
    col = (gx[ys] > thr_x).mean(axis=0)
    row = (gy[:, xs] > thr_y).mean(axis=1)
    act_x = act[ys].mean(axis=0)
    act_y = act[:, xs].mean(axis=1)
    str_x = gx[ys].mean(axis=0) / thr_x
    str_y = gy[:, xs].mean(axis=1) / thr_y
    sx, sy = max(3, int(0.3 * fw)), max(3, int(0.3 * fh))
    ref = float(np.median(act))   # typical activity of the frame (mostly game)
    gx_max, gy_max = int(2 * fw), int(2 * fh)
    left = _pick_border(col, str_x, act_x, int(cx - 4.0 * fw), int(cx - 0.9 * fw), True, -1, sx,
                        ref_act=ref, max_gap=gx_max)
    right = _pick_border(col, str_x, act_x, int(cx + 0.9 * fw), int(cx + 4.0 * fw), False, w - 1, sx,
                         ref_act=ref, max_gap=gx_max)
    top = _pick_border(row, str_y, act_y, int(cy - 3.0 * fh), int(cy - 0.9 * fh), True, -1, sy,
                       ref_act=ref, max_gap=gy_max)
    bottom = _pick_border(row, str_y, act_y, int(cy + 0.9 * fh), int(cy + 3.5 * fh), False, h - 1, sy,
                          ref_act=ref, max_gap=gy_max)
    if None in (left, right, top, bottom):
        return None
    L, R, T, B = left + 1, right + 1, top + 1, bottom + 1
    bw, bh = R - L, B - T
    if bw < 1.5 * fw or bh < 1.3 * fh or bw > w * 0.6 or bh > h * 0.7:
        return None
    return int(L), int(T), int(bw), int(bh)


def analyze_layout(path: Path, detector: FaceDetector | None = None, n_frames: int = 20) -> LayoutAnalysis:
    frames, W, H = sample_frames(path, n_frames, width=960)
    if not frames:
        return LayoutAnalysis("gameplay", W, H)
    sh, sw = frames[0][1].shape[:2]
    scale = W / sw
    detector = detector or FaceDetector()
    faces_per_frame = [detector.detect(f) if detector.available else [] for _, f in frames]
    all_faces = [f for fl in faces_per_frame for f in fl]
    grays = [f.mean(axis=2) for _, f in frames]
    # motion map for gameplay focus
    track: list[tuple[float, float]] = []
    total_motion = 0.0
    for i in range(1, len(grays)):
        d = np.abs(grays[i] - grays[i - 1])
        total_motion += float(d.mean())
        colm = d.sum(axis=0)
        if colm.sum() > 0:
            cx = float((colm * np.arange(sw)).sum() / colm.sum()) / sw
            track.append((frames[i][0], cx))
    motion = total_motion / max(1, len(grays) - 1) / 255.0
    analysis = LayoutAnalysis("gameplay", W, H, motion=motion, faces_found=len(all_faces))
    analysis.crop_track = _smooth_track(track)

    clusters = _cluster_faces(faces_per_frame, radius=sw * 0.06)
    # the facecam face is the one that stays put: among equally frequent faces prefer the most static one
    def _spread(c):
        xs = [f[0] + f[2] / 2 for f in c]
        ys = [f[1] + f[3] / 2 for f in c]
        return float(np.std(xs) + np.std(ys))
    clusters.sort(key=lambda c: (-len(c), _spread(c)))
    if clusters:
        best = clusters[0]
        frac_frames = len(best) / len(frames)
        fw = float(np.median([f[2] for f in best]))
        fh = float(np.median([f[3] for f in best]))
        cx = float(np.median([f[0] + f[2] / 2 for f in best]))
        cy = float(np.median([f[1] + f[3] / 2 for f in best]))
        if fw / sw > 0.15 and frac_frames >= 0.4:
            analysis.kind = "camera"           # the stream is mostly the streamer's camera
            analysis.face_center = (cx / sw, cy / sh)
            return analysis
        if frac_frames >= 0.3:
            face_box = (cx - fw / 2, cy - fh / 2, fw, fh)
            box = find_cam_rectangle(grays, face_box) or refine_facecam(grays, face_box)
            if box is not None and (box[2] < fw * 3.0 or box[3] < fh * 2.0):
                box = None  # an internal face/monitor edge is not the whole camera panel
            if box is not None:
                r = Rect(int(box[0] * scale), int(box[1] * scale), int(box[2] * scale), int(box[3] * scale))
            else:
                # no visible border: frame the head generously (16:9-ish) and snap to nearby edges
                box_w, box_h = fw * 3.4, fw * 3.4 * 0.75
                r = Rect(int((cx - box_w / 2) * scale), int((cy - box_h * 0.45) * scale),
                         int(box_w * scale), int(box_h * scale)).clamp(W, H)
                r = _snap_to_corner(r, W, H)
            analysis.kind = "facecam"
            analysis.facecam = r.clamp(W, H)
            analysis.face_center = (cx / sw, cy / sh)
            return analysis
    if motion < 0.004:
        analysis.kind = "desktop"
    return analysis


def _snap_to_corner(r: Rect, W: int, H: int) -> Rect:
    """Facecams usually sit flush against an edge; snap if within 4% of it."""
    tol_x, tol_y = W * 0.04, H * 0.04
    x, y = r.x, r.y
    if x < tol_x:
        x = 0
    if W - (r.x + r.w) < tol_x:
        x = W - r.w
    if y < tol_y:
        y = 0
    if H - (r.y + r.h) < tol_y:
        y = H - r.h
    return Rect(x, y, r.w, r.h).clamp(W, H)


def _smooth_track(track: list[tuple[float, float]], max_points: int = 8) -> list[tuple[float, float]]:
    """Heavily smoothed horizontal focus track -> no distracting camera movement."""
    if not track:
        return []
    ts = np.array([t for t, _ in track])
    xs = np.array([x for _, x in track])
    k = max(1, len(xs) // 3)
    sm = np.convolve(np.pad(xs, (k, k), mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    idx = np.linspace(0, len(ts) - 1, min(max_points, len(ts))).round().astype(int)
    out = [(float(ts[i]), float(np.clip(sm[i], 0.0, 1.0))) for i in idx]
    # collapse tiny movements into a static crop
    if max(x for _, x in out) - min(x for _, x in out) < 0.08:
        mean = float(np.mean([x for _, x in out]))
        return [(out[0][0], mean)]
    return out
