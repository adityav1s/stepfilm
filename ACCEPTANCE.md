# Stepfilm parity milestone

This is the release gate for the next Guidde-like milestone.

## PASS thresholds

- Three consecutive Chrome runs capture at least three ordered screenshots while tab video continues uninterrupted. The popup shows an active capture indicator/timer and restores timer/count after close/reopen; Stop flushes final media and opens the editor. A separate run selects screen/window capture and uses manual checkpoints.
- Optional microphone permission is requested before capture. During a narrated run, mic and tab state are shown separately, narration survives Stop, and screenshots have monotonic capture offsets. Denying mic access allows tab-only recovery without losing screenshots.
- Captured narration transcribes into editable caption segments aligned to screenshot boundaries; caption text and times survive project save/reopen and VTT/SRT export. Playing a step's source narration seeks to the corresponding capture interval.
- Caption translations preserve source cues/timing; selected-language preview, burned-in MP4 captions, and rendered-timeline VTT/SRT agree after reordering and audio-source changes.
- The full transcript derived from captured captions cannot drift independently: edit timed caption rows to update it. Uploaded-file transcripts remain directly editable.
- The editor presents the captured clip separately from screenshots, allows preview and explicit insertion after the selected step, and keeps that clip stable through reorder, delete, project save/reopen, preview, and MP4 export. A run with no screenshots opens with the video clip as its first scene.
- A first-time creator can select, reorder, duplicate, delete, narrate, add a marker, add a callout, add a highlight, and redact a scene without leaving the editor.
- Bulk move, duplicate, and delete preserve selected scene order and media/audio references; deleting every scene is blocked.
- A transferred mobile video imports as a video step with original audio, survives project save/reopen, and exports to playable MP4.
- At least one annotation is baked into the screenshot bytes and remains visible in the exported MP4.
- The guide preview shows every step with a number, screenshot, and written instruction; at least four of five representative steps communicate the intended action without extra explanation.
- With AI configured, Draft tutorial generates an editable title, distinct step descriptions, imperative instructions, and correctly targeted callouts from screenshots. Manual authored text is never overwritten without confirmation; video-only steps are explicitly reported as skipped.
- Audio/video narration upload produces an editable full transcript that survives save/reopen and appears in HTML/Markdown exports. All narrated steps can generate, preview, save, and export AI voiceovers; steps without AI audio continue to use local speech.
- When the AI TTS request is rejected, the editor offers a one-click local speech selection. Text and voice settings remain intact; local export must ignore any stale AI clip and produce a playable audio track.
- Three consecutive exports produce playable 1280×720 H.264/AAC MP4 files; failed export preserves the editable draft.
- Theme, logo, and accent persist and appear in MP4/HTML/print preview; print-to-PDF and SCORM packages include expected guide/media/caption assets; portable ZIP opens offline.
- A new user reaches the first capture in three explicit actions from the landing page, and blocking states contain recovery instructions.

## Not required yet

Hosted links, teams, analytics, native desktop/mobile apps, camera/system audio, direct integrations, slide PDF, true forced word-level alignment, and native phone capture are later phases. The transcription fallback now requests Whisper-provided word timestamps where available; this is timestamped recognition, not forced alignment. Realtime TTS is available as an opt-in provider, with mocked protocol coverage; live API generation remains unverified. AI authoring, captured-mic transcription, translation, video analysis, and generated voiceover are optional OpenAI-backed features; media is sent only when the creator invokes those actions.

## Automated gate

Run `STEPFILM_PORT=8765 ./acceptance-test.sh` while Stepfilm is running. It checks Python/JavaScript syntax, manifest validity, caption timeline/translation mapping, mobile video import/audio, portable ZIP and SCORM structure, server health, capture lifecycle, saved narration, and mixed-media MP4 rendering. Manual Chrome screen/mic permission flows, real OpenAI calls, live LMS launch, popup recovery, uninterrupted capture, brand visual review, annotation/redaction visibility, viewer comprehension, and three consecutive exports remain required before claiming full PASS.
