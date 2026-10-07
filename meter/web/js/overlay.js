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
const ADD = { open: false, q: '', res: [], pick: null, n: 1, seq: 0 };

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
  if (OV_ID === 'tip') renderTip(box);
  else if (OV_ID === 'meter') renderMeter(box);
  else if (OV_ID === 'luck') renderLuck(box);
  else renderGoals(box);
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
    bar.appendChild(el('div', 'mclock'));
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
  box.appendChild(h);
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
  h.appendChild(btn('ib', ADD.open ? '−' : '+', ADD.open ? tr('Fermer') : tr('Ajouter un objectif'),
    () => { ADD.open = !ADD.open; if (!ADD.open) leaveTyping(); render(); }));
  box.appendChild(h);
  const list = el('div', 'body glist');
  box.appendChild(list);
  renderList(list);
  if (ADD.open) box.appendChild(addForm());
}

function renderList(into) {
  const list = into || document.querySelector('.glist');
  if (!list || !OV || OV_ID !== 'goals') return;
  const g = OV.goals || { rows: [] };
  list.textContent = '';
  if (!(g.rows || []).length) {
    list.appendChild(el('div', 'empty', ADD.open ? tr('Choisis un objet ou une rareté ci-dessous.')
      : tr('Aucun objectif. « + » pour en ajouter un.')));
  }
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
    row.appendChild(btn('ib x', '×', tr('Retirer'), () => api() && api().notify('goal_del', { id: r.id })));
    list.appendChild(row);
  });
  if (g.rows && g.rows.some((r) => !r.rk) && g.banks === 0) {
    list.appendChild(el('div', 'note', tr('Banque pas encore lue : ouvre-la une fois en jeu.')));
  }
}

/* ---- adding: search an item, or pick a rarity, then how many ----------- */
function enterTyping() { if (api()) api().ov('focus', true); }
function leaveTyping() { if (api()) api().ov('focus', false); }

function addForm() {
  const f = el('div', 'add');
  if (ADD.pick) {
    const row = el('div', 'qty');
    row.appendChild(el('span', 'pick', ADD.pick.t));
    const n = el('input');
    n.type = 'number'; n.min = 1; n.value = ADD.n;
    n.addEventListener('focus', enterTyping);
    n.addEventListener('input', () => { ADD.n = Math.max(1, parseInt(n.value, 10) || 1); });
    n.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') confirmAdd();
      if (e.key === 'Escape') { ADD.pick = null; render(); }
    });
    row.appendChild(n);
    row.appendChild(btn('go', tr('Ajouter'), null, confirmAdd));
    f.appendChild(row);
    f.appendChild(btn('chip', tr('‹ Changer'), null, () => { ADD.pick = null; render(); }));
    setTimeout(() => { n.focus(); n.select(); }, 0);
    return f;
  }
  const q = el('input');
  q.type = 'text';
  q.placeholder = tr('Rechercher un objet (minerai, plante…)');
  q.value = ADD.q;
  q.addEventListener('focus', enterTyping);
  q.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { ADD.open = false; leaveTyping(); render(); }
    if (e.key === 'Enter' && ADD.res.length) choose({ kind: 'item', ref: ADD.res[0].id, t: ADD.res[0].t });
  });
  q.addEventListener('input', () => {
    ADD.q = q.value;
    const seq = ++ADD.seq;
    if (!api() || ADD.q.trim().length < 2) { ADD.res = []; drawRes(); return; }
    api().call('goal_search', { q: ADD.q }).then((r) => {
      if (seq !== ADD.seq) return;
      ADD.res = r || [];
      drawRes();
    });
  });
  f.appendChild(q);
  const res = el('div', 'res');
  f.appendChild(res);
  const drawRes = () => {
    res.textContent = '';
    ADD.res.forEach((it) => {
      const r = el('div', 'ri');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; r.appendChild(im); }
      else r.appendChild(el('span', 'ph'));
      const t = el('span', 'rt', it.t);
      if (it.type) { t.appendChild(document.createElement('br')); t.appendChild(el('small', null, it.type)); }
      r.appendChild(t);
      r.addEventListener('mousedown', (e) => e.preventDefault());
      r.addEventListener('click', () => choose({ kind: 'item', ref: it.id, t: it.t }));
      res.appendChild(r);
    });
  };
  drawRes();
  const quick = el('div', 'quick');
  ((OV.goals || {}).rarities || []).forEach((r) => {
    const label = tr('Objet {rarity}', { rarity: r.t.toLowerCase() });
    quick.appendChild(btn('chip', label, tr('Compte les objets de cette rareté ramassés à partir de maintenant'),
      () => choose({ kind: 'rarity', ref: r.v, t: label })));
  });
  f.appendChild(quick);
  setTimeout(() => q.focus(), 0);
  return f;
}

function choose(pick) {
  ADD.pick = pick;
  ADD.n = 1;
  render();
}

function confirmAdd() {
  if (!ADD.pick || !api()) return;
  api().notify('goal_add', { kind: ADD.pick.kind, ref: ADD.pick.ref, n: ADD.n });
  ADD.pick = null;
  ADD.q = '';
  ADD.res = [];
  ADD.open = false;
  leaveTyping();
  render();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && ADD.open && document.activeElement.tagName !== 'INPUT') {
    ADD.open = false; leaveTyping(); render();
  }
});
