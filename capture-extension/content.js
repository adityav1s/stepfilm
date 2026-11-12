const notify = (type) => chrome.runtime.sendMessage({ type }).catch(() => {});
notify("page-ready");
document.addEventListener("click", () => notify("page-action"), true);
