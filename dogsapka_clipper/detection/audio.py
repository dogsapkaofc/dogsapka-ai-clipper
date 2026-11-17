"""Detection method C: audio excitement.

Uses signal processing rather than raw loudness:

* loudness rise relative to an adaptive baseline (continuous loud audio raises the baseline,
  so a loud game or music bed is not a highlight by itself),
* speech-band energy share and 3-8 Hz envelope modulation (syllable / laughter rhythm) to
  tell microphone reactions apart from music and explosions,
* sustain check so a single short broadband transient (an explosion) scores low unless the
  streamer reacts to it.

An optional Silero VAD model is used for voice probability when installed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from dogsapka_clipper.detection.base import EWStats, SignalSample, ramp

log = logging.getLogger(__name__)
SR = 16000
BLOCK = 0.5   # seconds


@dataclass
class BlockFeatures:
    ts: float
    db: float
    speech_ratio: float
    flatness: float
    modulation: float
    voice_prob: float = -1.0


def block_features(x: np.ndarray, ts: float, sr: int = SR, context: np.ndarray | None = None) -> BlockFeatures:
    if x.size == 0:
        return BlockFeatures(ts, -90.0, 0.0, 0.0, 0.0)
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)
    db = 20 * np.log10(rms)
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(x.size, 1 / sr)
    total = spec.sum()
    speech = spec[(freqs >= 85) & (freqs <= 4000)].sum() / total
    flat = float(np.exp(np.mean(np.log(spec))) / np.mean(spec))
    # envelope modulation over a ~2 s context (10 ms frames)
    ctx = context if context is not None and context.size >= sr else x
    hop = sr // 100
    n = ctx.size // hop
    mod = 0.0
    if n >= 32:
        env = np.sqrt(np.mean(ctx[: n * hop].reshape(n, hop) ** 2, axis=1))
        env = env - env.mean()
        es = np.abs(np.fft.rfft(env * np.hanning(n))) ** 2
        ef = np.fft.rfftfreq(n, 0.01)
        tot = es[(ef > 0.5) & (ef < 20)].sum() + 1e-12
        mod = float(es[(ef >= 3) & (ef <= 8)].sum() / tot)
    return BlockFeatures(ts, float(db), float(speech), flat, mod)


class _SileroVAD:
    """Optional voice-activity model (requires the ``silero-vad`` package + torch)."""

    def __init__(self) -> None:
        from silero_vad import load_silero_vad  # type: ignore
        import torch  # type: ignore
        self.torch = torch
        self.model = load_silero_vad()

    def prob(self, x: np.ndarray) -> float:
        chunk = 512
        probs = []
        for i in range(0, x.size - chunk + 1, chunk):
            t = self.torch.from_numpy(x[i:i + chunk].astype(np.float32))
            probs.append(float(self.model(t, SR).item()))
        return float(np.mean(probs)) if probs else 0.0


def load_vad() -> _SileroVAD | None:
    try:
        return _SileroVAD()
    except Exception:
        return None


class AudioExcitementDetector:
    def __init__(self, sensitivity: float = 0.5, use_vad: bool = False) -> None:
        self.sensitivity = sensitivity
        self.db_stats = EWStats(span=120)        # ~60 s of 0.5 s blocks
        self._pending: list[BlockFeatures] = []  # look-ahead buffer for sustain check
        self._history: np.ndarray = np.zeros(0, dtype=np.float32)
        self.vad = load_vad() if use_vad else None
        self.last_level = 0.0                    # 0..1 for UI meters

    def process(self, pcm: np.ndarray, start_ts: float) -> list[tuple[float, SignalSample]]:
        """Feed PCM (mono float32 @16 kHz) starting at wall time ``start_ts``.

        Returns scored samples (possibly for earlier blocks, because scoring needs 1.5 s of
        look-ahead).
        """
        bs = int(SR * BLOCK)
        out: list[tuple[float, SignalSample]] = []
        for i in range(0, pcm.size - bs + 1, bs):
            blk = pcm[i:i + bs]
            self._history = np.concatenate([self._history, blk])[-2 * SR:]
            f = block_features(blk, start_ts + i / SR, context=self._history)
            if self.vad is not None:
                try:
                    f.voice_prob = self.vad.prob(blk)
                except Exception:
                    self.vad = None
            self._pending.append(f)
            self.last_level = ramp(f.db, -50, -6)
            while len(self._pending) >= 4:
                out.append(self._score(self._pending[0], self._pending[1:4]))
                self._pending.pop(0)
        return out

    def _score(self, f: BlockFeatures, ahead: list[BlockFeatures]) -> tuple[float, SignalSample]:
        warm = self.db_stats.n >= 20
        mean = self.db_stats.mean
        std = max(self.db_stats.std, 3.0)
        z = (f.db - mean) / std if warm else 0.0
        z_ahead = [(a.db - mean) / std for a in ahead] if warm else [0.0]
        sustain = sum(1 for v in z_ahead if v > 0.8) / max(1, len(z_ahead))
        if f.voice_prob >= 0:
            voice = f.voice_prob
        else:
            # voiced speech: energy concentrated in the voice band, harmonic (not noise-flat),
            # with syllable-rate envelope modulation
            voice = (ramp(f.speech_ratio, 0.55, 0.9) * (1.0 - ramp(f.flatness, 0.2, 0.5))
                     * (0.4 + 0.6 * ramp(f.modulation, 0.12, 0.35)))
        loud = ramp(z, 1.0, 3.6 - self.sensitivity)
        value = loud * (0.3 + 0.7 * voice) * (0.35 + 0.65 * sustain)
        # tonal, unmodulated -> probably music; very flat, short -> explosion/impact
        if f.modulation < 0.08 and f.flatness < 0.05:
            value *= 0.5
        if f.flatness > 0.35 and sustain < 0.34:
            value *= 0.4
        if f.db < -45:
            value = 0.0
        # baseline update (clipped so a reaction doesn't instantly become "normal")
        self.db_stats.update(min(f.db, mean + 2 * std) if warm else f.db)
        reason = ""
        if value >= 0.5:
            kind = "Laughter / excited voice" if f.modulation > 0.3 and voice > 0.5 else \
                   "Loud reaction" if voice > 0.4 else "Sudden loud audio"
            reason = f"{kind} (+{f.db - mean:.0f} dB)"
        return f.ts, SignalSample(value if warm else 0.0, reason,
                                  {"db": f.db, "baseline_db": mean, "voice": voice, "mod": f.modulation})

    def state(self) -> dict:
        return {"db": self.db_stats.state()}

    def load_state(self, st: dict | None) -> None:
        if st:
            self.db_stats.load(st.get("db"))
            self.db_stats.n = min(self.db_stats.n, 20)
