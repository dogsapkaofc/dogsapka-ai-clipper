# Implementation status

Phases 1–8 of the specification have working implementations. What was **verified in this
development environment** (Linux container, no GPU, no Twitch credentials, restricted network):

| Component | Verification |
|---|---|
| Database, settings, migrations | unit tests |
| Twitch Helix live/offline, auth failures, 401 refresh, device-code flow, IRC parsing | unit tests with mocked HTTP |
| Monitor (grace period, error reporting, unauthorized channels ignored) | unit tests |
| Chat spike (small vs large streamer, normal fluctuation), reactions EN/HU, audio voice vs explosion vs music, scorer merge/cooldown/auto-threshold | unit tests |
| Circular buffer, protection, wall-clock mapping, clip window, accurate trimming, corrupt segment skipping | unit + FFmpeg tests |
| Rendering of all layouts with subtitles/zoom/fade/silence cut, fps cap, QC failure detection | FFmpeg tests |
| Job queue: fail/retry, pause/resume, cancel, crash recovery | unit tests |
| YouTube resumable upload, TikTok inbox/direct-post rules, OAuth loopback + state check | mocked HTTP tests |
| **Full live pipeline** (real-time file stream → recorder → detectors → 2 highlights → clips → 9:16 exports → QC) | end-to-end simulation run, both planted moments detected |
| UI pages render; live graphs/preview update during a simulation | offscreen screenshots |

Not verifiable here (please test on the target Windows PC):

* **Real Twitch recording via Streamlink** – the recorder runs the same FFmpeg segment pipeline as
  simulation, but the Streamlink stage was not executed against a real channel.
* **Whisper transcription** – model download was blocked (HTTP 403) by the sandbox network; the
  "transcription unavailable → subtitles skipped" path was verified. Subtitle generation from
  transcripts is unit-tested.
* **NVENC** – no GPU here; NVENC is enabled only after a real test encode succeeds, otherwise CPU.
* **Windows packaging** – spec/installer/build script written; the installer must be built on Windows
  with Inno Setup. Video playback in the editor uses QtMultimedia (not testable headless).
* Real YouTube / TikTok uploads require your own developer credentials (TikTok app approval).

## Windows build & verification (2026-11-17, Windows 11, Python 3.13, RTX 4060 Laptop)

* Tests: 78/78 pass. `build_windows.ps1` (Inno Setup 6.7.3) → `dist\installer\DOGSAPKA_AI_CLIPPER_Setup_0.1.0.exe`.
* Built exe (bundled FFmpeg 9.0.2 + Streamlink 8.6.3) ran a simulation: highlight → clip → CPU Whisper
  fallback → 1080×1920 `h264_nvenc` export. Installing via the setup wizard itself was not tested.

Fixes ported from the HIRX Windows test run, plus one new:

* `av>=11,<19` pinned – PyAV 19 removed `av.open(metadata_errors=…)`, breaking faster-whisper 1.2.x.
* Whisper `auto` picked CUDA whenever an NVIDIA GPU exists; without cuBLAS the job crashed and the cached
  CUDA model hung the next job. The first CUDA runtime failure is now remembered and CPU is used.
* `tool_cmd` no longer splits `python -m streamlink` on spaces inside the Python path.
* Backend background tasks are referenced and cancelled on shutdown.
* Tests: FFmpeg skip marker on `test_qc_detects_problems`; the `db` fixture uses a temp file DB instead
  of `:memory:` (one shared sqlite connection across job-worker threads made `test_jobs` flaky).

### "Modern glass" design

* `ui/theme.py`: deeper background with a soft purple/cyan radial glow, translucent gradient cards (18 px
  radius), glass buttons/inputs with hover and focus states, restyled menus, checkboxes, sliders, scrollbars.
* Motion: sliding gradient highlight behind the active sidebar item; pages fade in while gliding up;
  cards glow (animated shadow + border) on hover; wheel scrolling glides (touchpads unchanged); stat numbers
  count up; the window fades in when opened or restored from the tray.

### YouTube live streamers, tray mode, multiple upload accounts

* **YouTube live streamers** (`dogsapka_clipper/youtube/live.py`, `YouTubeMonitor`): add a channel by @handle,
  URL or channel ID (Streamers → Add → YouTube). Needs a YouTube Data API v3 key (Settings → YouTube live,
  stored encrypted). Live detection uses the uploads playlist + `videos.list` (2 units per check, never
  `search.list` = 100 units); default every 120 s. Recording via Streamlink on the live video URL. Live chat
  via `liveChatMessages.list` (API key, falls back to a connected YouTube account's token), polled every
  20 s; chat is evaluated with a matching delay and the chat detectors now ignore messages newer than the
  evaluation time. Without chat, audio (+ optional visual) detection still works.
  *Not tested against a real YouTube live stream / real API key yet.*
* **System tray**: the window's X hides to the tray; right-click the tray icon → "Bezárás / Close" exits
  (with the usual safe-stop prompt during live sessions). Left-click toggles the window. The frameless
  window now has WS_MINIMIZEBOX/WS_SYSMENU, so clicking the taskbar button minimizes/restores it.
* **Multiple upload accounts** (`publishing/accounts.py`, table `publish_accounts`, schema v2): any number
  of YouTube channels and TikTok accounts, each with its own token, privacy/mode, streamer filter and
  auto-upload switch. Auto-upload needs the global switch + a one-time rights confirmation; it uploads only
  QC-passed, non-duplicate, non-simulation exports, once per account, via the job queue (retryable).
  The publish dialog uploads to any selected accounts. Old single YouTube/TikTok tokens become accounts.
* Tests: `tests/test_youtube_live.py`, `tests/test_accounts.py` (incl. v1 → v2 migration). 106 tests pass.

### Facecam not detected on real streams (fixed)

On a real TenZ (Valorant) recording the facecam was not found and both clips were rendered as
gameplay-only crops. Cause: the OpenCV *frontal* Haar cascade found 0 faces in the cam (streamer looks
at the monitor: turned head, looking down, glasses); its 15 hits were all HUD/game faces.

* Face detection now uses OpenCV's **YuNet** DNN model (bundled: `dogsapka_clipper/assets/`, 230 KB,
  also packaged by `dogsapka.spec`); Haar remains the fallback. Faces smaller than 1/64 of the frame
  width (kill-feed portraits) are ignored. On the TenZ clips: 20/20 frames hit inside the cam.
* Cam border search: a static line inside the cam (gaming chair) was taken as the left border. If
  the strip between the line and the frame edge is static (< 25 % of the frame's median activity)
  the cam now extends to the edge. Result on both TenZ clips: `Rect(0, 340, 376, 248)` = the real cam.
* Tests: `test_yunet_face_model_is_bundled_and_used`, `test_cam_flush_with_edge_ignores_static_line_inside_cam`
  (fails with the old border logic).

## Known limitations / next steps

1. Live detection uses Helix polling (official, ≥ 15 s); EventSub WebSocket `stream.online` would give
   instant notifications.
2. Visual detection is generic (scene cuts, motion bursts); game-specific "victory screen" recognition
   is not implemented.
3. Titles/descriptions are rule-based from transcript and events; an optional local LLM is a future add-on.
4. Hungarian UI covers navigation and main labels; some detailed settings/editor texts are still English.
5. Clips use the source stream audio; separate microphone/game tracks are not available from Twitch.
6. Custom editing profiles are stored in the DB (`editing_profiles`); a dedicated profile editor dialog is
   still to be built (built-ins can be overridden per clip in the editor).

## Changelog

### Rename + A/V sync + facecam
* Product renamed to **DOGSAPKA AI CLIPPER** (package `dogsapka_clipper`, data folder
  `%LOCALAPPDATA%\DOGSAPKA_AI_CLIPPER`, new installer AppId).
* **Audio/video desync fixed** (it was a bug, not a setting): clips were built by joining the 4 s TS
  recording segments with FFmpeg's concat *demuxer*, which shifted audio a little at every segment
  boundary (measured up to -537 ms, growing with clip position). Segments are now byte-joined with the
  concat *protocol* and trimmed with exact output seeking. Silence trimming no longer re-times video by
  frame count (drifted ~45 ms per export, worse on variable-frame-rate Twitch video). Measured on
  exports: -143/-163 ms before, -13/+10 ms after. Regression tests: `tests/test_buffer_clip.py`.
* **Facecam -> facecam top / gameplay bottom**: every vertical profile uses the `auto` layout; when a
  facecam is detected the video is always cut as facecam on top + game below (otherwise the profile's
  fallback: smart crop, or blurred background for Cinematic). Detection now samples at higher
  resolution, clusters faces across frames to ignore false positives, and finds the real facecam
  rectangle from continuous static borders and motion contrast (93-100 % overlap on test streams; the
  rectangle can still be adjusted in the editor). Tests: `tests/test_facecam.py`.
