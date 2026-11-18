"""Speech transcription (faster-whisper, local) and animated subtitle generation.

Transcript format (stored in the DB)::

    {"language": "en", "segments": [{"start":0.0,"end":2.1,"text":"...",
       "confidence":0.83, "words":[{"start":..,"end":..,"word":"..","prob":..}]}]}
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LOW_CONFIDENCE = 0.55


class TranscriptionUnavailable(RuntimeError):
    pass


def _is_cuda_runtime_error(e: BaseException) -> bool:
    """CUDA present but its runtime libraries (cuBLAS/cuDNN) missing or unusable."""
    msg = str(e).lower()
    return any(k in msg for k in ("cublas", "cudnn", "cuda", "cudart"))


class Transcriber:
    """Lazy wrapper around faster-whisper. The model loads only on first use."""

    _lock = threading.Lock()
    _cache: dict[tuple, object] = {}
    # Set after a CUDA runtime failure (e.g. cuBLAS missing). A CUDA model that failed once can hang
    # on the next call instead of raising, so it must never be reused in this process.
    _cuda_broken: str | None = None

    def __init__(self, model: str = "small", device: str = "auto", models_dir: Path | None = None) -> None:
        self.model_name = model
        self.device = device
        self.models_dir = models_dir

    @staticmethod
    def available() -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _model(self, device: str | None = None):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise TranscriptionUnavailable(
                "faster-whisper is not installed. Install it with 'pip install faster-whisper' "
                "or use the full installer, which includes it.") from e
        device = device or self.device
        if device == "auto":
            try:
                import ctranslate2
                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 and not Transcriber._cuda_broken else "cpu"
            except Exception:
                device = "cpu"
        if device == "cuda" and Transcriber._cuda_broken:
            raise TranscriptionUnavailable(self._cuda_message(Transcriber._cuda_broken))
        key = (self.model_name, device)
        with self._lock:
            if key not in self._cache:
                compute = "float16" if device == "cuda" else "int8"
                log.info("Loading Whisper model '%s' on %s (%s)", self.model_name, device, compute)
                try:
                    self._cache[key] = WhisperModel(self.model_name, device=device, compute_type=compute,
                                                    download_root=str(self.models_dir) if self.models_dir else None)
                except Exception as e:
                    raise TranscriptionUnavailable(
                        f"Could not load Whisper model '{self.model_name}': {e}. The model is downloaded on first "
                        f"use - check the internet connection, or copy the model into the 'models' folder.") from e
            return self._cache[key]

    @classmethod
    def unload_all(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    def transcribe(self, media: Path, language: str = "auto", cancel: threading.Event | None = None,
                   progress=None) -> dict:
        try:
            return self._transcribe(self._model(), media, language, cancel, progress)
        except (RuntimeError, OSError) as e:
            if isinstance(e, TranscriptionUnavailable) or not _is_cuda_runtime_error(e):
                raise
            with self._lock:
                Transcriber._cuda_broken = str(e)
                for key in [k for k in self._cache if k[1] == "cuda"]:
                    del self._cache[key]
            if self.device != "auto":
                raise TranscriptionUnavailable(self._cuda_message(str(e))) from e
            log.warning("CUDA transcription unavailable (%s) - using CPU from now on", e)
            return self._transcribe(self._model("cpu"), media, language, cancel, progress)

    @staticmethod
    def _cuda_message(err: str) -> str:
        return (f"GPU transcription failed ({err}). Install the NVIDIA CUDA 12 cuBLAS/cuDNN libraries "
                f"or set the Whisper device to 'cpu' / 'auto' in Settings.")

    def _transcribe(self, model, media: Path, language: str, cancel: threading.Event | None, progress) -> dict:
        segments, info = model.transcribe(str(media), language=None if language == "auto" else language,
                                          word_timestamps=True, vad_filter=True, beam_size=5,
                                          condition_on_previous_text=False)
        out = []
        total = max(getattr(info, "duration", 0.0) or 0.0, 0.1)
        for seg in segments:
            if cancel and cancel.is_set():
                raise RuntimeError("Cancelled")
            words = [{"start": float(w.start), "end": float(w.end), "word": w.word.strip(), "prob": float(w.probability)}
                     for w in (seg.words or []) if w.word.strip()]
            conf = sum(w["prob"] for w in words) / len(words) if words else 0.0
            out.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(),
                        "confidence": round(conf, 3), "words": words})
            if progress:
                progress(min(1.0, seg.end / total))
        return {"language": info.language, "language_probability": float(info.language_probability),
                "segments": out}


# ---------------------------------------------------------------------------
# caption layout


@dataclass
class Caption:
    start: float
    end: float
    words: list[dict]

    @property
    def text(self) -> str:
        return " ".join(w["word"] for w in self.words)

    @property
    def low_confidence(self) -> bool:
        probs = [w.get("prob", 1.0) for w in self.words]
        return bool(probs) and sum(probs) / len(probs) < LOW_CONFIDENCE


def transcript_words(transcript: dict) -> list[dict]:
    words: list[dict] = []
    for seg in transcript.get("segments", []):
        if seg.get("words"):
            words += [dict(w) for w in seg["words"]]
        elif seg.get("text"):
            # no word timing (e.g. manually edited): distribute evenly
            toks = seg["text"].split()
            dur = (seg["end"] - seg["start"]) / max(1, len(toks))
            words += [{"start": seg["start"] + i * dur, "end": seg["start"] + (i + 1) * dur, "word": t, "prob": 1.0}
                      for i, t in enumerate(toks)]
    return words


def build_captions(words: list[dict], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.6,
                   max_dur: float = 2.5) -> list[Caption]:
    caps: list[Caption] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            chars = len(" ".join(x["word"] for x in cur + [w]))
            if (len(cur) >= max_words or chars > max_chars or w["start"] - cur[-1]["end"] > max_gap
                    or w["end"] - cur[0]["start"] > max_dur or re.search(r"[.!?]$", cur[-1]["word"])):
                caps.append(Caption(cur[0]["start"], cur[-1]["end"], cur))
                cur = []
        cur.append(w)
    if cur:
        caps.append(Caption(cur[0]["start"], cur[-1]["end"], cur))
    # avoid flicker: extend each caption to the next one if the gap is small
    for a, b in zip(caps, caps[1:]):
        if 0 < b.start - a.end < 0.3:
            a.end = b.start
    return caps


def remap_time(t: float, keep: list[tuple[float, float]] | None, offset: float = 0.0) -> float | None:
    """Map a source time to output time given kept ranges (silence trimming) and trim offset."""
    t -= offset
    if not keep:
        return t
    acc = 0.0
    for a, b in keep:
        if t < a:
            return None
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def shift_words(words: list[dict], offset: float, duration: float,
                keep: list[tuple[float, float]] | None = None) -> list[dict]:
    out = []
    for w in words:
        s = remap_time(w["start"], keep, offset)
        e = remap_time(w["end"], keep, offset)
        if s is None or e is None or e <= 0 or s >= duration:
            continue
        out.append({**w, "start": max(0.0, s), "end": min(duration, max(e, s + 0.05))})
    return out


# ---------------------------------------------------------------------------
# SRT / ASS writers


def _srt_ts(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(captions: list[Caption], path: Path) -> Path:
    lines = []
    for i, c in enumerate(captions, 1):
        lines += [str(i), f"{_srt_ts(c.start)} --> {_srt_ts(c.end)}", c.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


STYLE_PRESETS = {
    # name: (font scale, bold, outline, shadow, uppercase, words/caption, highlight mode)
    "viral": dict(size=0.062, bold=True, outline=0.0075, shadow=0, upper=True, words=3, mode="color"),
    "clean": dict(size=0.042, bold=False, outline=0.0, shadow=0.002, upper=False, words=7, mode="fade"),
    "dynamic": dict(size=0.066, bold=True, outline=0.006, shadow=0.002, upper=True, words=2, mode="pop"),
    "minimal": dict(size=0.032, bold=False, outline=0.002, shadow=0, upper=False, words=8, mode="none"),
    "boxed": dict(size=0.040, bold=True, outline=0.005, shadow=0, upper=False, words=5, mode="none"),
    "neon": dict(size=0.052, bold=True, outline=0.003, shadow=0.003, upper=True, words=3, mode="color"),
}

POSITIONS = {"lower": (2, 0.18), "middle": (5, 0.0), "upper": (8, 0.12)}   # alignment, marginV share


def write_ass(captions_words: list[dict], path: Path, width: int, height: int, style: str = "viral",
              accent: str = "#FFE600", position: str = "lower", font: str = "Arial Black",
              font_size: int = 0) -> Path:
    """Write an animated ASS file.

    The lower position leaves an 18% margin for platform controls.
    """
    p = STYLE_PRESETS.get(style, STYLE_PRESETS["viral"])
    ref = min(width, height * 9 / 16) if height > width else height
    base = height if height > width else ref * 16 / 9
    size = font_size or max(16, int(base * p["size"]))
    outline = max(0, round(base * p["outline"], 1))
    shadow = round(base * p["shadow"], 1)
    align, mv = POSITIONS.get(position, POSITIONS["lower"])
    margin_v = int(height * mv)
    margin_h = int(width * 0.08)
    font_name = font if style in ("viral", "dynamic") else "Arial"
    border_style = 3 if style == "boxed" else 1
    outline_color = _ass_color(accent) if style == "neon" else "&H00000000"
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font_name},{size},&H00FFFFFF,{_ass_color(accent)},{outline_color},&H80000000,{-1 if p['bold'] else 0},0,0,0,100,100,0,0,{border_style},{outline},{shadow},{align},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    caps = build_captions(captions_words, max_words=p["words"], max_chars=18 if p["words"] <= 3 else 42)
    acc = _ass_color(accent)
    events = []
    for c in caps:
        toks = [(_esc(w["word"].upper() if p["upper"] else w["word"])) for w in c.words]
        mode = p["mode"]
        if mode in ("color", "pop"):
            # one event per word so the active word is highlighted
            for i, w in enumerate(c.words):
                s = w["start"] if i else c.start
                e = c.words[i + 1]["start"] if i + 1 < len(c.words) else c.end
                if e <= s:
                    continue
                parts = []
                for j, t in enumerate(toks):
                    if j == i:
                        if mode == "pop":
                            parts.append(r"{\c" + acc + r"\fscx85\fscy85\t(0,90,\fscx118\fscy118)\t(90,160,\fscx108\fscy108)}"
                                         + t + r"{\r}")
                        else:
                            parts.append(r"{\c" + acc + "}" + t + r"{\r}")
                    else:
                        parts.append(t)
                intro = r"{\fad(60,0)}" if i == 0 else ""
                events.append(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(e)},Main,,0,0,0,,{intro}{' '.join(parts)}")
        elif mode == "fade":
            events.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Main,,0,0,0,,"
                          r"{\fad(150,100)\move(" + f"{width // 2},{height - margin_v + 12},{width // 2},{height - margin_v}"
                          + r",0,150)}" + " ".join(toks) if align == 2 else
                          f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Main,,0,0,0,,{{\\fad(150,100)}}{' '.join(toks)}")
        else:
            events.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Main,,0,0,0,,{' '.join(toks)}")
    path.write_text(hdr + "\n".join(events) + "\n", encoding="utf-8")
    return path


def transcript_text(transcript: dict) -> str:
    return " ".join(s.get("text", "") for s in transcript.get("segments", [])).strip()
