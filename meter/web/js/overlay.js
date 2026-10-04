/* An overlay over the game (menu_host.Overlay): "meter" or "goals", set by
   window.__OVERLAY__. State arrives through applyOverlay(json). */
const OV_ID = window.__OVERLAY__;
let OV = null;                  // the last state
let OV_SMALL = false;           // collapsed to the header
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

window.applyOverlay = function (json) {
  try { OV = JSON.parse(json); } catch (e) { return; }
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
  const r = box.getBoundingClientRect();
  const s = Math.ceil(r.width) + 'x' + Math.ceil(r.height);
  if (s === LAST_SIZE) return;
  LAST_SIZE = s;
  api().ov('size', [Math.ceil(r.width), Math.ceil(r.height)]);
}
new ResizeObserver(reportSize).observe(document.getElementById('ov'));

/* ---- drawing ------------------------------------------------------------ */
function head(title, extra) {
  const h = el('div', 'head');
  h.addEventListener('mousedown', startDrag);
  h.appendChild(el('span', 't', title));
  (extra || []).forEach((x) => h.appendChild(x));
  h.appendChild(el('span', 'sp'));
  return h;
}

function render() {
  const box = document.getElementById('ov');
  box.textContent = '';
  if (!OV) return;
  if (OV_ID === 'meter') renderMeter(box);
  else renderGoals(box);
}

function renderMeter(box) {
  const m = OV.meter || { rows: [] };
  const heal = m.tab === 'heal';
  box.className = heal ? 'heal' : '';
  const tabs = el('div', 'tabs');
  if (m.heals) {
    [['dmg', 'Dégâts'], ['heal', 'Soins']].forEach(([k, t]) => {
      tabs.appendChild(btn('tab' + (m.tab === k ? ' on' : ''), t, null,
        () => api() && api().notify('ov_tab', { tab: k })));
    });
  }
  const h = head('Groupe', m.heals ? [tabs] : []);
  if (m.time) h.appendChild(el('span', 'clock' + (m.fight ? ' hot' : ''), m.time));
  h.appendChild(btn('ib', OV_SMALL ? '▸' : '▾', OV_SMALL ? 'Déplier' : 'Replier',
    () => { OV_SMALL = !OV_SMALL; render(); }));
  box.appendChild(h);
  if (OV_SMALL) return;
  const body = el('div', 'body');
  if (!(m.rows || []).length) {
    body.appendChild(el('div', 'empty', 'En attente d’un combat…'));
  } else {
    if (m.total) {
      const t = el('div', 'total');
      t.appendChild(el('span', null, heal ? 'Soins du groupe' : 'Dégâts du groupe'));
      t.appendChild(el('b', null, m.total));
      body.appendChild(t);
    }
    m.rows.forEach((r) => {
      const row = el('div', 'mrow' + (r.me ? ' me' : ''));
      const bar = el('i', 'bar');
      bar.style.width = (r.f * 100).toFixed(1) + '%';
      row.appendChild(bar);
      const icon = (window.__ICONS__ || {})[r.ck];
      if (icon) { const im = el('img'); im.src = icon; im.alt = ''; row.appendChild(im); }
      row.appendChild(el('span', 'nm', r.n));
      row.appendChild(el('span', 'v', r.v));
      row.appendChild(el('span', 'ps', r.ps ? r.ps + '/s' : ''));
      body.appendChild(row);
    });
  }
  box.appendChild(body);
}

function renderGoals(box) {
  const g = OV.goals || { rows: [] };
  const done = (g.rows || []).filter((r) => r.done).length;
  const h = head('Objectifs', [el('span', 'clock', (g.rows || []).length
    ? done + ' / ' + g.rows.length : '')]);
  h.appendChild(btn('ib', ADD.open ? '−' : '+', ADD.open ? 'Fermer' : 'Ajouter un objectif',
    () => { ADD.open = !ADD.open; OV_SMALL = false; if (!ADD.open) leaveTyping(); render(); }));
  h.appendChild(btn('ib', OV_SMALL ? '▸' : '▾', OV_SMALL ? 'Déplier' : 'Replier',
    () => { OV_SMALL = !OV_SMALL; render(); }));
  box.appendChild(h);
  if (OV_SMALL) return;
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
    list.appendChild(el('div', 'empty', ADD.open ? 'Choisis un objet ou une rareté ci-dessous.'
      : 'Aucun objectif. « + » pour en ajouter un.'));
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
    row.appendChild(btn('ib x', '×', 'Retirer', () => api() && api().notify('goal_del', { id: r.id })));
    list.appendChild(row);
  });
  if (g.rows && g.rows.some((r) => !r.rk) && g.banks === 0) {
    list.appendChild(el('div', 'note', 'Banque pas encore lue : ouvre-la une fois en jeu.'));
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
    row.appendChild(btn('go', 'Ajouter', null, confirmAdd));
    f.appendChild(row);
    f.appendChild(btn('chip', '‹ Changer', null, () => { ADD.pick = null; render(); }));
    setTimeout(() => { n.focus(); n.select(); }, 0);
    return f;
  }
  const q = el('input');
  q.type = 'text';
  q.placeholder = 'Rechercher un objet (minerai, plante…)';
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
    quick.appendChild(btn('chip', 'Objet ' + r.t.toLowerCase(), 'Compte les objets de cette rareté ramassés à partir de maintenant',
      () => choose({ kind: 'rarity', ref: r.v, t: 'Objet ' + r.t.toLowerCase() })));
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
