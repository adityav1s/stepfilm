(() => {
  if (window.opener && !window.opener.closed) {
    window.opener.postMessage({ type: "stepfilm-chatgpt-connected" }, window.location.origin);
    window.setTimeout(() => window.close(), 900);
  }
})();
