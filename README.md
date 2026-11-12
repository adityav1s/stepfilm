# Stepfilm

Stepfilm turns captured browser workflows into narrated MP4 tutorials. The editor, speech generation, and video rendering run on your computer.

## Run it

Install Python 3.9+ and FFmpeg with H.264 encoding (`libx264`). Make sure `ffmpeg` and `ffprobe` are on your PATH. On macOS, Stepfilm can use the built-in `say` command. On Linux or Windows, use an FFmpeg build with the `flite` filter or configure Piper.

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
3. Open the pinned **Stepfilm Capture** extension, start a capture, and click **Capture current step** after each meaningful browser action.
4. Click **Stop & open editor** to continue editing narration and export the MP4.

The extension captures the visible active tab after page clicks or when you click the manual capture button. It sends screenshots to the local Stepfilm server and does not read typed values.

For a quick first run without a capture, open `sample-guide.html` from this folder in Stepfilm.

## Create a tutorial

1. Install [Mimik](https://github.com/westpoint-io/mimik) and capture a browser workflow.
2. Review the screenshots and apply any needed blur in Mimik. Export as **HTML** with screenshots included.
3. Open the HTML guide in Stepfilm. Select each step to edit or play its narration. An empty line produces a silent scene.
4. Click **Export MP4**. Stepfilm renders a 720p video and downloads it. You can also play it in the editor.

Stepfilm uses the screenshots and annotations already embedded in the export. Each step is a still image timed to its narration. The current editor handles 1–50 steps and HTML guides under 50 MB. Edits last until you reload the editor. The original HTML remains your source.

Stepfilm imports Mimik’s HTML export format and runs as a separate app. The source is MIT licensed. Mimik, FFmpeg, Piper, and voice models have their own licenses.
