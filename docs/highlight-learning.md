# Smarter local highlight selection

The selector combines video, microphone/game audio and audience reactions. This is
local signal analysis and preference learning, not a vision-language model that
understands a game's story or every joke.

## Video and reactions

- Video analysis samples 160×90 grayscale frames at 2 fps. It compensates camera
  translation and global brightness changes, then looks for sustained local action
  relative to that streamer's usual activity. Blank/loading screens and abrupt
  cuts receive little evidence. It does not identify objects or read game text.
- Audio analyzes voice-band energy, rhythm and sustained loudness relative to the
  streamer's baseline. Sustained laughter/voiced reactions and audience reactions
  can keep a quieter funny moment even with a small chat.
- Message count and reaction messages belong to one evidence family. A busy chat
  is not counted twice as independent confirmation. Known generic noise and visual
  transitions alone stay below the default 60-point clipping threshold.
- Learned audio/chat/video baselines are saved separately for each real streamer.

Video analysis is enabled for new configurations. An existing explicit opt-out is
retained. Enable **Settings → Detection → Watch video: local action and scene
analysis (2 fps)** to activate it for existing setups. Changes apply to running
sessions without restarting the recording. Decoding video adds CPU work even
though the frame analysis itself is small.

## Teach your preferences

In the clip library, use **Good clip** or **Not interesting**. Clicking the selected
button again clears its rating. The display counts usable current ratings.

After at least four rated clips, nearby patterns of audio, laughter, sustained
voice, visual action and chat reactions receive a small correction of up to
±10 score points. Similar liked clips score higher; similar disliked clips score
lower. Unfamiliar patterns remain neutral. This correction cannot bypass the
noise safeguards at the default threshold.

The system learns only from explicit ratings. Successful automatic exports,
simulations, duplicates and clips without detection evidence are not training
labels. Re-rating replaces the old example. Up to the latest 500 usable ratings
influence selection, and live sessions see updates without a database query on
every second.

Ratings live in the local SQLite database separately from render edits, so a
background render cannot overwrite them. Restarting restores the same preference
model; deleting a clip removes its rating. No footage or ratings are uploaded to
train this model. Existing publishing settings are unchanged.
