/* The Farever France window's renderer.
 *
 * This file knows how to draw a NODE, not a page. The meter sends a
 * declarative spec — a flat list of nodes for the current page — a few times
 * a second, and everything here turns it into DOM. All the logic (what the
 * numbers are, what a button does) lives in the meter.
 *
 * Generic nodes:
 *   {k:"section", t}   {k:"note", t, warn?}   {k:"gap"}
 *   {k:"prose", t}     {k:"bullets", items}   {k:"code", t}
 *   {k:"button", id, t, on?, tone?, p?}
 *   {k:"field", t, c:<control>}   controls: select | slider | text | label
 *   {k:"search", id, v, count?}
 *   {k:"list", id, h?, grow?, rows:[{t?, name?, cls?, meta?, btns?:[{id,t,p?,off?}]}], empty?}
 *   {k:"sub", t}   a small heading inside a section
 * Live and report nodes:
 *   {k:"toolbar", btns}          {k:"cards", items}
 *   {k:"meter", title, heal, rows, empty}
 *   {k:"detail", name, cls, stats, dmg, heal, elements, empty}
 *   {k:"events", rows}           {k:"report", title, when, phases}
 */
'use strict';

const $ = (sel) => document.querySelector(sel);

/* The class icons, inlined by the host as data URIs: {"warrior": "data:..."}. */
/*ICONS*/
const CLASS_ICONS = window.__ICONS__ || {};
const CLASS_NAMES = { warrior: 'Guerrier', mage: 'Mage', priest: 'Prêtre',
                      rogue: 'Voleur' };

/* A player's class: its icon when there is one, the abbreviation otherwise. */
function classEl(tag, key, cls) {
  if (key && CLASS_ICONS[key]) {
    const im = el('img', 'clsicon' + (cls ? ' ' + cls : ''));
    im.src = CLASS_ICONS[key];
    im.alt = im.title = CLASS_NAMES[key] || tag || '';
    return im;
  }
  return el('span', cls || 'cls', tag || '');
}

let STATE = {};
let NODES = new Map();
let READY = false;

/* ---- talking to the meter ---------------------------------------------- */
function notify(method, params) {
  if (!window.pywebview) return;
  window.pywebview.api.notify(method, params || {});
}

/* ---- helpers ------------------------------------------------------------ */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

/* Inline **bold** and `code`, as ELEMENTS — never innerHTML: the same path
   renders player names, which must never become markup. */
function inline(parent, text) {
  String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach((p) => {
    if (!p) return;
    if (p.startsWith('**') && p.endsWith('**') && p.length > 4) {
      parent.appendChild(el('strong', null, p.slice(2, -2)));
    } else if (p.startsWith('`') && p.endsWith('`') && p.length > 2) {
      parent.appendChild(el('code', null, p.slice(1, -1)));
    } else {
      parent.appendChild(document.createTextNode(p));
    }
  });
  return parent;
}

function btnClass(node) {
  let c = 'btn';
  if (node.on) c += ' on';
  if (node.tone) c += ' ' + node.tone;
  return c;
}

function button(node) {
  const b = el('button', btnClass(node), node.t);
  if (node.tone !== 'disabled') {
    b.addEventListener('click', () => notify(node.id, node.p || {}));
  }
  return b;
}

/* A bar: `f` of the track filled, optionally with a second segment `sf`
   drawn from the left over it (self-healing). */
function bar(kind, f, sf) {
  const b = el('div', 'bar ' + kind);
  const all = el('i', 'all');
  all.style.width = (Math.max(0, Math.min(1, f || 0)) * 100) + '%';
  b.appendChild(all);
  if (sf) {
    const s = el('i', 'self');
    s.style.width = (Math.max(0, Math.min(1, sf)) * 100) + '%';
    b.appendChild(s);
  }
  return b;
}

let TOAST_JOB = null;
function showToast(text) {
  let t = $('#toast');
  if (!t) {
    t = el('div', null);
    t.id = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.classList.add('on');
  if (TOAST_JOB) clearTimeout(TOAST_JOB);
  TOAST_JOB = setTimeout(() => t.classList.remove('on'), 2200);
}

function setZoom(pct) {
  const z = Math.max(50, Math.min(200, Number(pct) || 100));
  document.documentElement.style.zoom = (z / 100).toString();
  widthClasses();
  headerWrap();
}

/* The page's real width: the window's, divided by the zoom. A media query
   only sees the window — at 130 % a 720 px window lays out 554 px of page —
   so the layout's breakpoints are classes on <html> instead (lt-1100: the
   page is 1100 px wide or less), set from that real width. */
const WIDTH_STEPS = [1250, 1100, 1000, 900, 760, 620];
function widthClasses() {
  const root = document.documentElement;
  const w = window.innerWidth / (parseFloat(root.style.zoom) || 1);
  WIDTH_STEPS.forEach((s) => root.classList.toggle('lt-' + s, w <= s));
}
window.addEventListener('resize', widthClasses);
widthClasses();

/* The header on two lines (its right-hand group gone under the name: a
   narrow window) is centred, both lines. Measured, not guessed from a width:
   the group's own width changes (Jouer, En jeu, a long server name). */
let HEADER_WRAP = 0;
function headerWrap() {
  cancelAnimationFrame(HEADER_WRAP);
  HEADER_WRAP = requestAnimationFrame(() => {
    const top = $('#top'), right = $('#topright'), brand = $('#top .brand');
    if (!top || !right || !brand) return;
    top.classList.remove('wrapped');
    // on two lines: the group starts below the name's bottom
    top.classList.toggle('wrapped',
      right.offsetTop >= brand.offsetTop + brand.offsetHeight - 2);
  });
}
window.addEventListener('resize', headerWrap);

/* A button that floats at the page's bottom right once it is scrolled down,
   and takes it back to the top. */
function initToTop() {
  const page = $('#page');
  if (!page || document.getElementById('totop')) return;
  const b = el('button', 'totop');
  b.id = 'totop';
  b.type = 'button';
  b.title = 'Revenir en haut';
  b.setAttribute('aria-label', 'Revenir en haut');
  b.addEventListener('click', () => page.scrollTo({ top: 0, behavior: 'smooth' }));
  page.parentNode.appendChild(b);
  // the header's right-hand group changes width with the game's state
  // the header's right-hand group changes width with the game's state, the
  // name's height once the logo has loaded
  if (window.ResizeObserver && $('#topright')) {
    const ro = new ResizeObserver(headerWrap);
    ro.observe($('#topright'));
    if ($('#top .brand')) ro.observe($('#top .brand'));
  }
  headerWrap();
  const sync = () => b.classList.toggle('on', page.scrollTop > 400);
  page.addEventListener('scroll', sync, { passive: true });
  sync();
}

/* ---- controls ----------------------------------------------------------- */
function buildControl(c) {
  if (c.k === 'select') {
    const s = el('select');
    (c.o || []).forEach((o) => {
      const opt = el('option', null, typeof o === 'string' ? o : o.t);
      opt.value = typeof o === 'string' ? o : o.v;
      s.appendChild(opt);
    });
    s.value = c.v;
    s.addEventListener('change', () => notify(c.id, { value: s.value }));
    return s;
  }
  if (c.k === 'slider') {
    const wrap = el('div', 'ctl');
    const r = el('input');
    r.type = 'range';
    r.min = c.min; r.max = c.max; r.step = c.step || 1; r.value = c.v;
    const out = el('span', 'slider-val', c.v + (c.unit || ''));
    r.addEventListener('input', () => {
      out.textContent = r.value + (c.unit || '');
      if (c.live) notify(c.id, { value: Number(r.value) });
    });
    r.addEventListener('change', () => {
      if (!c.live) notify(c.id, { value: Number(r.value) });
    });
    wrap.appendChild(r);
    wrap.appendChild(out);
    return wrap;
  }
  if (c.k === 'text') {
    const i = el('input');
    i.type = 'text';
    i.value = c.v || '';
    if (c.ph) i.placeholder = c.ph;
    i.addEventListener('input', () => notify(c.id, { value: i.value }));
    return i;
  }
  return el('span', 'meta', c.t || '');
}

/* ---- generic nodes ------------------------------------------------------ */
function buildRow(r) {
  if (r.portrait !== undefined) return buildPortraitRow(r);
  const row = el('div', 'row' + (r.check ? ' checkable' + (r.check.on ? ' ticked' : '') : ''));
  if (r.check) {
    const cb = el('input', 'rowcheck');
    cb.type = 'checkbox';
    cb.checked = !!r.check.on;
    cb.addEventListener('change', () => notify(r.check.id, r.check.p || {}));
    row.appendChild(cb);
  }
  if (r.name !== undefined) row.appendChild(el('span', 'name', r.name));
  if (r.cls !== undefined) row.appendChild(el('span', 'cls', r.cls));
  if (r.t !== undefined) row.appendChild(el('span', 'name', r.t));
  if (r.meta !== undefined) row.appendChild(el('span', 'meta', r.meta));
  (r.btns || []).forEach((b) => {
    const btn = el('button', 'rowbtn', b.t);
    if (b.off) btn.disabled = true;
    else btn.addEventListener('click', () => notify(b.id, b.p || {}));
    row.appendChild(btn);
  });
  return row;
}

/* A dungeon's loading screen behind its card, once the pictures are in. */
function applyBackdrop(node) {
  const src = (window.__DBG__ || {})[node.dataset.bg];
  if (src) node.style.setProperty('--bg', 'url("' + src + '")');
}

/* A row of item icons, each with its rarity and a tooltip. */
function lootStrip(icons) {
  const s = el('div', 'lootstrip');
  icons.forEach((ic) => {
    const box = el('span', 'lic' + (ic.rk ? ' r-' + ic.rk : ''));
    box.title = ic.tip || '';
    if (ic.img) {
      const im = document.createElement('img');
      im.src = ic.img;
      im.alt = '';
      box.appendChild(im);
    }
    if (ic.n) box.appendChild(el('b', null, String(ic.n)));
    s.appendChild(box);
  });
  return s;
}

/* The loot by difficulty: a line each, led by the game's skull. */
function lootTiers(list) {
  const tiers = el('div', 'loottiers');
  list.forEach((t) => {
    const line = el('div', 'ltier d' + t.d);
    const lab = el('span', 'ltl');
    const art = (window.__SHEET__ || {})['dungeon_diff_' + t.d];
    if (art) {
      const im = document.createElement('img');
      im.src = art;
      im.alt = '';
      lab.appendChild(im);
    }
    const nm = el('span', 'ltn', t.t);
    if (t.sub) nm.appendChild(el('small', null, t.sub));
    lab.appendChild(nm);
    line.appendChild(lab);
    line.appendChild(lootStrip(t.icons));
    tiers.appendChild(line);
  });
  return tiers;
}

/* The dungeon list: a card each, the whole card a link to its runs — on
   top the boss and what we have done there, under it what it can give. */
function buildDungeonCards(n) {
  const grid = el('div', 'dcards');
  (n.cards || []).forEach((c) => {
    const card = el('div', 'dcard' + (c.runs ? ' done' : '') + (c.bg ? ' hasbg' : ''));
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    const open = () => notify('open_dungeon_kind', { kind: c.kind });
    card.addEventListener('click', open);
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    if (c.bg) {
      card.dataset.bg = c.bg;
      applyBackdrop(card);
    }
    const head = el('div', 'dchead');
    const pic = el('div', 'portrait');
    const src = (window.__PORTRAITS__ || {})[c.portrait];
    if (src) {
      const im = document.createElement('img');
      im.src = src;
      im.alt = '';
      pic.appendChild(im);
    }
    head.appendChild(pic);
    const info = el('div', 'dcinfo');
    info.appendChild(el('div', 'dcname', c.t));
    if (c.boss) info.appendChild(el('div', 'dcboss', 'Boss : ' + c.boss));
    const done = el('div', 'dcdone');
    if (c.runs) {
      done.appendChild(el('span', 'dcstat', fmtN(c.runs) + ' run' + (c.runs > 1 ? 's' : '')));
      done.appendChild(el('span', 'dcstat' + (c.wins ? ' win' : ''),
        fmtN(c.wins) + ' victoire' + (c.wins > 1 ? 's' : '')));
    } else {
      done.appendChild(el('span', 'dcstat none', 'Pas encore fait'));
    }
    info.appendChild(done);
    if ((c.recs || []).length) {
      const recs = el('div', 'dcrecs');
      c.recs.forEach((r) => {
        const chip = el('span', 'dcrec d' + r.d);
        chip.title = 'Record en ' + r.t;
        const art = (window.__SHEET__ || {})['dungeon_diff_' + r.d];
        if (art) {
          const im = document.createElement('img');
          im.src = art;
          im.alt = '';
          chip.appendChild(im);
        }
        chip.appendChild(el('b', null, r.v));
        recs.appendChild(chip);
      });
      info.appendChild(recs);
    }
    head.appendChild(info);
    card.appendChild(head);
    if ((c.tiers || []).length) {
      const loot = el('div', 'dcloot');
      loot.appendChild(el('div', 'dclabel', 'Butin possible'));
      loot.appendChild(lootTiers(c.tiers));
      card.appendChild(loot);
    }
    grid.appendChild(card);
  });
  if (!(n.cards || []).length) grid.appendChild(el('div', 'empty', 'Aucun donjon.'));
  return grid;
}

/* A row led by a boss portrait (the dungeon list): the name over its
   details, vertically centred against the picture. */
function buildPortraitRow(r) {
  const row = el('div', 'row prow');
  const pic = el('div', 'portrait');
  const src = (window.__PORTRAITS__ || {})[r.portrait];
  if (src) {
    const im = document.createElement('img');
    im.src = src;
    im.alt = '';
    pic.appendChild(im);
  }
  row.appendChild(pic);
  const txt = el('div', 'ptext');
  txt.appendChild(el('span', 'name', r.t));
  if (r.meta) txt.appendChild(el('span', 'meta', r.meta));
  if (r.meta2) txt.appendChild(el('span', 'meta meta2', r.meta2));
  row.appendChild(txt);
  if (r.icons && r.icons.length) row.appendChild(lootStrip(r.icons));
  if (r.tiers && r.tiers.length) row.appendChild(lootTiers(r.tiers));
  (r.btns || []).forEach((b) => {
    const btn = el('button', 'rowbtn', b.t);
    if (b.off) btn.disabled = true;
    else btn.addEventListener('click', () => notify(b.id, b.p || {}));
    row.appendChild(btn);
  });
  return row;
}

/* ---- node dispatch ------------------------------------------------------ */
function buildNode(n) {
  switch (n.k) {
    case 'section': return el('div', 'section', n.t);
    case 'note': return el('p', 'note' + (n.warn ? ' warn' : ''), n.t);
    case 'dcards': return buildDungeonCards(n);
    case 'prose': return inline(el('p', 'prose'), n.t);
    case 'bullets': {
      const ul = el('ul', 'bullets');
      (n.items || []).forEach((it) => ul.appendChild(inline(el('li'), it)));
      return ul;
    }
    case 'code': return el('pre', 'code', n.t);
    case 'gap': return el('div', 'gap');
    case 'sub': return el('div', 'subhead', n.t);
    case 'button': return button(n);
    case 'field': {
      const row = el('div', 'field');
      row.appendChild(el('label', null, n.t));
      const c = buildControl(n.c);
      if (c.classList.contains('ctl')) {
        row.appendChild(c);
      } else {
        const w = el('div', 'ctl');
        w.appendChild(c);
        row.appendChild(w);
      }
      return row;
    }
    case 'search': {
      const row = el('div', 'searchrow');
      row.appendChild(el('label', null, 'Rechercher'));
      const i = el('input');
      i.type = 'text';
      i.value = n.v || '';
      i.addEventListener('input', () => notify(n.id, { value: i.value }));
      row.appendChild(i);
      row.appendChild(el('span', 'count', n.count || ''));
      return row;
    }
    case 'list': {
      const box = el('div', 'list');
      if (n.h && !n.grow) box.style.maxHeight = n.h + 'px';
      if (!n.rows || !n.rows.length) {
        box.appendChild(el('div', 'empty', n.empty || "Rien pour l'instant."));
        return box;
      }
      n.rows.forEach((r) => box.appendChild(buildRow(r)));
      return box;
    }
    case 'toolbar': {
      const bar_ = el('div', 'toolbar');
      (n.btns || []).forEach((b) => bar_.appendChild(button(b)));
      return bar_;
    }
    case 'cards': {
      const box = el('div', 'cards');
      (n.items || []).forEach((c) => {
        const card = el('div', 'card' + (c.tone ? ' ' + c.tone : ''));
        const t = el('div', 't' + (c.art ? ' withart' : ''));
        const art = c.art && (window.__SHEET__ || {})[c.art];
        if (art) { const im = document.createElement('img'); im.src = art; im.alt = ''; t.appendChild(im); }
        t.appendChild(document.createTextNode(c.title));
        card.appendChild(t);
        card.appendChild(el('div', 'v', c.value));
        card.appendChild(el('div', 's', c.sub || ''));
        box.appendChild(card);
      });
      return box;
    }
    case 'meter': return buildMeter(n);
    case 'detail': return buildDetail(n);
    case 'events': return buildEvents(n);
    case 'luck': return buildLuck(n);
    case 'statcards': return buildStatCards(n);
    case 'report': return buildReport(n);
    case 'droptable': return buildDropTable(n);
    case 'collection': return buildCollection(n);
    case 'hunt': return buildHunt(n);
    case 'huntmon': return buildHuntMon(n);
    case 'achievements': return buildAch(n);
    case 'map': return buildMap(n);
    case 'character': return buildCharacter(n);
    case 'build': return buildBuild(n);
    default: return el('div');
  }
}

/* ---- the page ----------------------------------------------------------- */
/* Keyed reconciliation: only nodes whose content changed are rebuilt, so a
   search box keeps its caret and a list keeps its scroll while the page is
   pushed several times a second. */
function renderPage(nodes) {
  const page = $('#page');
  const next = new Map();
  let prevEl = null;
  nodes.forEach((n, i) => {
    const key = n.k + ':' + (n.id || i);
    const sig = JSON.stringify(n);
    const had = NODES.get(key);
    let node;
    if (had && had.sig === sig) {
      node = had.el;
    } else {
      node = buildNode(n);
      if (had) page.replaceChild(node, had.el);
    }
    next.set(key, { el: node, sig });
    const want = prevEl ? prevEl.nextSibling : page.firstChild;
    if (node !== want) page.insertBefore(node, want);
    prevEl = node;
  });
  NODES.forEach((v, k) => { if (!next.has(k)) v.el.remove(); });
  NODES = next;
}

/* Which view a page shows: its first block (a list's toolbar or a detail's
   "back" bar) and the subject of any detail block (`uid`). The live
   updates that redraw a page keep both, so they never scroll it. */
let PAGE_VIEW = '';
function pageView(nodes) {
  const first = nodes[0] ? nodes[0].k + ':' + (nodes[0].id || 0) : '';
  return first + '|' + nodes.filter((n) => n.uid).map((n) => n.uid).join(',');
}

/* The app's own tabs (Réglages, Aide) are icons on the right of the band,
   named on hover. Built as elements, never as markup. */
window.applyState = function (json) {
  let s;
  try {
    s = JSON.parse(json);
  } catch (e) {
    return;
  }
  const prev = STATE;
  STATE = s;

  if (s.zoom !== prev.zoom) setZoom(s.zoom);
  if (s.version !== prev.version) $('#version').textContent = 'v' + s.version;
  if (JSON.stringify([s.link, s.shard]) !== JSON.stringify([prev.link, prev.shard])) {
    renderLink(s.link || {}, s.shard);
  }
  if (JSON.stringify(s.rift) !== JSON.stringify(prev.rift) && s.rift) {
    const r = $('#riftclock');
    r.className = 'card' + (s.rift.tone ? ' ' + s.rift.tone : '');
    r.querySelector('.t').textContent = s.rift.title;
    r.querySelector('.v').textContent = s.rift.value;
    r.querySelector('.s').textContent = s.rift.sub || '';
  }
  if (s.toast && (!prev.toast || s.toast.n !== prev.toast.n) && s.toast.t) {
    showToast(s.toast.t);
  }

  const changed = s.tab !== prev.tab;
  if (changed) {
    const page = $('#page');
    page.textContent = '';
    page.className = 'page-' + s.tab;
    NODES = new Map();
    page.scrollTop = 0;
  }
  renderTabs(s.tabs || [], s.tab);
  // a tab whose content changes view (a monster's page, a dungeon, a rift
  // report opened or closed) starts at the top, like a new tab
  const view = pageView(s.page || []);
  if (s.tab === prev.tab && view !== PAGE_VIEW) $('#page').scrollTop = 0;
  PAGE_VIEW = view;
  renderPage(s.page || []);
  if (changed) pageEnter();
  renderEvents();
  updateEventsBadge();
  renderLinkSteps();
  renderBuildEditor();
};

/* A new tab's page comes in softly: its blocks rise and fade in, one
   after the other. Only on a tab change — the state pushes that redraw a
   block several times a second must not animate. */
let PAGE_ENTER = 0;
function pageEnter() {
  const page = $('#page');
  page.classList.remove('leaving', 'enter');
  void page.offsetWidth;                 // restart the animation
  page.classList.add('enter');
  clearTimeout(PAGE_ENTER);
  PAGE_ENTER = setTimeout(() => page.classList.remove('enter'), 700);
}

/* The pictures arrive after the page, in batches (menu_host.py). */
window.__COLL__ = window.__COLL__ || {};
window.__BEST__ = window.__BEST__ || {};
window.__MAP__ = window.__MAP__ || {};
window.__SKILL__ = window.__SKILL__ || {};
window.__DBG__ = window.__DBG__ || {};
window.addImages = function (ns, json) {
  const into = ns === 'best' ? window.__BEST__ : ns === 'map' ? window.__MAP__
    : ns === 'skill' ? window.__SKILL__ : ns === 'dbg' ? window.__DBG__ : window.__COLL__;
  Object.assign(into, JSON.parse(json));
  if (ns === 'dbg') {
    document.querySelectorAll('[data-bg]').forEach(applyBackdrop);
    // the rifts' loading screen, behind the whole Rifts tab
    const rift = window.__DBG__.Rifts_hd || window.__DBG__.Rifts;
    if (rift) document.documentElement.style.setProperty('--rift-bg', 'url("' + rift + '")');
    return;
  }
  if (ns === 'map') { mapTiles(); return; }
  if (ns === 'skill') { document.querySelectorAll('img.skic[data-id]').forEach((im) => {
    const src = window.__SKILL__[im.dataset.id];
    if (src && !im.src) im.src = src;
  }); return; }
  rerenderHunt();
  rerenderCollection();
  rerenderAch();
};

function fmtN(v) {
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}
