# DOGSAPKA AI CLIPPER

AI-powered highlight detection and automatic short-form editing for **authorized** Twitch livestreams.
DOGSAPKA watches channels you have permission to record, keeps a rolling recording buffer, detects exciting
moments from several signals, saves clips with context before and after the moment, and turns them into
subtitled 9:16 videos for TikTok and YouTube Shorts.

> Only monitor, record and republish channels you own or have explicit permission from. DOGSAPKA uses only
> official interfaces (Twitch Helix + OAuth + IRC chat, YouTube Data API, TikTok Content Posting API).

## Features

| Area | What it does |
|---|---|
| Monitoring | Helix live/offline polling for many channels, exponential backoff, offline grace period, crash recovery |
| Recording | Streamlink → FFmpeg segment muxer (no re-encode), circular buffer (30–300 s), protected ranges for pending clips |
| Detection | Chat spikes (adaptive z-score per streamer), chat reactions (EN + HU keywords/emotes), audio excitement (voice-band, syllable modulation, sustain; music/explosion rejection), optional visual change detection, weighted 0–100 score, merging, cooldown, auto-threshold |
| Clips | Window always starts before the event; accurate trimming (re-encode only when boundaries need it); corrupt segment skipping |
| Editing | 5 built-in profiles + custom; layouts: facecam-top/gameplay-bottom, smart crop with smoothed focus tracking, face focus, blurred background, original aspect |
| AI | faster-whisper word-level transcription (HU/EN/auto), animated ASS subtitles (4 styles), SRT export, low-confidence flags, OpenCV face/facecam detection |
| Enhancements | Loudness normalization, silence shortening (never cuts the highlight), peak zoom, fades, duplicate detection, quality ranking |
| QC | Missing audio, silent audio, black frames, wrong duration/dimensions, decode errors |
| Export | Standard/High/Ultra/Custom presets, NVENC (verified by a real test encode) with automatic CPU fallback, fps never upsampled |
| Publishing | Manual export (always), YouTube resumable upload, TikTok inbox upload / direct post — each requires explicit confirmation |
| UI | Frameless PySide6 app: Dashboard, Streamers, Live Monitor, Clip Library, AI Editor, Processing queue, Settings, setup wizard, EN/HU |
| Simulation | Generates a local "livestream" with known highlights + simulated chat, runs the full real pipeline without Twitch |

## Running from source

```bash
python -m pip install -r requirements.txt     # Python 3.11+ (3.12+ recommended)
python -m dogsapka_clipper                         # opens the setup wizard on first run
python -m dogsapka_clipper --simulate --no-monitor # try everything without a Twitch stream
```

FFmpeg/FFprobe must be on `PATH` or in a `tools/` folder next to the app; Streamlink is needed for real
recordings only. Data lives in `%LOCALAPPDATA%\DOGSAPKA_AI_CLIPPER` (override with `--data-dir` or `DOGSAPKA_DATA_DIR`):

```
DOGSAPKA_AI_CLIPPER/ recordings/ clips/ exports/ thumbnails/ subtitles/ cache/ logs/ models/ database/ temp/
```

## Twitch setup

1. Go to <https://dev.twitch.tv/console> → *Register Your Application* → client type **Public**, any redirect URL (e.g. `http://localhost`).
2. Paste the **Client ID** into *Settings → Twitch* and click **Connect Twitch account** (device-code login; only `chat:read` is requested).
   Alternatively enter a Client Secret for app-only access (public stream status).
3. Add streamers on the *Streamers* page and confirm you are authorized.

## Windows build

```powershell
.\packaging\build_windows.ps1   # venv, tests, PyInstaller, bundles FFmpeg + Streamlink, Inno Setup installer
```

Produces `dist\DOGSAPKA AI CLIPPER\DOGSAPKA AI CLIPPER.exe` and `dist\installer\DOGSAPKA_AI_CLIPPER_Setup_0.1.0.exe`
(Start-menu shortcut, optional desktop icon, Hungarian/English installer). The Whisper model (default
`small`, ~460 MB) downloads into `models/` on first transcription; choose `tiny`/`base` for slow PCs.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/STATUS.md](docs/STATUS.md).
