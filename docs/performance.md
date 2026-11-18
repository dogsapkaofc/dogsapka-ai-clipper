# Performance changes

Measured locally on Windows using the existing TypicalGamer source clip, without
changing output resolution, encoder quality, subtitle timing or facecam framing.
These are workload measurements, not a promise that every export is this much faster.

| Workload | Before | After |
| --- | --- | --- |
| Editor preview at 18 seconds, 360×640, median of 3 runs | 0.763 s | 0.213 s (3.58× faster) |
| Eight repeated metadata reads for one unchanged file | 0.524 s | 0.0022 s with a warm cache |
| Refresh 100 completed processing jobs | Up to 202 SELECTs | 3 SELECTs |
| Chat at 100 messages/s: retained messages | 6,000 | 500 |
| 2,000 chat evaluations, best of 5 runs | 0.416 s | 0.071 s (5.86× faster) |

The preview measurement produced identical pixels before and after. Chat signals
matched exactly across 240 evaluations including delayed and out-of-order messages.

## Changes

- Metadata uses a 128-entry cache keyed by file identity, size and nanosecond
  timestamps. File replacement/changes invalidate entries; callers get independent
  results. Non-file inputs and failures are not cached.
- Indexed local preview files seek to the preceding keyframe. Original clip time
  is restored for crop tracking and subtitles. Output seeking preserves frame
  selection between frame boundaries. Transport streams and other inputs keep
  their original decoding path.
- The queue batches export/clip lookups and retains unchanged widgets. Live
  preview images are extracted only when a new recording segment is available;
  failed extraction remains retryable.
- Chat activity retains the active window plus future batched messages, rather
  than an unnecessary minute of history. Evaluation time is monotonic, as in the
  live monitoring loop.
- Whisper keeps at most two cached model configurations. Active transcription
  retains its model reference. Cancellation is checked before loading a model and
  before advancing its lazy inference generator.
- Face detection instances are reused per worker, rather than mutated concurrently
  by different workers. Disabling face detection skips loading/inference.

## Regression checks

Run `python -m pytest` from the project root. Added coverage checks file cache
invalidation and bounds, 60fps and variable-frame-rate previews with timed captions
and moving crops, SQL query counts, widget reuse, delayed chat, model cache bounds,
cancellation and concurrent workers. The existing render, publishing and deleted
widget tests are retained.
