# Stepfilm Capture extension

1. Start Stepfilm with `python3 server.py`.
2. Open `chrome://extensions` in Chrome.
3. Enable **Developer mode**, choose **Load unpacked**, and select this `capture-extension` folder.
4. Pin **Stepfilm Capture**. Open the extension, enter a title, and click **Start capture**.
5. Perform browser actions. Stepfilm automatically captures after page clicks; use **Capture current step** for any missed action.
6. Click **Stop & open editor**. Stepfilm opens the captured workflow for narration and export.

The extension captures only the visible active tab after a page click or when you explicitly click **Capture current step**. It does not read typed values or send images to a remote service. The toolbar badge shows the active capture state/count.
