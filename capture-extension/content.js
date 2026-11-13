const notify = (type) => chrome.runtime.sendMessage({ type }).catch(() => {});
if (!window.__stepfilmCaptureHookInstalled) {
  window.__stepfilmCaptureHookInstalled = true;
  notify("page-ready");
  document.addEventListener("click", () => notify("page-action"), true);
}
