# YouTube publication schedule

Settings → Publishing includes an optional automatic YouTube schedule. Defaults:
**09:00, 15:00, 21:00, Europe/Budapest**. All three times and the IANA timezone are editable.
It is disabled until explicitly enabled. The existing global automatic-upload confirmation,
per-account automatic-upload switch, streamer permissions, quality checks and duplicate checks still apply.

Each ready export is uploaded **privately first**. After YouTube returns the video ID,
the app persists it and selects the next available publication time, at least 15 minutes away.
It calls the official `videos.update` API with `status.privacyStatus=private` and
`status.publishAt` in UTC. YouTube then publishes it publicly at that time, even if this app is closed.
The upload and scheduling must finish before closing the app. YouTube processing can still delay availability.

There are at most three scheduled slots per channel per local calendar day. Extra clips
roll into later days. Empty slots are not filled unless ready clips exist. DST gaps are skipped;
repeated clock times are used once. Persisted reservations share capacity across workers,
running app instances and multiple connections to the same channel. Changing the times,
timezone or enable switch does not cancel or move already scheduled videos on YouTube.
Manage existing schedules in YouTube Studio.

Manual publishing has a **Scheduled public (3/day)** mode using the same slots. Explicit
private, unlisted and public manual uploads retain their existing immediate behavior.
Manual immediate uploads and videos uploaded outside this app are outside this schedule's daily limit.

If scheduling fails, the video ID and reservation stay on disk as `schedule_pending`.
**Processing → Publish all again** retries the scheduling operation using that same video.
An expired reservation is moved to a safe future slot. A failed upload does not reserve a slot.
Concurrent uploads to the same account/export are claimed atomically. When a resumable upload's
final response is lost, the client probes its session before sending more bytes. If completion
remains unknown, it blocks another insert and asks the user to check YouTube Studio. An interrupted
claim with no persisted video ID needs manual reconciliation; it is not automatically reuploaded.

Existing YouTube connections may need **Reauthorize** in Settings → Publishing. The new login
requests `youtube.force-ssl` as well as the existing upload/read scopes because `videos.update`
does not accept `youtube.upload` alone. Select the same channel; reauthorization preserves
the account ID and pending uploads, and restores the old token if a different channel is selected.
The Google Cloud OAuth consent configuration must allow the requested scopes. Google may restrict
public visibility for unaudited API projects; YouTube's API response remains authoritative.

The pure time allocator, account orchestration and API layer are covered using local SQLite
databases and mocked YouTube HTTP responses. Tests do not upload to a real YouTube channel.

API references:

- https://developers.google.com/youtube/v3/docs/videos/update
- https://developers.google.com/youtube/v3/docs/videos#status.publishAt
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
