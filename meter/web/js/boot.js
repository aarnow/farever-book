/* Start: wire the static buttons and the frame, then tell the meter. */

/* ---- boot --------------------------------------------------------------- */
function boot() {
  if (READY) return;
  READY = true;
  document.querySelectorAll('[data-act]').forEach((b) => {
    b.addEventListener('click', () => notify(b.dataset.act, {}));
  });
  initWindowFrame();
  notify('boot', {});
}

window.addEventListener('pywebviewready', boot);
if (window.pywebview) boot();
