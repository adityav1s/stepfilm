# Stepfilm

Stepfilm turns captured browser workflows into narrated MP4 tutorials. The editor and video rendering run on your computer. Optional AI authoring uses OpenAI for screenshot-based writing, audio transcription, and generated voice.

## Run it

Install Python 3.9+ and FFmpeg with H.264 encoding (`libx264`). Make sure `ffmpeg` and `ffprobe` are on your PATH. On macOS, Stepfilm can use the built-in `say` command. On Linux or Windows, use an FFmpeg build with the `flite` filter or configure Piper.

Caption overlays in MP4 export use Pillow. Realtime TTS uses the bounded `websockets` dependency. Install Python dependencies with `python3 -m pip install -r requirements.txt` (or `py -m pip install -r requirements.txt` on Windows).

From the extracted `stepfilm` directory:

```sh
python3 server.py
```

On macOS, you can also double-click `Start-Stepfilm.command` in this folder. Keep the Terminal window open while using the Chrome extension.

On Windows, use `py server.py`. The editor opens at http://127.0.0.1:8765. Keep the terminal open while you use it. Use `--port 0` if that port is taken.

For Piper, install `piper-tts`, download a voice model and its matching config, and start Stepfilm with the model path:

```sh
python3 server.py --piper-model /path/to/voice.onnx
```

## Capture from Chrome

Stepfilm includes a small local Chrome extension for creating a guide without a Mimik HTML export:

1. Start Stepfilm with `python3 server.py`.
2. In Chrome, open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select `capture-extension`.
3. Open the pinned **Stepfilm Capture** extension and start screenshot + video capture. Optionally enable **Record microphone narration**. Stepfilm asks for mic permission, then records the selected tab and your narration while page actions add ordered screenshots. Speak through each action; each screenshot marks the next transcript boundary. Use **Capture current step** for a missed screenshot.
4. Stop to open the editor. Review the continuous clip in **Captured tab video**, then use **Insert after selected step** to add it at the desired point in the guide. The clip remains a separate guide step and can be reordered, previewed, narrated, saved, and included in MP4 export.
5. In the editor’s step list, check multiple steps to move them together, duplicate the selection as a block, or delete it. Stepfilm preserves scene metadata and media references, and prevents deleting every step.

The extension captures the visible active tab after page clicks or when you click the manual capture button, while tab video and optional microphone narration record continuously in the background. Raw capture media stays in the local `~/.stepfilm/captured-clips` directory. The mic track is sent to OpenAI only when **Transcribe captured narration** is chosen in the editor. Stepfilm does not read typed values or record tab/system audio. Video capture is limited to 250 MB; low disk space or an upload failure offers a path to keep screenshots.

To use a phone-recorded walkthrough, transfer the video file to the computer running Stepfilm, then choose **Use a mobile recording → Choose video file** in the editor. Files up to 250 MB are normalized to local WebM, and original clip audio is retained for preview, transcription, and MP4 export. This imports a recording; it does not capture directly from a phone.

For a quick first run without a capture, open `sample-guide.html` from this folder in Stepfilm.

## Create a tutorial

1. (Optional) Install [Mimik](https://github.com/westpoint-io/mimik) and capture a browser workflow if you prefer its recorder.
2. Review the screenshots and apply any needed blur in Mimik. Export as **HTML** with screenshots included.
3. Open the HTML guide in Stepfilm. Select each step to edit or play its narration. An empty line produces a silent scene.
4. Click **Export MP4**. Stepfilm renders a 720p video and downloads it. You can also play it in the editor.

### AI tutorial drafting

Choose **Continue with ChatGPT** to use eligible ChatGPT plan allowance without an API key, or connect an OpenAI API key for separately billed API use. ChatGPT sign-in is available for eligible Go, Plus, Pro, and Business plans. In Stepfilm, plan usage covers text/image tasks: screenshot-based tutorial drafts, analysis of sampled video frames, and caption translation. It does not cover transcription/audio input or voice generation. Usage counts toward ChatGPT plan limits; review app limits and credit permissions in ChatGPT settings. Stepfilm stores refreshable OAuth credentials in an owner-only-permission file at `~/.stepfilm/chatgpt-plan.json`; use **Disconnect ChatGPT** to remove that local connection. Install `requirements.txt` to enable secure ID-token validation.

**Draft tutorial** sends guide screenshots to OpenAI to propose an editable title, step description, instruction, and callout. **Transcribe captured narration** needs API billing and uses click timestamps to align recognized speech and editable captions with screenshot steps; when timestamped transcription is unavailable, short pause-aware chunks request Whisper word timestamps and group them into readable cues. If word timestamps are unavailable, captions fall back to estimated phrase/chunk spans. These are model-provided timestamps, not forced alignment. Translate cues into Spanish, French, German, Italian, Portuguese, Japanese, Korean, or Chinese without changing original text or timing, then download either language as VTT or SRT. An audio/video file can also be transcribed into a full-guide transcript using API billing. **Generate voiceover for all steps** or generate it for the selected step using the API; the chosen AI voice is retained in the project and used on MP4 export. Existing local speech remains available without an API key.

The AI voice route uses the OpenAI Speech API model `gpt-4o-mini-tts` by default. Set `STEPFILM_TTS_MODEL` to choose a different model ID supported by `/v1/audio/speech`. OpenAI schedules `gpt-4o-mini-tts` for removal on January 6, 2027. To use its recommended `gpt-realtime-2.1-mini` successor, set `STEPFILM_TTS_PROVIDER=realtime` and install `requirements.txt`; this selects a separate Realtime WebSocket path, requests 24 kHz PCM, and converts the result to MP3 with FFmpeg. `STEPFILM_TTS_MODEL` may override the Realtime model when that provider is selected. Do not pass a Realtime model to `/v1/audio/speech`. If Realtime generation fails, Stepfilm retains the narration and offers **Use local speech for this step**. The Speech API path remains the default and uses `STEPFILM_TTS_MODEL` independently.

AI drafting sends screenshots you choose; video-step analysis scans a bounded set of low-resolution frames, then selects up to 12 frames across the timeline while favoring visible changes. The selected frames (640 pixels wide or less) include timestamps, and the image payload is capped at 6 MB. On ChatGPT-plan access this sends the selected still images through the Responses API; raw video/audio inputs are not supported. This can improve scene coverage but cannot establish what happened between stills; brief actions can be missed. The editor shows the selected times. Review the suggested sequence and verify each action. Transcription sends the captured mic track or an uploaded narration file only after you invoke it and requires API billing. The capture extension sends captured media to the local Stepfilm server. Review generated content and caption timing before sharing. AI voice tracks are synthetic. Live AI calls are not verified by the local synthetic-video test.

Stepfilm uses screenshots and annotations already embedded in the export. Screenshot steps are still images timed to narration; an inserted tab-video step plays for its captured duration. The current editor handles 1–50 steps and HTML guides under 50 MB. Draft edits are saved locally in the browser; use **Save project** for a durable `.stepfilm.json` file. That project keeps local references to captured clips. Use **Export portable guide ZIP** to bundle the guide HTML, captured WebM clips, and available caption tracks for offline viewing on another computer. Share the ZIP manually; Stepfilm does not host it or control viewer access. The original HTML remains your source.

Stepfilm imports Mimik’s HTML export format and runs as a separate app. The source is MIT licensed. Mimik, FFmpeg, Piper, and voice models have their own licenses.

Use **Print / Save as PDF** to open a print-ready guide in a new browser tab. Choose **Save as PDF** in the browser print dialog; this is browser print-to-PDF, not a direct PDF file download. It includes embedded screenshots, written instructions, descriptions, and captions.

Use **Export SCORM 1.2 package** to create an LMS-ready ZIP with a manifest, the tutorial page, captured video, and caption tracks. Learners use **Mark tutorial complete** to report completion to a compatible LMS. This is a course package export; it does not connect Stepfilm to an LMS or other external service.

### Guide branding

In the editor, choose a Clean, Editorial, or Bold guide theme, set an accent color and title style, and optionally add a PNG, JPEG, or WebP logo up to 350 KB. Branding settings are saved in the project and applied to the guide preview, standalone HTML export, print-to-PDF view, and MP4 scenes.
