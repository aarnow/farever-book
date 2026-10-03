/* Start: wire the static buttons and the frame, then tell the meter. */

/* ---- boot --------------------------------------------------------------- */
function boot() {
  if (READY) return;
  READY = true;
  document.querySelectorAll('[data-act]').forEach((b) => {
    b.addEventListener('click', () => notify(b.dataset.act, {}));
  });
  initWindowFrame();
  brandLogo();
  notify('boot', {});
}

/* The header's name: the game's own FAREVER wordmark (extracted from the
   player's game files), FRANCE under it in the flag's colours. Without the
   wordmark, the plain text stays. */
function brandLogo() {
  const brand = document.querySelector('#top .brand');
  if (!brand || !window.__LOGO__) return;
  brand.textContent = '';
  brand.classList.add('logo');
  brand.title = 'Farever France';
  const im = document.createElement('img');
  im.src = window.__LOGO__;
  im.alt = 'Farever';
  brand.appendChild(im);
  brand.appendChild(el('span', 'fr', 'France'));
}

window.addEventListener('pywebviewready', boot);
if (window.pywebview) boot();
