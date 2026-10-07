/* An overlay over the game (menu_host.Overlay): "meter", "goals", "luck",
   or "tip" (the meter's player details under the mouse, menu_host.Tip), set
   by window.__OVERLAY__. State arrives through applyOverlay(json). */
const OV_ID = window.__OVERLAY__;

/* The interface's language, as in the window (core.js): the texts are
   written in French, tr() gives them in the chosen one ({lang, dict: French
   text -> translation}, i18n.py). {name} placeholders are filled from `vars`. */
let I18N = window.__I18N__ || { lang: 'fr', dict: {} };
function tr(s, vars) {
  let r = (I18N.dict && I18N.dict[s]) || s;
  if (vars) r = r.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));
  return r;
}
document.documentElement.lang = I18N.lang || 'fr';
let OV = null;                  // the last state
let SETTINGS = false;           // the gear's panel open

function api() { return window.pywebview && window.pywebview.api; }

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function btn(cls, text, title, onClick) {
  const b = el('button', cls, text);
  b.type = 'button';
  if (title) b.title = title;
  b.addEventListener('mousedown', (e) => e.stopPropagation());
  b.addEventListener('click', onClick);
  return b;
}

window.applyTheme = function (css) {
  const st = document.getElementById('css');
  if (st) st.textContent = css;
};

/* Another language (called by the host): its catalogue, then drawn again. */
window.applyLang = function (json) {
  try { I18N = JSON.parse(json); } catch (e) { return; }
  document.documentElement.lang = I18N.lang || 'fr';
  render();
};

window.applyOverlay = function (json) {
  const mine = (SETTINGS || GRIP) && OV ? OV.style : null;
  try { OV = JSON.parse(json); } catch (e) { return; }
  // the settings open: they hold the style being chosen, and a slider held
  // must not be redrawn under the mouse; the grip held, its height
  if (SETTINGS) { if (mine) OV.style = mine; return; }
  if (GRIP && mine) OV.style = mine;
  // the add form keeps its own state: a push must not wipe what is typed
  if (document.activeElement && document.activeElement.tagName === 'INPUT') {
    renderList();
    return;
  }
  render();
};

/* ---- moving: the header drags the window ------------------------------- */
let DRAG = null;
function startDrag(e) {
  if (e.button !== 0 || !api()) return;
  const sx = e.screenX, sy = e.screenY;
  api().ov('rect').then((r) => {
    if (!r) return;
    DRAG = { sx: sx, sy: sy, x: r[0], y: r[1], pend: null };
  });
}
document.addEventListener('mousemove', (e) => {
  if (!DRAG) return;
  const x = DRAG.x + e.screenX - DRAG.sx, y = DRAG.y + e.screenY - DRAG.sy;
  if (DRAG.pend) { DRAG.pend = [x, y]; return; }
  DRAG.pend = [x, y];
  requestAnimationFrame(() => {
    if (!DRAG || !DRAG.pend) return;
    api().ov('move', DRAG.pend);
    DRAG.pend = null;
  });
});
document.addEventListener('mouseup', () => {
  if (!DRAG) return;
  DRAG = null;
  api().ov('drop');
});

/* ---- the window follows the content's size ---------------------------- */
let LAST_SIZE = '';
function reportSize() {
  const box = document.getElementById('ov');
  if (!box || !api()) return;
  // whole pixels: the window (and its cut, the frame's outline) is, and a
  // panel a fraction short would leave its last row to the background
  box.style.minWidth = box.style.minHeight = '';
  const r = box.getBoundingClientRect();
  const w = Math.ceil(r.width), h = Math.ceil(r.height);
  // the measure is zoomed (the player's size), the minimum is not
  const z = parseFloat(box.style.zoom) || 1;
  box.style.minWidth = (w / z) + 'px';
  box.style.minHeight = (h / z) + 'px';
  const s = w + 'x' + h;
  if (s === LAST_SIZE) return;
  LAST_SIZE = s;
  api().ov('size', [w, h]);
}
new ResizeObserver(reportSize).observe(document.getElementById('ov'));
// the panel held at its last whole size (above) never shrinks by itself:
// measured again after each drawing, which a shorter content needs
let SIZE_DUE = false;
function sizeSoon() {
  if (SIZE_DUE) return;
  SIZE_DUE = true;
  requestAnimationFrame(() => { SIZE_DUE = false; reportSize(); });
}

/* ---- drawing ------------------------------------------------------------ */
function head(title, extra) {
  const h = el('div', 'head');
  h.addEventListener('mousedown', startDrag);
  h.appendChild(titleEl(title));
  // the title centred on its own: the rest on the right
  h.appendChild(el('span', 'sp'));
  (extra || []).forEach((x) => h.appendChild(x));
  return h;
}

/* A window's title as the game writes it: its own bitmap font (Platypi
   Bold, menu_host._title_font), in its title brown, centred over the
   header; the page's font without it. */
const TITLE_FONT = window.__TITLE_FONT__ || null;
const TITLE_INK = '#6A4E44';
let TITLE_ATLAS = null;
const TITLE_CACHE = {};
function titleEl(text) {
  const span = el('span', 't', text);
  const f = TITLE_FONT;
  if (!f || !f.glyphs) return span;
  const chars = Array.from(text);
  if (!chars.every((c) => f.glyphs[c.codePointAt(0)] || c === ' ')) return span;
  if (!TITLE_ATLAS) {
    TITLE_ATLAS = new Image();
    TITLE_ATLAS.src = f.img;
    TITLE_ATLAS.addEventListener('load', () => render());
  }
  if (!TITLE_ATLAS.complete || !TITLE_ATLAS.naturalWidth) return span;
  if (!TITLE_CACHE[text]) {
    const space = f.glyphs[32] ? f.glyphs[32][6] : Math.round(f.size / 4);
    let w = 0;
    chars.forEach((c) => { const g = f.glyphs[c.codePointAt(0)]; w += g ? g[6] : space; });
    const cv = document.createElement('canvas');
    // even sizes: centred, it falls on whole pixels
    cv.width = Math.max(2, w + 2 + ((w + 2) % 2));
    cv.height = f.lineHeight + (f.lineHeight % 2);
    const cx = cv.getContext('2d');
    let x = 0;
    chars.forEach((c) => {
      const g = f.glyphs[c.codePointAt(0)];
      if (!g) { x += space; return; }
      if (g[2] && g[3]) cx.drawImage(TITLE_ATLAS, g[0], g[1], g[2], g[3], x + g[4], g[5], g[2], g[3]);
      x += g[6];
    });
    // the glyphs are white: inked in the game's title colour
    cx.globalCompositeOperation = 'source-in';
    cx.fillStyle = TITLE_INK;
    cx.fillRect(0, 0, cv.width, cv.height);
    TITLE_CACHE[text] = { src: cv.toDataURL(), w: cv.width, h: cv.height };
  }
  const t = TITLE_CACHE[text];
  const im = el('img', 't timg');
  im.src = t.src;
  im.width = t.w;
  im.height = t.h;
  // a bitmap font: shown at its own pixels whatever the overlay's size
  // (the player's zoom would blur it)
  const z = (OV && OV.style && OV.style.scale || 100) / 100;
  if (z !== 1) im.style.zoom = 1 / z;
  im.alt = text;
  im.draggable = false;
  return im;
}

function render() {
  const box = document.getElementById('ov');
  if (!OV) { box.textContent = ''; return; }
  // the meter keeps its tabs between redraws (their background slides)
  if (OV_ID !== 'meter') box.textContent = '';
  // the player's size for this overlay (its gear), the window following
  const st = OV.style || {};
  box.style.zoom = (st.scale || 100) / 100;
  sizeSoon();
  if (OV_ID === 'esc') renderEsc(box);
  else if (OV_ID === 'tip') renderTip(box);
  else if (OV_ID === 'picker') renderPicker(box);
  else if (OV_ID === 'meter') renderMeter(box);
  else if (OV_ID === 'luck') renderLuck(box);
  else if (OV_ID === 'goals') renderGoals(box);
}

function renderMeter(box) {
  const m = OV.meter || { rows: [] };
  const heal = m.tab === 'heal';
  const h = head(tr('Meter'));
  h.appendChild(gearBtn());
  if (SETTINGS) {
    box.textContent = '';
    box.className = '';
    box.appendChild(h);
    box.appendChild(settingsPanel());
    return;
  }
  // its parts kept from one redraw to the next (the tabs' slide, the list's
  // scroll and its scrollbar held), only their content renewed
  let parts = box.querySelector(':scope > .mtabsbar') ? box : null;
  if (!parts) {
    box.textContent = '';
    box.appendChild(h);
    // the fight's duration, then damage or heals: two tabs side by side
    // across the whole width, the shown one's background sliding to it
    const bar = el('div', 'mtabsbar');
    const line = el('div', 'mline');
    line.appendChild(el('div', 'mclock'));
    // its height back to its content's (the footer's grip set one)
    const fit = btn('mfit', '', tr('Hauteur automatique'), () => {
      const st = Object.assign({ opacity: 100, scale: 100, maxh: 0 }, OV.style || {});
      st.maxh = 0;
      OV.style = st;
      const list = document.querySelector('.mrows');
      if (list) list.style.maxHeight = '';
      line.classList.remove('sized');
      sizeSoon();
      if (api()) api().notify('ov_style', { id: OV_ID, opacity: st.opacity, scale: st.scale, maxh: 0, save: true });
    });
    fit.innerHTML = '<svg viewBox="0 0 12 14" width="11" height="13"><path d="M6 1 2.5 4.5h7zM6 13 2.5 9.5h7z" fill="currentColor"/><rect x="2" y="6.3" width="8" height="1.4" fill="currentColor"/></svg>';
    line.appendChild(fit);
    bar.appendChild(line);
    const tabs = el('div', 'mtabs');
    [['dmg', tr('Dégâts')], ['heal', tr('Soins')]].forEach(([k, t]) => {
      const b = btn('mtab', t, null, () => {
        if (tabs.dataset.tab === k) return;
        setTabs(tabs, k);                   // at once, the push follows
        if (api()) api().notify('ov_tab', { tab: k });
      });
      b.dataset.k = k;
      tabs.appendChild(b);
    });
    bar.appendChild(tabs);
    box.appendChild(bar);
    const body = el('div', 'body');
    body.appendChild(el('div', 'rkr h'));
    body.appendChild(el('div', 'mrows'));
    box.appendChild(body);
    box.appendChild(gripEl());
  } else {
    box.replaceChild(h, box.firstChild);
  }
  box.className = heal ? 'heal' : '';
  const tabs = box.querySelector('.mtabs');
  tabs.querySelectorAll('.mtab').forEach((b) => { b.textContent = b.dataset.k === 'heal' ? tr('Soins') : tr('Dégâts'); });
  setTabs(tabs, heal ? 'heal' : 'dmg');
  const clock = box.querySelector('.mclock');
  clock.className = 'mclock' + (m.fight ? ' hot' : '');
  clock.textContent = tr('Durée : {t}', { t: m.time || '0:00' })
    + (m.fight ? ' ' + tr('(en cours)') : m.held ? ' ' + tr('(terminé)') : '');
  // the rift reports' ranking (report.js rankTable): the class colour
  // filling the row as far as the player's share against the best; the
  // group always listed, at nothing until it fights
  const th = box.querySelector('.rkr.h');
  th.textContent = '';
  th.appendChild(el('span', 'ic'));
  [tr('Joueur'), heal ? 'HPS' : 'DPS', tr('Total'), tr('Part')]
    .forEach((t, i) => th.appendChild(el('span', i ? 'num' : 'nm', t)));
  const list = box.querySelector('.mrows');
  // the player's height for the list: past it, it scrolls
  const maxh = (OV.style || {}).maxh || 0;
  list.style.maxHeight = maxh ? maxh + 'px' : '';
  box.querySelector('.mline').classList.toggle('sized', !!maxh);
  const rows = [];
  if (!(m.rows || []).length) rows.push(el('div', 'empty', tr('En attente d’un combat…')));
  (m.rows || []).forEach((r) => {
    const row = el('div', 'rkr' + (r.ck ? ' c-' + r.ck : '') + (r.rank <= 3 && !r.zero ? ' top' : '')
      + (r.zero ? ' zero' : ''));
    row.style.setProperty('--fill', (Math.max(0, Math.min(1, r.f || 0)) * 100) + '%');
    const ic = el('span', 'ic');
    const icon = (window.__ICONS__ || {})[r.ck];
    if (icon) { const im = el('img'); im.src = icon; im.alt = ''; ic.appendChild(im); }
    row.appendChild(ic);
    row.appendChild(el('span', 'nm', r.rank + '. ' + r.name));
    row.appendChild(el('span', 'num', r.rate));
    row.appendChild(el('span', 'num', r.total));
    row.appendChild(el('span', 'num', r.pct));
    // its details under the mouse, damage or heals as the tab
    if (r.tip) {
      row.addEventListener('mouseenter', () => { TIP_ROW = r.name; if (api()) api().ov('tip', r.tip); });
      row.addEventListener('mouseleave', () => { TIP_ROW = null; if (api()) api().ov('tip', null); });
      if (TIP_ROW === r.name && api()) api().ov('tip', r.tip);   // kept fresh while hovered
    }
    rows.push(row);
  });
  list.replaceChildren(...rows);
}

/* The footer, dragged: the list's greatest height (past it, the list
   scrolls), shown as it is set, saved once let go. */
let GRIP = null;
function gripEl() {
  const g = el('div', 'grip');
  g.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const list = document.querySelector('.mrows');
    if (!list) return;
    const z = parseFloat(document.getElementById('ov').style.zoom) || 1;
    // at least one player's row
    const row = list.querySelector('.rkr') || list.firstElementChild;
    const one = row ? row.getBoundingClientRect().height / z + 4 : GRIP_MIN;
    GRIP = { y: e.screenY, h: list.getBoundingClientRect().height / z, z: z, min: Math.ceil(one) };
    list.classList.add('sizing');
    g.setPointerCapture(e.pointerId);
  });
  g.addEventListener('pointermove', (e) => {
    if (!GRIP) return;
    const list = document.querySelector('.mrows');
    let v = Math.round(GRIP.h + (e.screenY - GRIP.y) / GRIP.z);
    v = Math.max(GRIP.min, Math.min(GRIP_MAX, v));
    // the height being set shown as it is, the list shorter or not
    GRIP.v = v;
    list.style.maxHeight = '';
    list.style.height = v + 'px';
    sizeSoon();
  });
  const done = () => {
    if (!GRIP) return;
    const list = document.querySelector('.mrows');
    const st = Object.assign({ opacity: 100, scale: 100, maxh: 0 }, OV.style || {});
    if (GRIP.v != null) st.maxh = GRIP.v;
    const line = document.querySelector('.mline');
    if (line) line.classList.toggle('sized', !!st.maxh);
    GRIP = null;
    OV.style = st;
    if (list) {
      list.classList.remove('sizing');
      list.style.height = '';
      list.style.maxHeight = st.maxh ? st.maxh + 'px' : '';
    }
    sizeSoon();
    if (api()) api().notify('ov_style', { id: OV_ID, opacity: st.opacity, scale: st.scale, maxh: st.maxh, save: true });
  };
  g.addEventListener('pointerup', done);
  g.addEventListener('pointercancel', done);
  return g;
}
const GRIP_MIN = 30, GRIP_MAX = 900;

let TIP_ROW = null;

function setTabs(tabs, k) {
  tabs.dataset.tab = k;
  tabs.classList.toggle('heal', k === 'heal');
  tabs.querySelectorAll('.mtab').forEach((b) => b.classList.toggle('on', b.dataset.k === k));
}             // the row the mouse is on (its tip shown)
document.addEventListener('mouseleave', () => {
  if (TIP_ROW !== null && api()) api().ov('tip', null);
  TIP_ROW = null;
});

/* The gear: this overlay's settings, in place of its content. */
function gearBtn() {
  const g = btn('ib gear' + (SETTINGS ? ' on' : ''), '', SETTINGS ? tr('Fermer') : tr('Réglages'),
    () => { SETTINGS = !SETTINGS; render(); });
  g.innerHTML = '<svg viewBox="0 0 16 16" width="17" height="17"><path fill="currentColor" d="M9.4 1l.3 1.8c.4.1.8.3 1.1.5l1.5-1 1.4 1.4-1 1.5c.2.3.4.7.5 1.1L15 6.6v2l-1.8.3c-.1.4-.3.8-.5 1.1l1 1.5-1.4 1.4-1.5-1c-.3.2-.7.4-1.1.5L9.4 15h-2l-.3-1.8c-.4-.1-.8-.3-1.1-.5l-1.5 1-1.4-1.4 1-1.5c-.2-.3-.4-.7-.5-1.1L1.8 9V7l1.8-.3c.1-.4.3-.8.5-1.1l-1-1.5 1.4-1.4 1.5 1c.3-.2.7-.4 1.1-.5L7.4 1h2zM8.4 5.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z"/></svg>';
  return g;
}

function settingsPanel() {
  const st = Object.assign({ opacity: 100, scale: 100, maxh: 0 }, OV.style || {});
  const p = el('div', 'body sets');
  // shown live while a slider moves (the size here at once, the opacity by
  // the window), saved once it is let go
  let due = null;
  const send = (save) => {
    OV.style = Object.assign({}, st);
    document.getElementById('ov').style.zoom = st.scale / 100;
    if (!api()) return;
    const go = () => { due = null; api().notify('ov_style', { id: OV_ID, opacity: st.opacity, scale: st.scale, maxh: st.maxh, save: save }); };
    if (save) { clearTimeout(due); go(); } else if (!due) due = setTimeout(go, 60);
  };
  [['opacity', tr('Opacité'), 30, 100, 5], ['scale', tr('Taille'), 90, 110, 5]].forEach(([k, t, lo, hi, step]) => {
    const row = el('label', 'set');
    const top = el('span', 'setl');
    top.appendChild(el('span', null, t));
    const v = el('b', null, st[k] + ' %');
    top.appendChild(v);
    row.appendChild(top);
    const r = el('input');
    r.type = 'range'; r.min = lo; r.max = hi; r.step = step; r.value = st[k];
    r.addEventListener('mousedown', (e) => e.stopPropagation());
    r.addEventListener('input', () => { st[k] = +r.value; v.textContent = st[k] + ' %'; send(false); });
    r.addEventListener('change', () => send(true));
    row.appendChild(r);
    p.appendChild(row);
  });
  const reset = btn('chip', tr('Par défaut'), null, () => {
    st.opacity = 100; st.scale = 100;
    send(true);
    render();
  });
  p.appendChild(reset);
  return p;
}

/* The tip: a player's damage or heals, and the skills that did them. */
function renderTip(box) {
  const t = OV.tip;
  if (!t) return;
  box.className = 'tipbox' + (t.heal ? ' heal' : '');
  const h = el('div', 'tiph' + (t.ck ? ' c-' + t.ck : ''));
  const icon = (window.__ICONS__ || {})[t.ck];
  if (icon) { const im = el('img'); im.src = icon; im.alt = ''; h.appendChild(im); }
  h.appendChild(el('b', null, t.name));
  h.appendChild(el('span', 'tipk', t.heal ? tr('Soins') : tr('Dégâts')));
  box.appendChild(h);
  const st = el('div', 'tips');
  (t.stats || []).forEach(([k, v]) => {
    const c = el('div', 'tipst');
    c.appendChild(el('span', null, k));
    c.appendChild(el('b', null, v));
    st.appendChild(c);
  });
  box.appendChild(st);
  if ((t.skills || []).length) {
    const sk = el('div', 'tipsk');
    t.skills.forEach((s) => {
      const r = el('div', 'tipr');
      r.style.setProperty('--f', (Math.max(0, Math.min(1, s.f || 0)) * 100) + '%');
      const ic = el('span', 'tic');
      if (s.img) { const im = el('img'); im.src = s.img; im.alt = ''; ic.appendChild(im); }
      r.appendChild(ic);
      r.appendChild(el('span', 'tn', s.t));
      r.appendChild(el('span', 'tv', s.v));
      r.appendChild(el('span', 'tp', s.pct));
      sk.appendChild(r);
    });
    box.appendChild(sk);
  } else {
    box.appendChild(el('div', 'empty', t.heal ? tr('Aucun soin pour l’instant.') : tr('Aucun dégât pour l’instant.')));
  }
}

/* The loot luck counters (the live tab's): each bonus, how far to its cap,
   whether its Soulwell offering is on. Something to look forward to. */
function renderLuck(box) {
  const l = OV.luck || {};
  const h = head(tr('Chance de butin'));
  h.appendChild(gearBtn());
  box.appendChild(h);
  if (SETTINGS) { box.appendChild(settingsPanel()); return; }
  const body = el('div', 'body');
  if (!l.rows) {
    body.appendChild(el('div', 'empty', tr('Lecture des compteurs…')));
    box.appendChild(body);
    return;
  }
  // one line each: its icon, then a bar to the cap with the name and the
  // bonus in it (lit while the offering is on, the details in its tooltip).
  // Without its offering a bonus is frozen: st.Player.rollLuck then rolls
  // the base chance alone and leaves the counter as it is
  l.rows.forEach((r) => {
    const row = el('div', 'lrow' + (r.on ? ' on' : ' frozen') + (r.full ? ' full' : ''));
    row.title = [
      r.grows ? (r.full ? tr('plafond atteint') : tr('max {cap}', { cap: r.cap })) : tr('Bonus fixe'),
      r.on ? (r.left != null ? tr('Offrande active · {left} min', { left: r.left }) : tr('Offrande active'))
        : tr('Bonus gelé : sans offrande, il ne compte pas et le compteur ne bouge pas')].join(' · ');
    const ic = el('span', 'lic');
    if (r.img) { const im = el('img'); im.src = r.img; im.alt = ''; ic.appendChild(im); }
    row.appendChild(ic);
    const bar = el('div', 'lbar');
    const fill = el('i');
    fill.style.width = (Math.max(0, Math.min(1, r.f || 0)) * 100) + '%';
    bar.appendChild(fill);
    bar.appendChild(el('span', 'nm', r.t));
    const v = el('b', 'v', '+' + r.bonus);
    if (!r.on) v.prepend(lockIcon());
    bar.appendChild(v);
    row.appendChild(bar);
    body.appendChild(row);
  });
  // no offering at all: a veil over the counters, what unfreezes them
  if (!l.rows.some((r) => r.on)) {
    body.classList.add('veiled');
    const veil = el('div', 'lveil');
    const msg = el('div', 'lvbox');
    const t = el('div', 'lvt');
    t.appendChild(lockIcon());
    t.appendChild(el('span', null, tr('Bonus gelés')));
    msg.appendChild(t);
    msg.appendChild(el('div', 'lvs', tr('Fais une offrande au {well} pour les activer.',
      { well: l.well || tr('Puits des âmes') })));
    veil.appendChild(msg);
    body.appendChild(veil);
  }
  box.appendChild(body);
}

/* A small padlock, drawn: the same in every font. */
function lockIcon() {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 12 14');
  s.setAttribute('class', 'lock');
  s.innerHTML = '<path d="M3 6V4a3 3 0 0 1 6 0v2" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    + '<rect x="1" y="6" width="10" height="7.5" rx="1.5" fill="currentColor"/>';
  return s;
}

function renderGoals(box) {
  const g = OV.goals || { rows: [] };
  const done = (g.rows || []).filter((r) => r.done).length;
  const h = head(tr('Objectifs'), [el('span', 'clock', (g.rows || []).length
    ? done + ' / ' + g.rows.length : '')]);
  h.appendChild(gearBtn());
  box.appendChild(h);
  if (SETTINGS) { box.appendChild(settingsPanel()); return; }
  const list = el('div', 'body glist');
  box.appendChild(list);
  renderList(list);
}

function renderList(into) {
  const list = into || document.querySelector('.glist');
  if (!list || !OV || OV_ID !== 'goals') return;
  const g = OV.goals || { rows: [] };
  list.textContent = '';
  (g.rows || []).forEach((r) => {
    const row = el('div', 'grow' + (r.done ? ' done' : ''));
    row.title = r.t + ' — ' + r.how;
    const pic = el('span', 'pic' + (r.rk ? ' r-' + r.rk : ''));
    if (r.img) { const im = el('img'); im.src = r.img; im.alt = ''; pic.appendChild(im); }
    else pic.textContent = r.rk ? '✦' : '?';
    row.appendChild(pic);
    const t = el('div', 'gt');
    t.appendChild(el('div', 'gn', r.t));
    const p = el('div', 'gp');
    const bar = el('span', 'gb');
    const fill = el('i');
    fill.style.width = Math.min(100, r.have / r.n * 100).toFixed(1) + '%';
    bar.appendChild(fill);
    p.appendChild(bar);
    p.appendChild(el('span', 'gc', r.done ? '✓ ' + r.have + ' / ' + r.n : r.have + ' / ' + r.n));
    t.appendChild(p);
    row.appendChild(t);
    row.appendChild(btn('x', '', tr('Retirer'), () => api() && api().notify('goal_del', { id: r.id })));
    list.appendChild(row);
  });
  // the last line adds one: the chooser in the middle of the game
  const add = btn('gadd', '', null, () => api() && api().ov('picker'));
  add.appendChild(el('span', 'gplus', '+'));
  add.appendChild(el('span', null, tr('Ajouter un objectif')));
  list.appendChild(add);
  if (g.rows && g.rows.some((r) => !r.rk) && g.banks === 0) {
    list.appendChild(el('div', 'note', tr('Banque pas encore lue : ouvre-la une fois en jeu.')));
  }
}

/* ---- the button over the game's menu (menu_host.EscButton) ------------ */
window.openEsc = function () { OV = OV || {}; render(); };
function renderEsc(box) {
  box.className = 'escbox';
  box.appendChild(btn('escbtn', tr('Farever Book - Overlay'), null,
    () => api() && api().notify('esc_open')));
}
if (OV_ID === 'esc') { OV = {}; setTimeout(render, 0); }

/* ---- the goals' chooser (menu_host.Picker) ------------------------------
   A kind of thing, then (gear, weapons, resources) a narrower one as the
   Build tab asks it, then the thing, then how many. */
let PK = null;
window.openPicker = function () {
  PK = { cat: null, catT: '', sub: null, subT: '', item: null, n: 1, q: '', data: null, seq: 0 };
  OV = OV || {};
  pkLoad();
};

function pkLoad() {
  const seq = ++PK.seq;
  PK.data = null;
  render();
  if (!api()) return;
  api().call('goal_catalog', { cat: PK.cat, sub: PK.sub }).then((d) => {
    if (!PK || seq !== PK.seq) return;
    PK.data = d || { step: 'items', items: [] };
    render();
  });
}

function pkBack() {
  if (PK.item) { PK.item = null; render(); return; }
  if (PK.sub) { PK.sub = null; PK.subT = ''; PK.q = ''; pkLoad(); return; }
  if (PK.cat) { PK.cat = null; PK.catT = ''; PK.q = ''; pkLoad(); return; }
  pkClose();
}

function pkClose() {
  PK = null;
  if (api()) api().ov('close');
}

function pkAdd() {
  if (!PK || !PK.item || !api()) return;
  api().notify('goal_add', { kind: PK.item.kind, ref: PK.item.ref, n: PK.item.one ? 1 : PK.n,
    rar: PK.item.rar || null, istat: PK.item.istat || null });
  pkClose();
}

function pkTile(o, onPick) {
  const b = btn('pktile' + (o.rk ? ' r-' + o.rk : ''), '', null, onPick);
  const pic = el('span', 'pkpic');
  // a gear slot: the character sheet's empty one, as in a build
  const ghost = PK && PK.cat === 'gear' && !PK.sub && (window.__SHEET__ || {})['slot_' + o.v];
  if (ghost) { const im = el('img', 'ghost'); im.src = ghost; im.alt = ''; pic.appendChild(im); pic.classList.add('slot'); }
  else if (o.img) { const im = el('img'); im.src = o.img; im.alt = ''; pic.appendChild(im); }
  else pic.textContent = '✦';
  b.appendChild(pic);
  b.appendChild(el('span', 'pkt', o.t));
  return b;
}

function renderPicker(box) {
  box.className = 'picker';
  if (!PK) return;
  const x = btn('pkx', '', tr('Fermer'), pkClose);
  box.appendChild(head(tr('Nouvel objectif'), [x]));
  const body = el('div', 'body pkbody');
  box.appendChild(body);
  // where the player is: each step a way back
  const crumbs = el('div', 'pkcrumbs');
  const crumb = (t, on, go) => {
    const c = on ? btn('pkc', t, null, go) : el('span', 'pkc cur', t);
    crumbs.appendChild(c);
  };
  crumb(tr('Type'), !!PK.cat, () => { PK.cat = PK.sub = PK.item = null; PK.catT = PK.subT = ''; PK.q = ''; pkLoad(); });
  if (PK.cat) {
    crumbs.appendChild(el('span', 'pksep', '›'));
    crumb(PK.catT, !!(PK.sub || PK.item), () => { PK.sub = PK.item = null; PK.subT = ''; PK.q = ''; pkLoad(); });
  }
  if (PK.sub) {
    crumbs.appendChild(el('span', 'pksep', '›'));
    crumb(PK.subT, !!PK.item, () => { PK.item = null; render(); });
  }
  body.appendChild(crumbs);
  if (PK.item) { body.appendChild(pkCount()); return; }
  const d = PK.data;
  if (!d) { body.appendChild(el('div', 'empty', tr('Chargement…'))); return; }
  if (d.step === 'sub' && (d.opts || []).length > PK_GRID_MAX) { pkKinds(body, d); return; }
  if (d.step === 'cat' || d.step === 'sub') {
    body.appendChild(el('div', 'pkq', d.step === 'cat' ? tr('Que veux-tu obtenir ?')
      : PK.cat === 'weapon' ? tr('Quel type d’arme ?') : tr('Lequel ?')));
    const grid = el('div', 'pkgrid' + (d.step === 'cat' ? ' big' : ''));
    (d.opts || []).forEach((o) => grid.appendChild(pkTile(o, () => {
      if (d.step === 'cat') { PK.cat = o.v; PK.catT = o.t; pkLoad(); return; }
      PK.sub = o.v; PK.subT = o.t; PK.q = ''; pkLoad();
    })));
    body.appendChild(grid);
    return;
  }
  if (d.rarities) { pkGear(body, d); return; }
  // the things: a filter, then the list
  const q = el('input');
  q.type = 'text';
  q.className = 'pksearch';
  q.placeholder = tr('Filtrer…');
  q.value = PK.q;
  body.appendChild(q);
  const list = el('div', 'pklist');
  body.appendChild(list);
  const draw = () => {
    const f = fold(PK.q);
    list.textContent = '';
    const items = (d.items || []).filter((it) => !f || fold(it.t).indexOf(f) >= 0);
    if (!items.length) list.appendChild(el('div', 'empty', tr('Aucun résultat.')));
    items.forEach((it) => {
      const r = el('div', 'pkrow' + (it.rk ? ' r-' + it.rk : ''));
      const pic = el('span', 'pkpic');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; pic.appendChild(im); }
      r.appendChild(pic);
      const t = el('div', 'pkrt');
      t.appendChild(el('b', null, it.t));
      if (it.sub) t.appendChild(el('small', null, it.sub));
      r.appendChild(t);
      r.addEventListener('click', () => {
        PK.item = { kind: 'item', ref: it.id, t: it.t, img: it.img, sub: it.sub, rk: it.rk, one: it.one };
        PK.n = 1;
        render();
      });
      list.appendChild(r);
    });
  };
  q.addEventListener('input', () => { PK.q = q.value; draw(); sizeSoon(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); pkBack(); } });
  draw();
  fadeEdges(list);
  setTimeout(() => q.focus(), 0);
}

/* A long choice of kinds (the resources', the weapons'): a list to scroll,
   line by line, with a search. */
const PK_GRID_MAX = 12;
function pkKinds(body, d) {
  body.appendChild(el('div', 'pkq', PK.cat === 'weapon' ? tr('Quel type d’arme ?')
    : PK.cat === 'resource' ? tr('Quel type de ressource ?') : tr('Lequel ?')));
  const q = el('input');
  q.type = 'text';
  q.className = 'pksearch';
  q.placeholder = tr('Rechercher…');
  q.value = PK.q;
  body.appendChild(q);
  const list = el('div', 'pklist');
  body.appendChild(list);
  const draw = () => {
    const f = fold(PK.q);
    list.textContent = '';
    const opts = (d.opts || []).filter((o) => !f || fold(o.t).indexOf(f) >= 0);
    if (!opts.length) list.appendChild(el('div', 'empty', tr('Aucun résultat.')));
    opts.forEach((o) => {
      const r = el('div', 'pkrow');
      const pic = el('span', 'pkpic');
      if (o.img) { const im = el('img'); im.src = o.img; im.alt = ''; pic.appendChild(im); }
      r.appendChild(pic);
      const t = el('div', 'pkrt');
      t.appendChild(el('b', null, o.t));
      if (o.n != null) t.appendChild(el('small', null, tr(o.n > 1 ? '{n} objets' : '{n} objet', { n: o.n })));
      r.appendChild(t);
      r.addEventListener('click', () => { PK.sub = o.v; PK.subT = o.t; PK.q = ''; pkLoad(); });
      list.appendChild(r);
    });
  };
  q.addEventListener('input', () => { PK.q = q.value; draw(); sizeSoon(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); pkBack(); } });
  draw();
  fadeEdges(list);
  setTimeout(() => q.focus(), 0);
}

/* The gear: the Build tab's filters (rarity, attributes, stats, family, a
   search), its pieces, each one's card under the mouse. */
const PKF = { rar: '', stats: new Set(), fac: '' };
function pkGear(body, d) {
  const filters = el('div', 'pkfilters');
  const row = (label, items) => {
    const r = el('div', 'pkfrow');
    r.appendChild(el('span', 'pkfl', label));
    const cs = el('div', 'pkchips');
    items.forEach((c) => cs.appendChild(c));
    r.appendChild(cs);
    filters.appendChild(r);
  };
  const chip = (t, on, go, cls) => btn('pkchip' + (cls ? ' ' + cls : '') + (on ? ' on' : ''), t, null, go);
  if (PKF.rar && !d.rarities.some((r) => r.v === PKF.rar)) PKF.rar = '';
  row(tr('Rareté'), [{ v: '', t: tr('Toutes') }].concat(d.rarities).map((r) =>
    chip(r.t, PKF.rar === r.v, () => { PKF.rar = r.v; render(); }, r.v ? 'r-' + r.v.toLowerCase() : '')));
  const MAIN = ['Strength', 'Intellect', 'Faith', 'Dexterity'];
  const SECOND = ['CritChanceRating', 'FervorRating', 'ArmorPenetrationRating', 'SpellPenetrationRating'];
  const statChip = (s) => {
    const c = chip('', PKF.stats.has(s.k), () => {
      if (PKF.stats.has(s.k)) PKF.stats.delete(s.k); else PKF.stats.add(s.k);
      render();
    });
    const art = (window.__SHEET__ || {})['stat_' + (s.k === 'Intellect' ? 'Intelligence' : s.k)];
    if (art) { const im = el('img', 'pkatb'); im.src = art; im.alt = ''; c.appendChild(im); }
    c.appendChild(document.createTextNode(s.t));
    return c;
  };
  const pick = (keys) => keys.map((k) => d.stats.find((s) => s.k === k)).filter(Boolean);
  [...PKF.stats].forEach((k) => { if (!d.stats.some((s) => s.k === k)) PKF.stats.delete(k); });
  if (pick(MAIN).length) row(tr('Attributs'), pick(MAIN).map(statChip));
  if (pick(SECOND).length) row(tr('Statistiques'), pick(SECOND).map(statChip));
  if (PKF.fac && !d.factions.includes(PKF.fac)) PKF.fac = '';
  if (d.factions.length) {
    const sel = el('select', 'pkfac');
    [['', tr('Toutes les familles')]].concat(d.factions.map((f) => [f, f])).forEach(([v, t]) => {
      const o = el('option', null, t); o.value = v; if (v === PKF.fac) o.selected = true; sel.appendChild(o);
    });
    sel.addEventListener('change', () => { PKF.fac = sel.value; render(); });
    filters.appendChild(sel);
  }
  body.appendChild(filters);
  const q = el('input');
  q.type = 'text';
  q.className = 'pksearch';
  q.placeholder = tr('Rechercher…');
  q.value = PK.q;
  body.appendChild(q);
  const count = el('div', 'pkcountn');
  body.appendChild(count);
  const list = el('div', 'pklist');
  body.appendChild(list);
  const draw = () => {
    const f = fold(PK.q);
    const shown = d.items.filter((it) => (!f || fold(it.t).indexOf(f) >= 0 || fold(it.sub).indexOf(f) >= 0)
      && (!PKF.rar || it.rar === PKF.rar) && (!PKF.fac || it.fac === PKF.fac)
      && [...PKF.stats].every((k) => (it.stats || []).includes(k)));
    count.textContent = tr('{n} / {all} pièces', { n: shown.length, all: d.items.length });
    list.textContent = '';
    if (!shown.length) list.appendChild(el('div', 'empty', tr('Aucune pièce ne correspond.')));
    shown.forEach((it) => {
      const r = el('div', 'pkrow r-' + (it.rk || 'common'));
      const pic = el('span', 'pkpic');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; pic.appendChild(im); }
      r.appendChild(pic);
      const t = el('div', 'pkrt');
      t.appendChild(el('b', null, it.t));
      t.appendChild(el('small', null, it.sub));
      r.appendChild(t);
      r.addEventListener('click', () => {
        pkCard(null);
        PK.item = { kind: 'item', ref: it.id, t: it.t + ' (' + it.tip.rar + ')', img: it.img, sub: it.sub,
          rk: it.rk, rar: it.rar, istats: it.istats, istat: '' };
        PK.n = 1;
        render();
      });
      r.addEventListener('mousemove', (e) => pkCard(it.tip, e));
      r.addEventListener('mouseleave', () => pkCard(null));
      list.appendChild(r);
    });
  };
  q.addEventListener('input', () => { PK.q = q.value; draw(); sizeSoon(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); pkBack(); } });
  draw();
  fadeEdges(list);
  setTimeout(() => q.focus(), 0);
}

/* A scrolling list fades out where it is cut: at its foot while more is
   below, at its head once scrolled. */
function fadeEdges(list) {
  const set = () => {
    list.classList.toggle('fade-top', list.scrollTop > 2);
    list.classList.toggle('fade-bot', list.scrollTop + list.clientHeight < list.scrollHeight - 2);
  };
  list.addEventListener('scroll', set);
  // its content filtered, its height changed: no fade without a scroll
  new MutationObserver(() => requestAnimationFrame(set)).observe(list, { childList: true });
  new ResizeObserver(set).observe(list);
  requestAnimationFrame(set);
}

/* A piece's card beside the mouse, kept inside the window. */
function pkCard(tip, e) {
  let c = document.getElementById('pkcard');
  if (!tip) { if (c) c.remove(); return; }
  if (!c || c.dataset.name !== tip.name + tip.rar) {
    if (c) c.remove();
    c = el('div', 'pkcard r-' + (tip.rk || 'common'));
    c.id = 'pkcard';
    c.dataset.name = tip.name + tip.rar;
    const h = el('div', 'pkch');
    if (tip.img) { const im = el('img'); im.src = tip.img; im.alt = ''; h.appendChild(im); }
    const t = el('div');
    t.appendChild(el('b', 'pkcn', tip.name));
    t.appendChild(el('span', 'pkct', [tip.type, tip.rar].filter(Boolean).join(' · ')));
    h.appendChild(t);
    c.appendChild(h);
    c.appendChild(el('div', 'pkcl', tr('niv. {n}', { n: tip.lvl }) + (tip.il ? ' · ' + tr('niveau d’objet {n}', { n: tip.il }) : '')));
    (tip.stats || []).forEach((s) => {
      const r = el('div', 'pkcs');
      r.appendChild(el('span', null, s.t));
      r.appendChild(el('b', null, s.v));
      c.appendChild(r);
    });
    document.body.appendChild(c);
  }
  const z = parseFloat(document.getElementById('ov').style.zoom) || 1;
  const w = c.offsetWidth, h = c.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  let x = e.clientX + 16, y = e.clientY + 16;
  if (x + w > vw - 10) x = e.clientX - 16 - w;
  if (y + h > vh - 10) y = Math.max(10, vh - 10 - h);
  c.style.left = Math.max(10, x) + 'px';
  c.style.top = y + 'px';
}

/* The last step: the thing chosen, how many. */
function pkCount() {
  const it = PK.item;
  const box = el('div', 'pkcount');
  const top = el('div', 'pkrow sel' + (it.rk ? ' r-' + it.rk : ''));
  const pic = el('span', 'pkpic');
  if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; pic.appendChild(im); }
  else pic.textContent = '✦';
  top.appendChild(pic);
  const t = el('div', 'pkrt');
  t.appendChild(el('b', null, it.t));
  if (it.sub) t.appendChild(el('small', null, it.sub));
  top.appendChild(t);
  box.appendChild(top);
  // an infusable piece: the bonus it rolled as it dropped, if one is wanted
  if ((it.istats || []).length) {
    box.appendChild(el('div', 'pkq', tr('Un bonus d’imprégnation précis ?')));
    const chips = el('div', 'pkchips');
    [{ v: '', t: tr('Peu importe') }].concat(it.istats).forEach((s) => {
      chips.appendChild(btn('pkchip' + ((it.istat || '') === s.v ? ' on' : ''), s.t, null, () => {
        it.istat = s.v;
        render();
      }));
    });
    box.appendChild(chips);
  }
  if (!it.one) {
    box.appendChild(el('div', 'pkq', tr('Combien ?')));
    const row = el('div', 'pkqty');
    const n = el('input');
    n.type = 'number'; n.min = 1; n.max = 99999; n.value = PK.n;
    const set = (v) => { PK.n = Math.max(1, Math.min(99999, v || 1)); n.value = PK.n; };
    row.appendChild(btn('pkstep', '−', null, () => set(PK.n - 1)));
    n.addEventListener('input', () => { PK.n = Math.max(1, parseInt(n.value, 10) || 1); });
    n.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') pkAdd();
      if (e.key === 'Escape') { e.stopPropagation(); pkBack(); }
    });
    row.appendChild(n);
    row.appendChild(btn('pkstep', '+', null, () => set(PK.n + 1)));
    box.appendChild(row);
    setTimeout(() => { n.focus(); n.select(); }, 0);
  } else {
    box.appendChild(el('div', 'note', tr('Atteint dès qu’elle entre dans ta collection.')));
  }
  box.appendChild(btn('go pkgo', tr('Ajouter l’objectif'), null, pkAdd));
  return box;
}

/* Lower case, accents off: "eclat" finds "Éclat". */
function fold(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
}

document.addEventListener('keydown', (e) => {
  if (OV_ID === 'picker' && PK && e.key === 'Escape') pkBack();
});
