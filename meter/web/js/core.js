/* The window's renderer. The meter pushes the current page as a flat list
 * of nodes a few times a second; this turns them into DOM. All the logic
 * (the numbers, what a button does) lives in the meter.
 *
 * Generic nodes:
 *   {k:"section", t}   {k:"note", t, warn?}   {k:"gap"}
 *   {k:"prose", t}     {k:"bullets", items}   {k:"code", t}
 *   {k:"button", id, t, on?, tone?, p?}
 *   {k:"field", t, c:<control>}   controls: select | slider | label
 *   {k:"list", id, rows:[{t?, name?, cls?, meta?, btns?:[{id,t,p?,off?}]}], empty?}
 *   {k:"sub", t}   a small heading inside a section
 * Live and report nodes:
 *   {k:"toolbar", btns}          {k:"cards", items}
 *   {k:"meter", title, heal, rows, empty}
 *   {k:"detail", name, cls, stats, dmg, heal, elements, empty}
 *   {k:"report", title, when, phases}
 * Page-specific nodes (dcards, hunt, map, build...): see buildNode.
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

/* Breakpoints as classes on <html> (lt-1100: page <= 1100 px), from the
   window width divided by the zoom: media queries ignore the zoom. */
const WIDTH_STEPS = [1250, 1100, 1000, 900, 760, 620];
function widthClasses() {
  const root = document.documentElement;
  const w = window.innerWidth / (parseFloat(root.style.zoom) || 1);
  WIDTH_STEPS.forEach((s) => root.classList.toggle('lt-' + s, w <= s));
}
window.addEventListener('resize', widthClasses);
widthClasses();

/* A header wrapped onto two lines is centred. Measured, not a breakpoint:
   the right-hand group's width varies with the game's state. */
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

/* Back-to-top button, shown once the page is scrolled down. */
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
  // re-check the wrap when the right-hand group or the logo changes size
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
    r.addEventListener('input', () => { out.textContent = r.value + (c.unit || ''); });
    r.addEventListener('change', () => notify(c.id, { value: Number(r.value) }));
    wrap.appendChild(r);
    wrap.appendChild(out);
    return wrap;
  }
  return el('span', 'meta', c.t || '');
}

/* ---- generic nodes ------------------------------------------------------ */
function buildRow(r) {
  if (r.portrait !== undefined) return buildPortraitRow(r);
  const row = el('div', 'row');
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

/* The game's pictures behind some tabs, full size. */
const TAB_BACKDROPS = { Collection: 'loading_screen6_hd', Hunt: 'loading_screen1_hd',
  Achievements: 'loading_screen8_hd', Build: 'splashArt_hd' };

/* The picture a page asked to stand behind it, once the pictures are in. */
function applyPageBackdrop() {
  const page = $('#page');
  const src = page.dataset.pbg && (window.__DBG__ || {})[page.dataset.pbg];
  if (src) page.style.setProperty('--page-bg', 'url("' + src + '")');
  else page.style.removeProperty('--page-bg');
  page.classList.toggle('withbg', !!src);
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

/* The dungeon list: one clickable card per dungeon (boss, runs, loot). */
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
    case 'backdrop': return el('div', 'backdrop');      // read by renderPage
    case 'riftcards': return buildRiftCards(n);
    case 'columns': {
      // blocks side by side, each a list of nodes (the rifts' two chests)
      const row = el('div', 'columns');
      (n.cols || []).forEach((nodes) => {
        const col = el('div', 'column');
        nodes.forEach((c) => col.appendChild(buildNode(c)));
        row.appendChild(col);
      });
      return row;
    }
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
    case 'list': {
      const box = el('div', 'list');
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
        // a badge before the whole count (title and value), when it has one
        const icon = c.icon && (window.__SHEET__ || {})[c.icon];
        const body = icon ? el('div', 'cbody') : card;
        if (icon) {
          card.classList.add('withicon');
          const im = el('img', 'cicon');
          im.src = icon;
          im.alt = '';
          card.appendChild(im);
          card.appendChild(body);
        }
        body.appendChild(t);
        body.appendChild(el('div', 'v', c.value));
        body.appendChild(el('div', 's', c.sub || ''));
        box.appendChild(card);
      });
      return box;
    }
    case 'meter': return buildMeter(n);
    case 'detail': return buildDetail(n);
    case 'luck': return buildLuck(n);
    case 'liveintro': return buildLiveIntro(n);
    case 'statcards': return buildStatCards(n);
    case 'report': return buildReport(n);
    case 'droptable': return buildDropTable(n);
    case 'bosssheet': return buildBossSheet(n);
    case 'welcome': return buildWelcome(n);
    case 'setnav': return buildSetNav(n);
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
/* Keyed reconciliation: only changed nodes are rebuilt, so inputs keep
   their caret and lists their scroll across pushes. */
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
      if (had && had.el !== node) page.replaceChild(node, had.el);
    }
    next.set(key, { el: node, sig });
    const want = prevEl ? prevEl.nextSibling : page.firstChild;
    if (node !== want) page.insertBefore(node, want);
    prevEl = node;
  });
  NODES.forEach((v, k) => { if (!next.has(k)) v.el.remove(); });
  NODES = next;
  // the page's backdrop: its own `backdrop` node, else the tab's default
  const bg = nodes.find((n) => n.k === 'backdrop');
  const tab = (page.className.match(/page-(\w+)/) || [])[1];
  page.dataset.pbg = (bg && bg.bg) || TAB_BACKDROPS[tab] || '';
  applyPageBackdrop();
}

/* Which view a page shows: its first block plus any `uid`s. Live redraws
   keep both, so only a real view change scrolls back to the top. */
let PAGE_VIEW = '';
function pageView(nodes) {
  const first = nodes[0] ? nodes[0].k + ':' + (nodes[0].id || 0) : '';
  return first + '|' + nodes.filter((n) => n.uid).map((n) => n.uid).join(',');
}

/* Takes a JSON string, not a script literal: player names may hold quotes. */
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
  // a view change inside a tab (a monster's page, a dungeon) starts at the top
  const view = pageView(s.page || []);
  if (s.tab === prev.tab && view !== PAGE_VIEW) $('#page').scrollTop = 0;
  PAGE_VIEW = view;
  renderPage(s.page || []);
  if (changed) pageEnter();
  renderEvents();
  updateEventsBadge();
  renderLinkSteps();
  renderBuildEditor();
  if (JSON.stringify(s.update) !== JSON.stringify(prev.update)) renderUpdate(s.update);
};

/* Entry animation, on a tab change only: live redraws must not animate. */
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
    applyPageBackdrop();
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
