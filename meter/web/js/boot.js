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
  initToTop();
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

/* The bosses' portraits and the wordmark, sent again once the game's data
   has been read (menu_host.py): the page is redrawn to show them. */
window.addPortraits = function (json) {
  let d;
  try { d = JSON.parse(json); } catch (e) { return; }
  const had = Object.keys(window.__PORTRAITS__ || {}).length;
  window.__PORTRAITS__ = d.portraits || {};
  if (d.logo && !window.__LOGO__) { window.__LOGO__ = d.logo; brandLogo(); }
  if (Object.keys(window.__PORTRAITS__).length !== had && typeof STATE !== 'undefined') {
    NODES.forEach((v) => v.el.remove());
    NODES = new Map();
    renderPage(STATE.page || []);
  }
};

window.addEventListener('pywebviewready', boot);
if (window.pywebview) boot();
