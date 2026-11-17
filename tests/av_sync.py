"""Helpers to measure audio/video sync on the simulation stream (white flash + loud burst together)."""
import numpy as np

from dogsapka_clipper.processing.ffmpeg_utils import decode_audio_pcm, probe, run, tool_cmd


def flash_times(path):
    res = run(tool_cmd("ffmpeg") + ["-loglevel", "error", "-i", str(path), "-an", "-vf", "scale=32:32,format=gray",
                                    "-f", "rawvideo", "-"], timeout=300)
    fr = np.frombuffer(res.stdout, np.uint8).reshape(-1, 32 * 32).mean(axis=1)
    on = np.where((fr[1:] > 200) & (fr[:-1] <= 200))[0] + 1
    return [i / probe(path).fps for i in on]


def burst_times(path):
    pcm = decode_audio_pcm(path, 16000)
    n = 160
    rms = np.sqrt(np.mean(pcm[: len(pcm) // n * n].reshape(-1, n) ** 2, axis=1))
    on = np.where((rms[1:] > 0.15) & (rms[:-1] <= 0.15))[0] + 1
    out = []
    for i in on:
        if not out or i / 100 - out[-1] > 2:
            out.append(i / 100)
    return out


def av_offsets_ms(path):
    """Audio-minus-video offsets (ms) for each flash/burst pair."""
    f, b = flash_times(path), burst_times(path)
    return [(bb - ff) * 1000 for ff in f for bb in b if abs(bb - ff) < 1.5]
