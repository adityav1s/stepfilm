# Stepfilm parity milestone

This is the release gate for the next Guidde-like milestone.

## PASS thresholds

- Three consecutive Chrome runs capture at least three ordered steps, preserve the count after popup close/reopen, and open the editor after Stop.
- A first-time creator can select, reorder, duplicate, delete, narrate, add a marker, add a callout, add a highlight, and redact a scene without leaving the editor.
- At least one annotation is baked into the screenshot bytes and remains visible in the exported MP4.
- The guide preview shows every step with a number, screenshot, and written instruction; at least four of five representative steps communicate the intended action without extra explanation.
- Three consecutive exports produce playable 1280×720 H.264/AAC MP4 files; failed export preserves the editable draft.
- A new user reaches the first capture in three explicit actions from the landing page, and blocking states contain recovery instructions.

## Not required yet

Hosted links, teams, analytics, camera recording, AI transcription, integrations, and multilingual voice are later phases. This milestone targets the core loop: capture → annotate → narrate → preview → export.

## Automated gate

Run `STEPFILM_PORT=8765 ./acceptance-test.sh` while Stepfilm is running. It checks Python/JavaScript syntax, manifest validity, server health, a three-step capture lifecycle, and invalid-image rejection. Manual Chrome, annotation visibility, redaction, comprehension, and three-export checks remain required before claiming full PASS.
