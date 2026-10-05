/* Start: wire the static buttons and the frame, then tell the meter. */

/* ---- boot --------------------------------------------------------------- */
function boot() {
  if (READY) return;
  READY = true;
  document.querySelectorAll('[data-act]').forEach((b) => {
    b.addEventListener('click', () => notify(b.dataset.act, {}));
  });
  initWindowFrame();
  translateStatic();
  brandLogo();
  initToTop();
  notify('boot', {});
}

/* The header's name: the grimoire, then the Farever Book wordmark
   (assets/grimoire.png, wordmark.png). Without them, the plain text stays. */
function brandLogo() {
  const brand = document.querySelector('#top .brand');
  if (!brand || !window.__LOGO__) return;
  brand.textContent = '';
  brand.classList.add('logo');
  brand.title = 'Farever Book';
  if (window.__CREST__) {
    const crest = document.createElement('img');
    crest.className = 'crest';
    crest.src = window.__CREST__;
    crest.alt = '';
    brand.appendChild(crest);
  }
  const im = document.createElement('img');
  im.src = window.__LOGO__;
  im.alt = 'Farever Book';
  brand.appendChild(im);
}

/* The bosses' portraits, sent again once the game's data has been read
   (menu_host.py): the page is redrawn to show them. */
window.addPortraits = function (json) {
  let d;
  try { d = JSON.parse(json); } catch (e) { return; }
  const had = Object.keys(window.__PORTRAITS__ || {}).length;
  window.__PORTRAITS__ = d.portraits || {};
  if (Object.keys(window.__PORTRAITS__).length !== had && typeof STATE !== 'undefined') {
    NODES.forEach((v) => v.el.remove());
    NODES = new Map();
    renderPage(STATE.page || []);
  }
};

window.addEventListener('pywebviewready', boot);
if (window.pywebview) boot();
