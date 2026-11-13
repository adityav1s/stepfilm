# Stepfilm Capture extension

1. Start Stepfilm with `python3 server.py`.
2. Open `chrome://extensions` in Chrome.
3. Enable **Developer mode**, choose **Load unpacked**, and select this `capture-extension` folder.
4. Pin **Stepfilm Capture**. Open the extension, enter a title, and choose **Browser tab** or **Screen or window**. Click **Start screenshot + video** and grant Chrome’s capture permission. Microphone narration remains optional.
5. In Browser tab mode, page clicks add ordered screenshots automatically while video continues. In Screen/window mode, use **Capture current step** manually at each desktop action; browser page-click events are not captured for this mode. Close and reopen the popup as needed to check the elapsed recording time and screenshot count.
6. Click **Stop & open editor**. Review the continuous recording in **Captured tab video**, select the screenshot step where it belongs, and click **Insert after selected step**. The clip becomes a separate guide step.

Browser tab mode continues recording the tab selected at Start if you switch tabs; page clicks in other tabs are ignored, and the popup tells you to return to the original tab for more screenshots. Screen/window mode records the source selected in Chrome’s chooser and uses manual checkpoints from that live stream. This is browser-extension desktop capture, not a native desktop app; mobile capture is not supported. Microphone narration is optional and requires an explicit checkbox and Chrome permission. Screenshots include timing offsets; the editor uses those times to align transcript segments and editable captions to steps. Raw capture media uploads only to the local Stepfilm server; audio is sent to OpenAI only when the user chooses Transcribe. A 250 MB video cap and low-disk-space check preserve the screenshot recovery path. The extension does not read typed values or capture tab/system audio. The toolbar badge shows capture state/count.
