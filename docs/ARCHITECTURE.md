# Architecture

```
dogsapka_clipper/
  core/        paths, typed settings, logging (token redaction), i18n EN/HU, EventBus, SecretStore (keyring/DPAPI)
  db/          SQLAlchemy models + versioned migrations (SQLite, WAL)
  twitch/      Helix client + OAuth (device code, client credentials, refresh), read-only IRC chat (anonymised)
  recording/   StreamRecorder (streamlink | file → ffmpeg segment muxer), SegmentRingBuffer, WallClockMapper, clip extraction
  detection/   chat activity, chat reactions, audio excitement, visual change, HighlightScorer
  processing/  ffmpeg utils, layout analysis, renderer, subtitles, QC, analysis (silence/duplicates), ranking,
               metadata, profiles, DB-backed JobQueue, ClipProcessor pipeline, system checks
  publishing/  OAuth loopback+PKCE, YouTube resumable upload, TikTok Content Posting API
  simulation/  synthetic stream + simulated chat, offline replay of the detection engine
  services/    Backend facade (asyncio loop thread), StreamMonitor, LiveSession
  ui/          PySide6 window, pages, widgets, theme, Qt bridge
```

## Threads

| Thread | Work |
|---|---|
| Qt GUI | UI only; long work goes through `run_async` (QThreadPool) |
| backend-loop (asyncio) | Helix polling, chat sockets, 1 Hz session ticks (scoring) |
| rec-<login> | supervises streamlink/ffmpeg, reads the live segment list |
| analyze-<login> | decodes each finished segment for audio/visual detectors |
| clip-<login> | extracts clips from the buffer |
| job-worker-N | processing jobs (configurable count) |

Backend → UI communication is one-way through `EventBus` → `QtBridge` (queued Qt signal).

## Live data flow

```
Helix poll ─live─► LiveSession ─► StreamRecorder ─► 4 s TS segments ─► SegmentRingBuffer
                       │                                   └─► analysis thread ─► audio / visual detectors ─┐
                       ├─► IRC chat ─► chat activity + reaction detectors (1 Hz) ───────────────────────────┤
                       │                                                                                    ▼
                       └─────────────────────────────── HighlightScorer (align, weight, merge, cooldown)
                                                          │ finalized event
                                                          ▼
                                   plan_window → protect buffer range → wait for post-event footage
                                   → extract_clip → Clip row + thumbnail → JobQueue "process"
                                   → ClipProcessor: layout analysis → transcription → layout → silence
                                     → ASS/SRT → render (NVENC|x264) → QC → metadata → ranking/duplicates
```

### Timing

Segments are reported by FFmpeg only after they are complete, so `report_time - segment_end_pts` is an
upper bound of the stream→wall-clock offset; the minimum seen is used (`WallClockMapper`). Chat lags the
stream (latency + typing), so chat signals are shifted back by 5 s before alignment. Scoring runs
`2 × segment + 3 s` behind real time so audio/visual results have arrived.

### Detection scoring

Each detector outputs 0..1 per second. The score is the weight-normalised sum over enabled, available
detectors, plus an agreement bonus when ≥ 2 signals fire and a small bonus for a single very strong
signal. A highlight opens when the score passes the threshold, stays open above 75 % of it, and
closed events within the merge window are combined into one. Baselines use bias-corrected EWMA
statistics and are persisted per streamer (never from simulations).

## Database

Tables: `streamers`, `monitoring_sessions`, `recording_segments`, `highlights`, `clips`, `processing_jobs`,
`exported_videos`, `editing_profiles`, `app_settings`, `schema_version`. Add migrations to
`db/database.py:MIGRATIONS`. Exports are detached, never cascaded, when clips/streamers are deleted.

## Security

Tokens/secrets: OS keyring → Windows DPAPI → user-only file. Logs pass through a redacting formatter.
OAuth uses device code (Twitch) or loopback + PKCE + state check (Google/TikTok). Minimal scopes:
`chat:read`; `youtube.upload` (+readonly for the channel name); `video.upload` (+`video.publish` only for direct post).
