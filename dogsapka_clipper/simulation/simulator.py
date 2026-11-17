"""Simulation mode: develop and test without a real Twitch livestream.

* ``generate_sample_stream`` creates a synthetic "gameplay" recording with known highlight
  moments (voice-like excited audio + visual flash) using only FFmpeg.
* ``SimulatedChat`` produces realistic chat traffic with spikes and reactions shortly after
  each highlight (chat lags the stream by a few seconds, like real Twitch chat).
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from dogsapka_clipper.processing.ffmpeg_utils import run, tool_cmd
from dogsapka_clipper.twitch.chat import ChatMessage, anonymize

NORMAL_MSGS = ["hello chat", "what rank is this", "nice", "gg", "how long have you been live?", "music?",
               "first time here", "hi from Hungary", "szia", "mi a helyzet", "jó a stream", "this game looks fun",
               "what settings do you use", "lets go", "hmm", "o7", "Kappa", "true", "love this map"]
HYPE_MSGS = ["LOL", "LMAO", "NO WAY", "OMG", "CLIP IT", "KEKW", "KEKW KEKW", "WTF", "INSANE", "POGGERS",
             "HAHAHAHA", "NE MÁR", "MI VOLT EZ", "ÚRISTEN", "AZTA", "CLIP", "NOOOOO", "LUL", "OMEGALUL", "!!!!"]


@dataclass
class SampleStream:
    path: Path
    duration: float
    highlights: list[float]      # seconds from start


def generate_sample_stream(out: Path, duration: float = 150, highlights: list[float] | None = None,
                           width: int = 1280, height: int = 720, fps: int = 30) -> SampleStream:
    highlights = highlights if highlights is not None else [45.0, 105.0]
    out.parent.mkdir(parents=True, exist_ok=True)
    # audio: quiet noisy background "game" + speech-like (harmonic, 5 Hz syllable-modulated) bursts
    bursts = "+".join(
        f"between(t,{h:.2f},{h + 3.5:.2f})*0.55*(0.55+0.45*sin(2*PI*5*t))*"
        f"(sin(2*PI*210*t)+0.6*sin(2*PI*420*t)+0.4*sin(2*PI*630*t)+0.25*sin(2*PI*1260*t))/2.25"
        for h in highlights) or "0"
    aexpr = f"0.035*(random(0)*2-1)+0.02*sin(2*PI*110*t)+{bursts}"
    flash = "+".join(f"between(t,{h:.2f},{h + 0.6:.2f})" for h in highlights) or "0"
    vf = (f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='{flash}',"
          f"drawtext=text='DOGSAPKA SIMULATION %{{pts\\:hms}}':x=20:y=20:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5")
    args = tool_cmd("ffmpeg") + ["-y", "-hide_banner", "-loglevel", "error",
                                 "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}:d={duration}",
                                 "-f", "lavfi", "-i", f"aevalsrc='{aexpr}':s=48000:d={duration}",
                                 "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-g", str(fps * 2),
                                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)]
    try:
        run(args, timeout=600)
    except Exception:
        # drawtext needs a font; retry without the overlay text
        args[args.index("-vf") + 1] = vf.split(",drawtext")[0]
        run(args, timeout=600)
    (out.with_suffix(".json")).write_text(json.dumps({"highlights": highlights, "duration": duration}))
    return SampleStream(out, duration, highlights)


class SimulatedChat:
    def __init__(self, highlights: list[float], base_rate: float = 3.0, spike_rate: float = 28.0,
                 delay: float = 4.0, spike_len: float = 8.0, seed: int | None = None) -> None:
        self.highlights = highlights
        self.base_rate = base_rate
        self.spike_rate = spike_rate
        self.delay = delay
        self.spike_len = spike_len
        self.rng = random.Random(seed)
        self.users = [f"viewer{i}" for i in range(400)]
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def rate_at(self, t: float) -> tuple[float, bool]:
        for h in self.highlights:
            if h + self.delay <= t <= h + self.delay + self.spike_len:
                return self.spike_rate, True
        return self.base_rate, False

    def messages_between(self, t0: float, t1: float, wall0: float) -> list[ChatMessage]:
        """Deterministic-ish batch generation for offline tests."""
        out = []
        t = t0
        while t < t1:
            rate, hype = self.rate_at(t)
            t += self.rng.expovariate(rate)
            if t >= t1:
                break
            text = self.rng.choice(HYPE_MSGS if hype and self.rng.random() < 0.8 else NORMAL_MSGS)
            out.append(ChatMessage(wall0 + t, anonymize(self.rng.choice(self.users)), text))
        return out

    async def run(self, on_message, start_wall: float | None = None, duration: float | None = None) -> None:
        start = start_wall or time.time()
        t = time.time() - start
        while not self._stop.is_set():
            rate, hype = self.rate_at(t)
            await asyncio.sleep(min(1.0, self.rng.expovariate(rate)))
            t = time.time() - start
            if duration is not None and t > duration:
                break
            text = self.rng.choice(HYPE_MSGS if hype and self.rng.random() < 0.8 else NORMAL_MSGS)
            res = on_message(ChatMessage(time.time(), anonymize(self.rng.choice(self.users)), text))
            if asyncio.iscoroutine(res):
                await res
