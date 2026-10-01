/* The Farever+ window's renderer.
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
  if (r.icons && r.icons.length) {
    const strip = el('div', 'lootstrip');
    r.icons.forEach((ic) => {
      const box = el('span', 'lic');
      box.title = ic.tip || '';
      if (ic.img) {
        const im = document.createElement('img');
        im.src = ic.img;
        im.alt = '';
        box.appendChild(im);
      }
      if (ic.n) box.appendChild(el('b', null, String(ic.n)));
      strip.appendChild(box);
    });
    row.appendChild(strip);
  }
  (r.btns || []).forEach((b) => {
    const btn = el('button', 'rowbtn', b.t);
    if (b.off) btn.disabled = true;
    else btn.addEventListener('click', () => notify(b.id, b.p || {}));
    row.appendChild(btn);
  });
  return row;
}

/* ---- live nodes --------------------------------------------------------- */
function buildMeter(n) {
  const p = el('div', 'panel meter' + (n.heal ? ' heal' : ''));
  p.appendChild(el('h3', null, n.title));
  if (!n.rows || !n.rows.length) {
    p.appendChild(el('div', 'empty', n.empty));
    return p;
  }
  const head = el('div', 'mline mhead');
  ['#', 'Joueur', 'Cl.', 'Dégâts', 'DPS', '%']
    .concat(n.heal ? ['Soins', 'Excès'] : [])
    .forEach((h, i) => head.appendChild(el('span', i > 2 ? 'num' : '', h)));
  p.appendChild(head);
  n.rows.forEach((r) => {
    const row = el('div', 'mrow' + (r.me ? ' me' : '') + (r.focus ? ' focus' : ''));
    const line = el('div', 'mline');
    line.appendChild(el('span', 'rank', r.rank));
    line.appendChild(el('span', 'who', r.name));
    const c = el('span', 'cls');
    c.appendChild(classEl(r.cls, r.ck));
    line.appendChild(c);
    line.appendChild(el('span', 'num', r.dmg));
    line.appendChild(el('span', 'num', r.dps));
    line.appendChild(el('span', 'num', r.pct));
    if (n.heal) {
      line.appendChild(el('span', 'num', r.heal));
      line.appendChild(el('span', 'num', r.over));
    }
    row.appendChild(line);
    const bars = el('div', 'bars');
    bars.appendChild(bar('d', r.df));
    if (n.heal) bars.appendChild(bar('h', r.hf, r.hsf));
    row.appendChild(bars);
    row.addEventListener('click', () => notify('focus_player', { name: r.name }));
    p.appendChild(row);
  });
  return p;
}

function skillList(title, rows, kind) {
  const box = el('div');
  box.appendChild(el('h3', null, title));
  if (!rows || !rows.length) {
    box.appendChild(el('div', 'empty', '—'));
    return box;
  }
  rows.forEach((s) => {
    const sk = el('div', 'sk');
    const l = el('div', 'l');
    l.appendChild(el('span', null, s.t));
    l.appendChild(el('span', 'num', s.v));
    l.appendChild(el('span', 'num', s.pct));
    l.appendChild(el('span', 'num n', s.n + '×'));
    sk.appendChild(l);
    sk.appendChild(bar(kind, s.f, s.sf));
    box.appendChild(sk);
  });
  return box;
}

function buildDetail(n) {
  const p = el('div', 'panel detail');
  if (!n.name) {
    p.appendChild(el('h3', null, 'Détail'));
    p.appendChild(el('div', 'empty', n.empty));
    return p;
  }
  const name = el('div', 'dname', n.name);
  if (n.cls || n.ck) name.appendChild(classEl(n.cls, n.ck, 'cls'));
  p.appendChild(name);
  const stats = el('div', 'stats');
  (n.stats || []).forEach(([c, x]) => {
    const s = el('div', 'stat');
    s.appendChild(el('div', 'c', c));
    s.appendChild(el('div', 'x', x));
    stats.appendChild(s);
  });
  p.appendChild(stats);
  const cols = el('div', 'skills' + (n.heal ? '' : ' one'));
  cols.appendChild(skillList('Dégâts par sort', n.dmg, 'd'));
  if (n.heal) cols.appendChild(skillList('Soins par sort', n.heal, 'h'));
  p.appendChild(cols);
  if (n.elements && n.elements.length) {
    const e = el('div', 'elems');
    n.elements.forEach((x) => {
      const s = el('span');
      const dot = el('i');
      dot.style.background = x.c;
      s.appendChild(dot);
      s.appendChild(document.createTextNode(x.t + ' ' + x.pct));
      e.appendChild(s);
    });
    p.appendChild(e);
  }
  return p;
}

function buildEvents(n) {
  const p = el('div', 'panel events');
  const head = el('div', 'phead');
  const count = n.rows && n.rows.length ? ' (' + n.rows.length + ')' : '';
  head.appendChild(el('h3', null, 'Événements' + count));
  if (count) {
    const clr = el('button', 'rowbtn', 'Effacer');
    clr.addEventListener('click', () => notify('clear_events', {}));
    head.appendChild(clr);
  }
  p.appendChild(head);
  if (!n.rows || !n.rows.length) {
    p.appendChild(el('div', 'empty',
      'Les kills de boss, records et fins de faille apparaîtront ici.'));
    return p;
  }
  /* Newest first, in a box of its own that scrolls: the page never grows
     with the feed. */
  const list = el('div', 'evlist');
  p.appendChild(list);
  n.rows.forEach((r) => {
    const row = el('div', 'ev' + (r.tone ? ' ' + r.tone : ''));
    row.appendChild(el('span', 'when', r.when));
    row.appendChild(el('span', 'txt', r.t));
    if (r.btn) {
      const b = el('button', 'rowbtn', r.btn.t);
      b.addEventListener('click', () => notify(r.btn.id, r.btn.p || {}));
      row.appendChild(b);
    }
    list.appendChild(row);
  });
  return p;
}

/* ---- rift report -------------------------------------------------------- */
const players = (n) => n + (n > 1 ? ' joueurs' : ' joueur');

/* The player whose report card is open, and which of their phases. */
const REPORT_SEL = { name: null, phase: 0, detail: null };

function rankTable(rows, rateLabel) {
  const box = el('div', 'tbl');
  const h = el('div', 'rk h');
  ['', 'Joueur', rateLabel, 'Total', 'Part'].forEach((t, i) =>
    h.appendChild(el('span', i > 1 ? 'num' : '', t)));
  box.appendChild(h);
  rows.forEach((r) => {
    const row = el('div', 'rk click' + (r.zero ? ' zero' : r.rank <= 3 ? ' top' : ''));
    row.title = 'Détail de ' + r.name;
    row.addEventListener('click', () => { REPORT_SEL.name = r.name; renderPlayerCard(); });
    row.appendChild(el('span', null, r.rank));
    const nm = el('span', 'nm', r.name);
    if (r.cls || r.ck) nm.appendChild(classEl(r.cls, r.ck, 'cl'));
    row.appendChild(nm);
    row.appendChild(el('span', 'num', r.rate));
    row.appendChild(el('span', 'num', r.total));
    row.appendChild(el('span', 'num', r.pct));
    box.appendChild(row);
  });
  return box;
}

function buildReport(n) {
  REPORT_SEL.detail = n.detail || null;
  const p = el('div', 'panel report');
  const t = el('div', 'rtitle');
  t.appendChild(el('b', null, n.title));
  t.appendChild(el('span', null, n.when));
  if (n.sub) t.appendChild(el('span', 'rsub', n.sub));
  p.appendChild(t);
  const cols = el('div', 'phases');
  (n.phases || []).forEach((ph) => {
    const c = el('div', 'phase');
    const top = el('div', 'phtop');
    top.appendChild(el('h4', null, ph.label));
    const facts = el('div', 'facts');
    [['Durée', ph.dur], ['DPS', ph.dps.replace(' DPS', '')],
     ['HPS', ph.hps.replace(' HPS', '')]].forEach(([k, v]) => {
      const f = el('div', 'fact');
      f.appendChild(el('span', null, k));
      f.appendChild(el('b', null, v));
      facts.appendChild(f);
    });
    top.appendChild(facts);
    top.appendChild(el('div', 'totals', ph.totals));
    c.appendChild(top);
    if (ph.mvp) {
      const podium = el('div', 'podium');
      const m = el('div', 'mvpbox');
      m.appendChild(el('span', 'lbl', 'MVP dégâts'));
      const mn = el('div', 'mvp', '★ ' + ph.mvp.name + ' ');
      if (ph.mvp.cls || ph.mvp.ck) mn.appendChild(classEl(ph.mvp.cls, ph.mvp.ck, 'big'));
      m.appendChild(mn);
      m.appendChild(el('div', 'v', ph.mvp.v));
      podium.appendChild(m);
      if (ph.healer) {
        const h = el('div', 'mvpbox heal');
        h.appendChild(el('span', 'lbl', 'MVP soins'));
        const hn = el('div', 'healer', '✚ ' + ph.healer.name + ' ');
        if (ph.healer.cls || ph.healer.ck) hn.appendChild(classEl(ph.healer.cls, ph.healer.ck, 'big'));
        h.appendChild(hn);
        h.appendChild(el('div', 'v', ph.healer.v));
        podium.appendChild(h);
      }
      c.appendChild(podium);
    } else {
      c.appendChild(el('div', 'empty', "rien n'a été enregistré pour cette phase"));
    }
    if (ph.dmg && ph.dmg.length) {
      c.appendChild(el('div', 'sub', 'Dégâts — ' + players(ph.dmg.length)));
      c.appendChild(rankTable(ph.dmg, 'DPS'));
    }
    c.appendChild(el('div', 'sub', 'Soins' + (ph.heal && ph.heal.length ? ' — ' + players(ph.heal.length) : '')));
    if (ph.heal && ph.heal.length) c.appendChild(rankTable(ph.heal, 'HPS'));
    else c.appendChild(el('div', 'empty', 'aucun soin enregistré'));
    if (ph.types && ph.types.length) {
      c.appendChild(el('div', 'sub', 'Dégâts par type'));
      const tbox = el('div', 'tbl types');
      c.appendChild(tbox);
      ph.types.forEach((x) => {
        const r = el('div', 'typ');
        const l = el('span', null, x.t);
        l.style.color = x.c;
        r.appendChild(l);
        const b = el('div', 'b');
        b.style.background = x.c;
        b.style.width = (Math.max(0.02, x.f) * 100) + '%';
        r.appendChild(b);
        r.appendChild(el('span', 'num', x.pct));
        tbox.appendChild(r);
      });
    }
    cols.appendChild(c);
  });
  p.appendChild(cols);
  if (n.loot) p.appendChild(buildLoot(n.loot));
  return p;
}

/* ---- node dispatch ------------------------------------------------------ */
function buildNode(n) {
  switch (n.k) {
    case 'section': return el('div', 'section', n.t);
    case 'note': return el('p', 'note' + (n.warn ? ' warn' : ''), n.t);
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
        card.appendChild(el('div', 't', c.title));
        card.appendChild(el('div', 'v', c.value));
        card.appendChild(el('div', 's', c.sub || ''));
        box.appendChild(card);
      });
      return box;
    }
    case 'meter': return buildMeter(n);
    case 'detail': return buildDetail(n);
    case 'events': return buildEvents(n);
    case 'report': return buildReport(n);
    case 'droptable': return buildDropTable(n);
    case 'collection': return buildCollection(n);
    case 'hunt': return buildHunt(n);
    case 'achievements': return buildAch(n);
    case 'map': return buildMap(n);
    case 'character': return buildCharacter(n);
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

function renderTabs(tabs, active) {
  const nav = $('#nav');
  const sig = JSON.stringify([tabs, active]);
  if (nav.dataset.sig === sig) return;
  nav.dataset.sig = sig;
  nav.textContent = '';
  tabs.forEach((tab) => {
    const t = typeof tab === 'string' ? tab : tab.v;
    const label = typeof tab === 'string' ? tab : tab.t;
    if (tab.sep) nav.appendChild(el('div', 'navsep'));
    const b = el('button', t === active ? 'active' : '', label);
    b.addEventListener('click', () => notify('set_tab', { value: t }));
    nav.appendChild(b);
  });
}

/* ---- the state push ----------------------------------------------------- */
/* Called by the host with a JSON *string*: the state carries player names,
   and interpolating those into a script expression would break the page the
   first time somebody had a quote in their name. */
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
  if (s.shard !== prev.shard) {
    $('#shard').textContent = s.shard ? 'Serveur : ' + s.shard : '';
  }
  if (JSON.stringify(s.link) !== JSON.stringify(prev.link)) {
    const l = $('#link');
    l.textContent = (s.link && s.link.t) || '';
    l.style.color = (s.link && s.link.c) || '';
    l.className = 'pill' + (s.link && s.link.retry ? ' retry' : '');
    l.title = (s.link && s.link.retry) ? 'Cliquer pour chercher le jeu maintenant' : '';
    const b = $('#banner');
    b.textContent = (s.link && s.link.tip) || '';
    b.className = (s.link && s.link.tip) ? 'on' : '';
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

  if (s.tab !== prev.tab) {
    const page = $('#page');
    page.textContent = '';
    page.className = 'page-' + s.tab;
    NODES = new Map();
    page.scrollTop = 0;
  }
  renderTabs(s.tabs || [], s.tab);
  renderPage(s.page || []);
};

/* ---- boot --------------------------------------------------------------- */
function boot() {
  if (READY) return;
  READY = true;
  document.querySelectorAll('[data-act]').forEach((b) => {
    b.addEventListener('click', () => notify(b.dataset.act, {}));
  });
  $('#link').addEventListener('click', () => {
    if (STATE.link && STATE.link.retry) notify('link_retry', {});
  });
  notify('boot', {});
}

window.addEventListener('pywebviewready', boot);
if (window.pywebview) boot();

/* A dungeon run's loot: one block per phase, the reward chest first. */
function buildLoot(groups) {
  const box = el('div', 'loot');
  box.appendChild(el('h4', null, 'Butin'));
  if (!groups.length) {
    box.appendChild(el('div', 'empty', "aucun objet ramassé pendant ce run"));
    return box;
  }
  groups.forEach((g) => {
    box.appendChild(el('div', 'sub', g.t));
    const tbl = el('div', 'tbl lootlist');
    g.items.forEach((it) => {
      const r = el('div', 'lootrow' + (it.rk ? ' r-' + it.rk : ''));
      const nm = el('span', 'nm');
      const ic = el('span', 'ic');
      if (it.img) {
        const im = document.createElement('img');
        im.src = it.img;
        im.alt = '';
        ic.appendChild(im);
      }
      nm.appendChild(ic);
      nm.appendChild(el('span', 'n', it.name));
      r.appendChild(nm);
      r.appendChild(el('span', 'rar', [it.rarity, it.level].filter(Boolean).join(' · ')));
      r.appendChild(el('span', 'num', '×' + it.qty));
      tbl.appendChild(r);
    });
    box.appendChild(tbl);
  });
  return box;
}

/* A dungeon's possible loot: icon, name (rarity colour), type, classes,
   source, chance, quantity, how many the saved runs brought back. */
function buildDropTable(n) {
  const box = el('div', 'panel droptable');
  const head = el('div', 'drow dhead');
  ['', 'Objet', 'Type', 'Classes', 'Source', 'Chance', 'Quantité', 'Obtenu']
    .forEach((h) => head.appendChild(el('span', null, h)));
  box.appendChild(head);
  (n.rows || []).forEach((r) => {
    const row = el('div', 'drow' + (r.rk ? ' r-' + r.rk : '') + (r.got ? ' got' : ''));
    const ic = el('span', 'ic');
    if (r.img) {
      const im = document.createElement('img');
      im.src = r.img;
      im.alt = '';
      ic.appendChild(im);
    }
    row.appendChild(ic);
    row.appendChild(el('span', 'nm', r.name));
    row.appendChild(el('span', 'dim', r.type));
    const cl = el('span', 'apt');
    (r.apt || []).forEach((k) => cl.appendChild(classEl('', k)));
    row.appendChild(cl);
    row.appendChild(el('span', 'dim', r.src));
    row.appendChild(el('span', 'num', r.chance));
    row.appendChild(el('span', 'dim', r.qty));
    row.appendChild(el('span', 'num', r.got ? '×' + r.got : '—'));
    box.appendChild(row);
  });
  return box;
}

/* ---- the Collection page ------------------------------------------------ */
/* Category, filter, search and the open card live here, in the page, so
   typing and clicking never wait for the meter; the meter only says what
   exists and what is owned. */
const COLL = { cat: 'mounts', filter: 'all', q: '', open: null, slot: '', cls: '' };
let COLL_NODE = null;

/* The pictures arrive after the page, in batches (menu_host.py). */
window.__COLL__ = window.__COLL__ || {};
window.__BEST__ = window.__BEST__ || {};
window.__MAP__ = window.__MAP__ || {};
window.__SKILL__ = window.__SKILL__ || {};
window.addImages = function (ns, json) {
  const into = ns === 'best' ? window.__BEST__ : ns === 'map' ? window.__MAP__
    : ns === 'skill' ? window.__SKILL__ : window.__COLL__;
  Object.assign(into, JSON.parse(json));
  if (ns === 'map') { mapTiles(); return; }
  if (ns === 'skill') { document.querySelectorAll('img.skic[data-id]').forEach((im) => {
    const src = window.__SKILL__[im.dataset.id];
    if (src && !im.src) im.src = src;
  }); return; }
  rerenderHunt();
  rerenderCollection();
  rerenderAch();
};

function ring(pct) {
  const r = 22, c = 2 * Math.PI * r;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 56 56');
  svg.setAttribute('class', 'ring');
  [['track', c], ['fill', c * (1 - pct / 100)]].forEach(([cls, off]) => {
    const ci = document.createElementNS(ns, 'circle');
    ci.setAttribute('cx', 28); ci.setAttribute('cy', 28); ci.setAttribute('r', r);
    ci.setAttribute('class', cls);
    if (cls === 'fill') {
      ci.setAttribute('stroke-dasharray', c);
      ci.setAttribute('stroke-dashoffset', off);
    }
    svg.appendChild(ci);
  });
  return svg;
}

function collImg(id) {
  const src = (window.__COLL__ || {})[id];
  if (!src) return el('span', 'noimg', '?');
  const im = document.createElement('img');
  im.src = src;
  im.alt = '';
  im.loading = 'lazy';
  return im;
}

function buildCollection(n) {
  COLL_NODE = n;
  const box = el('div', 'coll');
  renderCollection(box, n);
  return box;
}

function rerenderCollection() {
  const box = document.querySelector('.coll');
  if (box && COLL_NODE) renderCollection(box, COLL_NODE);
}

function renderCollection(box, n) {
  const keepQ = document.activeElement && document.activeElement.classList.contains('collq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  const cats = n.cats || [];
  const total = cats.reduce((a, c) => a + c.n, 0);
  const got = cats.reduce((a, c) => a + c.got, 0);
  const pct = total ? Math.round(got / total * 100) : 0;

  box.appendChild(el('div', 'section', 'Collection'));
  box.appendChild(el('p', 'note', 'Tes montures, planeurs, compagnons, équipements et objets. ' + (n.sync || '')));
  const top = el('div', 'colltotal');
  top.appendChild(el('b', null, got + ' / ' + total));
  top.appendChild(el('span', null, pct + ' %'));
  box.appendChild(top);
  const bar_ = el('div', 'collbar');
  const fill = el('i');
  fill.style.width = pct + '%';
  bar_.appendChild(fill);
  box.appendChild(bar_);

  const cards = el('div', 'collcats');
  cats.forEach((c) => {
    const p = c.n ? Math.round(c.got / c.n * 100) : 0;
    const card = el('button', 'collcat' + (COLL.cat === c.v ? ' on' : ''));
    card.type = 'button';
    const rg = el('div', 'rg');
    rg.appendChild(ring(p));
    rg.appendChild(el('span', null, p + '%'));
    card.appendChild(rg);
    const t = el('div', 'tx');
    t.appendChild(el('b', null, c.t));
    t.appendChild(el('span', null, c.got + ' / ' + c.n));
    t.appendChild(el('small', null, (c.n - c.got) + ' restant' + (c.n - c.got > 1 ? 's' : '')));
    card.appendChild(t);
    const first = (n.items || []).find((it) => it.c === c.v && it.own)
      || (n.items || []).find((it) => it.c === c.v);
    if (first) {
      const th = el('span', 'th');
      th.appendChild(collImg(first.id));
      card.appendChild(th);
    }
    card.addEventListener('click', () => { COLL.cat = c.v; rerenderCollection(); });
    cards.appendChild(card);
  });
  box.appendChild(cards);

  const tools = el('div', 'colltools');
  const q = el('input', 'collq');
  q.type = 'text';
  q.placeholder = 'Rechercher par nom';
  q.value = COLL.q;
  q.addEventListener('input', () => { COLL.q = q.value; rerenderCollection(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['all', 'Tous'], ['missing', 'Manquants'], ['own', 'Obtenus']].forEach(([v, t]) => {
    const b = el('button', COLL.filter === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { COLL.filter = v; rerenderCollection(); });
    seg.appendChild(b);
  });
  tools.appendChild(seg);
  box.appendChild(tools);
  const gears = COLL.cat === 'gears';
  if (gears && (n.slots || []).length) {
    const sl = el('div', 'seg collslots');
    [{ v: '', t: 'Tous' }].concat(n.slots).forEach((s) => {
      const b = el('button', COLL.slot === s.v ? 'on' : '', s.t);
      b.type = 'button';
      b.addEventListener('click', () => { COLL.slot = s.v; rerenderCollection(); });
      sl.appendChild(b);
    });
    box.appendChild(sl);
  }
  if (gears && (n.classes || []).length) {
    const cl = el('div', 'seg collslots');
    [{ v: '', t: 'Toutes les classes' }].concat(n.classes).forEach((c) => {
      const b = el('button', COLL.cls === c.v ? 'on' : '', c.t);
      b.type = 'button';
      b.addEventListener('click', () => { COLL.cls = c.v; rerenderCollection(); });
      cl.appendChild(b);
    });
    box.appendChild(cl);
  }

  const cat = cats.find((c) => c.v === COLL.cat) || cats[0] || { one: 'élément' };
  const needle = COLL.q.trim().toLowerCase();
  const shown = (n.items || []).filter((it) => it.c === COLL.cat
    && (COLL.filter === 'all' || (COLL.filter === 'own') === it.own)
    && (!gears || !COLL.slot || it.sl === COLL.slot)
    && (!gears || !COLL.cls || !(it.cls || []).length || it.cls.includes(COLL.cls))
    && (!needle || it.name.toLowerCase().includes(needle)));
  box.appendChild(el('div', 'collcount', shown.length + ' ' + cat.one + (shown.length > 1 ? 's' : '')));

  const grid = el('div', 'collgrid');
  shown.forEach((it) => {
    const card = el('button', 'citem' + (it.own ? ' own' : '') + (it.rk ? ' r-' + it.rk : ''));
    card.type = 'button';
    const pic = el('span', 'pic');
    pic.appendChild(collImg(it.id));
    if (it.own) pic.appendChild(el('span', 'ok', '✓'));
    if (it.count) pic.appendChild(el('span', 'cnt', '×' + fmtN(it.count)));
    card.appendChild(pic);
    card.appendChild(el('span', 'nm', it.name));
    if (it.rmax) {
      const pips = el('span', 'cpips');
      for (let i = 0; i < it.rmax; i++) pips.appendChild(el('i', i < it.rank ? 'on' : ''));
      card.appendChild(pips);
    }
    card.addEventListener('click', () => { COLL.open = it.id; rerenderCollection(); });
    grid.appendChild(card);
  });
  if (!shown.length) grid.appendChild(el('div', 'empty', 'Rien à afficher.'));
  box.appendChild(grid);

  const open = COLL.open && (n.items || []).find((it) => it.id === COLL.open);
  if (open) box.appendChild(buildCollDetail(open, cat));

  if (keepQ) {
    const qi = box.querySelector('.collq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

function buildCollDetail(it, cat) {
  const shade = el('div', 'collshade');
  const close = () => { COLL.open = null; rerenderCollection(); };
  shade.addEventListener('click', (e) => { if (e.target === shade) close(); });
  const d = el('div', 'colldetail' + (it.rk ? ' r-' + it.rk : ''));
  const x = el('button', 'x', '×');
  x.type = 'button';
  x.addEventListener('click', close);
  d.appendChild(x);
  const head = el('div', 'dhead');
  const pic = el('span', 'pic' + (it.own ? ' own' : ''));
  pic.appendChild(collImg(it.id));
  head.appendChild(pic);
  const t = el('div', 'dt');
  t.appendChild(el('h3', 'nm', it.name));
  t.appendChild(el('span', 'sub', [cat.t && cat.t.replace(/s$/, ''), it.slot, it.rar].filter(Boolean).join(' · ')));
  t.appendChild(el('span', 'chip' + (it.own ? ' own' : ''), it.own ? '✓ Obtenu' : 'Manquant'));
  head.appendChild(t);
  d.appendChild(head);
  if (it.rmax) {
    d.appendChild(el('p', 'desc', 'Obtenu ' + fmtN(it.count || 0) + ' fois · rang '
      + (it.rank || 0) + ' / ' + it.rmax
      + (it.uses ? ' · utilisé dans ' + it.uses + ' recette' + (it.uses > 1 ? 's' : '') : '')));
  }
  if (it.slot && (it.lvl || it.apt)) {
    d.appendChild(el('p', 'desc', [it.lvl ? 'Niveau ' + it.lvl : '',
      it.apt ? 'Classes : ' + it.apt : ''].filter(Boolean).join(' · ')));
  }
  if (it.desc) d.appendChild(el('p', 'desc', it.desc));
  d.appendChild(el('div', 'sub2', "Comment l'obtenir"));
  if (it.src && it.src.length) {
    const ul = el('ul', 'srcs');
    it.src.forEach((s) => ul.appendChild(el('li', null, s)));
    d.appendChild(ul);
  } else {
    d.appendChild(el('p', 'none', "Aucune source dans les données du jeu : récompense "
      + "spéciale (événement, précommande…), boutique, ou pas encore disponible."));
  }
  shade.appendChild(d);
  return shade;
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && COLL.open) { COLL.open = null; rerenderCollection(); }
});

/* ---- achievements ------------------------------------------------------- */
const ACH = { cat: '', filter: 'todo', q: '' };
let ACH_NODE = null;

function buildAch(n) {
  ACH_NODE = n;
  const box = el('div', 'ach');
  renderAch(box, n);
  return box;
}

function rerenderAch() {
  const box = document.querySelector('.ach');
  if (box && ACH_NODE) renderAch(box, ACH_NODE);
}

function renderAch(box, n) {
  const keepQ = document.activeElement && document.activeElement.classList.contains('achq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  box.appendChild(el('div', 'section', 'Succès'));
  box.appendChild(el('p', 'note', n.sync || ''));
  const stats = el('div', 'cards');
  [['Points', fmtN(n.pts || 0) + ' / ' + fmtN(n.ptsAll || 0)],
   ['Succès obtenus', (n.got || 0) + ' / ' + (n.n || 0)],
   ['Progression', (n.n ? Math.round(n.got / n.n * 100) : 0) + ' %']].forEach(([t, v]) => {
    const c = el('div', 'card');
    c.appendChild(el('div', 't', t));
    c.appendChild(el('div', 'v', v));
    stats.appendChild(c);
  });
  box.appendChild(stats);

  const cats = el('div', 'achcats');
  [{ v: '', t: 'Toutes', got: n.got, n: n.n }].concat(n.cats || []).forEach((c) => {
    const b = el('button', 'achcat' + (ACH.cat === c.v ? ' on' : ''));
    b.type = 'button';
    if (c.img) {
      const src = (window.__COLL__ || {})[c.img];
      if (src) {
        const im = document.createElement('img');
        im.src = src;
        im.alt = '';
        b.appendChild(im);
      }
    }
    const t = el('span', 'ct');
    t.appendChild(el('b', null, c.t));
    t.appendChild(el('span', null, c.got + ' / ' + c.n + (c.pts != null ? ' · ' + c.pts + ' pts' : '')));
    b.appendChild(t);
    b.addEventListener('click', () => { ACH.cat = c.v; rerenderAch(); });
    cats.appendChild(b);
  });
  box.appendChild(cats);

  const tools = el('div', 'colltools');
  const q = el('input', 'collq achq');
  q.type = 'text';
  q.placeholder = 'Rechercher un succès';
  q.value = ACH.q;
  q.addEventListener('input', () => { ACH.q = q.value; rerenderAch(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['todo', 'En cours'], ['near', 'Presque finis'], ['done', 'Terminés'], ['all', 'Tous']].forEach(([v, t]) => {
    const b = el('button', ACH.filter === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { ACH.filter = v; rerenderAch(); });
    seg.appendChild(b);
  });
  tools.appendChild(seg);
  box.appendChild(tools);

  const needle = ACH.q.trim().toLowerCase();
  let shown = (n.items || []).filter((it) => (!ACH.cat || it.c === ACH.cat)
    && (ACH.filter === 'all' || (ACH.filter === 'done') === it.done)
    && (ACH.filter !== 'near' || (it.pct != null && it.pct >= 0.5))
    && (!needle || (it.name + ' ' + it.desc).toLowerCase().includes(needle)));
  if (ACH.filter === 'near') shown = shown.slice().sort((a, b) => b.pct - a.pct);
  box.appendChild(el('div', 'collcount', shown.length + ' succès'));

  const list = el('div', 'achlist');
  shown.forEach((it) => {
    const card = el('div', 'achcard' + (it.done ? ' done' : ''));
    const top = el('div', 'atop');
    const t = el('div', 'at');
    t.appendChild(el('b', 'nm', it.name));
    if (it.subT) t.appendChild(el('span', 'sub', it.subT));
    top.appendChild(t);
    top.appendChild(el('span', 'apts', it.done ? '✓' : '+' + it.pts + ' pts'));
    card.appendChild(top);
    if (it.desc) card.appendChild(el('p', 'ad', it.desc));
    if (it.need != null && !it.done) {
      const pr = el('div', 'aprog');
      const bar = el('div', 'abar');
      const fill = el('i');
      fill.style.width = Math.round((it.pct || 0) * 100) + '%';
      bar.appendChild(fill);
      pr.appendChild(bar);
      pr.appendChild(el('span', null, fmtN(Math.floor(it.have)) + ' / ' + fmtN(it.need)));
      card.appendChild(pr);
    }
    const foot = el('div', 'afoot');
    if ((it.tiers || []).length > 1) {
      const pips = el('span', 'apips');
      it.tiers.forEach((tr) => pips.appendChild(el('i', tr.ok ? 'on' : '')));
      foot.appendChild(pips);
    }
    (it.rewards || []).forEach((r) => {
      const rw = el('span', 'arew');
      if (r.img) {
        const im = document.createElement('img');
        im.src = r.img;
        im.alt = '';
        rw.appendChild(im);
      }
      rw.appendChild(el('span', null, r.name));
      foot.appendChild(rw);
    });
    if (it.when) foot.appendChild(el('span', 'awhen', 'Obtenu le ' + it.when));
    if (foot.childNodes.length) card.appendChild(foot);
    list.appendChild(card);
  });
  if (!shown.length) list.appendChild(el('div', 'empty', 'Rien à afficher.'));
  box.appendChild(list);

  if (keepQ) {
    const qi = box.querySelector('.achq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

/* ---- the hunting log ---------------------------------------------------- */
const HUNT = { view: 'units', reg: 'all', filter: 'all', sort: 'kills', q: '',
               farm: 'missing' };
let HUNT_NODE = null;

function buildHunt(n) {
  HUNT_NODE = n;
  const box = el('div', 'hunt');
  renderHunt(box, n);
  return box;
}

function rerenderHunt() {
  const box = document.querySelector('.hunt');
  if (box && HUNT_NODE) renderHunt(box, HUNT_NODE);
}

function fmtN(v) {
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function huntImg(id) {
  const src = (window.__BEST__ || {})[id];
  if (!src) return el('span', 'noimg', '?');
  const im = document.createElement('img');
  im.src = src;
  im.alt = '';
  im.loading = 'lazy';
  return im;
}

function renderHunt(box, n) {
  const keepQ = document.activeElement && document.activeElement.classList.contains('huntq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  const items = n.items || [];
  const hunted = items.filter((it) => it.kills > 0).length;
  const mastered = items.filter((it) => it.rank >= it.max).length;

  box.appendChild(el('div', 'section', 'Tableau de chasse'));
  box.appendChild(el('p', 'note', n.sync || ''));
  const stats = el('div', 'cards');
  [['Monstres tués', fmtN(n.total || 0), 'au total'],
   ['Espèces chassées', hunted + ' / ' + items.length, 'au moins un kill'],
   ['Codex maîtrisé', mastered + ' / ' + items.length, 'rang maximal atteint']]
    .forEach(([t, v, s]) => {
      const c = el('div', 'card');
      c.appendChild(el('div', 't', t));
      c.appendChild(el('div', 'v', v));
      c.appendChild(el('div', 's', s));
      stats.appendChild(c);
    });
  box.appendChild(stats);

  const views = el('div', 'seg huntview');
  [['units', 'Monstres'], ['families', 'Familles'], ['farm', 'Montures']].forEach(([v, t]) => {
    const b = el('button', HUNT.view === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { HUNT.view = v; rerenderHunt(); });
    views.appendChild(b);
  });
  box.appendChild(views);
  if (HUNT.view === 'families') {
    renderFamilies(box, n);
    return;
  }
  if (HUNT.view === 'farm') {
    renderFarm(box, n);
    return;
  }

  const chips = el('div', 'huntregs');
  [{ v: 'all', t: 'Toutes les régions', n: items.length }].concat(n.regions || [])
    .forEach((r) => {
      const b = el('button', 'chip' + (HUNT.reg === r.v ? ' on' : ''), r.t + ' · ' + r.n);
      b.type = 'button';
      b.addEventListener('click', () => { HUNT.reg = r.v; rerenderHunt(); });
      chips.appendChild(b);
    });
  box.appendChild(chips);

  const tools = el('div', 'colltools');
  const q = el('input', 'collq huntq');
  q.type = 'text';
  q.placeholder = 'Rechercher un monstre ou une famille';
  q.value = HUNT.q;
  q.addEventListener('input', () => { HUNT.q = q.value; rerenderHunt(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['all', 'Tous'], ['none', 'Jamais tués'], ['doing', 'En cours'], ['done', 'Maîtrisés']]
    .forEach(([v, t]) => {
      const b = el('button', HUNT.filter === v ? 'on' : '', t);
      b.type = 'button';
      b.addEventListener('click', () => { HUNT.filter = v; rerenderHunt(); });
      seg.appendChild(b);
    });
  tools.appendChild(seg);
  const sort = el('div', 'seg');
  [['kills', 'Plus tués'], ['name', 'Nom']].forEach(([v, t]) => {
    const b = el('button', HUNT.sort === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { HUNT.sort = v; rerenderHunt(); });
    sort.appendChild(b);
  });
  tools.appendChild(sort);
  box.appendChild(tools);

  const needle = HUNT.q.trim().toLowerCase();
  const shown = items.filter((it) =>
    (HUNT.reg === 'all' || it.reg === HUNT.reg)
    && (HUNT.filter === 'all'
        || (HUNT.filter === 'none' && it.kills === 0)
        || (HUNT.filter === 'doing' && it.kills > 0 && it.rank < it.max)
        || (HUNT.filter === 'done' && it.rank >= it.max))
    && (!needle || it.name.toLowerCase().includes(needle)
        || (it.fam || '').toLowerCase().includes(needle)));
  shown.sort(HUNT.sort === 'name'
    ? (a, b) => a.name.localeCompare(b.name, 'fr')
    : (a, b) => b.kills - a.kills || a.name.localeCompare(b.name, 'fr'));
  box.appendChild(el('div', 'collcount', shown.length + ' monstre' + (shown.length > 1 ? 's' : '')));

  const grid = el('div', 'huntgrid');
  shown.forEach((it) => {
    const card = el('div', 'hitem' + (it.kills ? ' seen' : '') + (it.rank >= it.max ? ' done' : ''));
    card.title = [it.name, it.fam, it.zones].filter(Boolean).join(' — ');
    const pic = el('span', 'pic');
    pic.appendChild(huntImg(it.id));
    if (it.tier) pic.appendChild(el('span', 'tier', it.tier));
    card.appendChild(pic);
    const body = el('div', 'hb');
    body.appendChild(el('span', 'nm', it.name));
    body.appendChild(el('span', 'fam', it.fam || '—'));
    const k = el('div', 'kills');
    k.appendChild(el('b', null, fmtN(it.kills)));
    k.appendChild(el('span', null, it.kills > 1 ? 'kills' : 'kill'));
    body.appendChild(k);
    const pips = el('div', 'pips');
    for (let i = 0; i < it.max; i++) pips.appendChild(el('i', i < it.rank ? 'on' : ''));
    pips.appendChild(el('span', null, it.rank >= it.max ? 'maîtrisé'
      : it.next ? it.kills + ' / ' + it.next : ''));
    body.appendChild(pips);
    card.appendChild(body);
    grid.appendChild(card);
  });
  if (!shown.length) grid.appendChild(el('div', 'empty', 'Rien à afficher.'));
  box.appendChild(grid);

  if (keepQ) {
    const qi = box.querySelector('.huntq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

/* The hunting log by family: a family shares its loot table, so its total
   is what counts for a drop any of its species can give. */
function renderFamilies(box, n) {
  const fams = n.families || [];
  box.appendChild(el('p', 'note', 'Toutes les espèces d’une famille tirent la même table de '
    + 'butin : c’est le total de la famille qui compte pour ses objets rares.'));
  const grid = el('div', 'famgrid');
  fams.forEach((f) => {
    const card = el('div', 'famcard' + (f.kills ? ' seen' : ''));
    const top = el('div', 'ftop');
    const pic = el('span', 'pic');
    pic.appendChild(huntImg(f.img));
    top.appendChild(pic);
    const t = el('div', 'ft');
    t.appendChild(el('b', 'nm', f.name));
    const k = el('div', 'kills');
    k.appendChild(el('b', null, fmtN(f.kills)));
    k.appendChild(el('span', null, f.kills > 1 ? 'kills' : 'kill'));
    t.appendChild(k);
    t.appendChild(el('span', 'fam', f.hunted + ' / ' + f.species + ' espèces chassées'));
    top.appendChild(t);
    card.appendChild(top);
    grid.appendChild(card);
  });
  if (!fams.length) grid.appendChild(el('div', 'empty', 'Rien à afficher.'));
  box.appendChild(grid);
}

/* ---- the world map -------------------------------------------------------- */
/* A small pan/zoom viewer, nothing loaded from anywhere: the game's minimap
   tiles (sent after the page, like the other pictures) in a transformed
   layer, and the points in a layer of their own, placed in screen pixels so
   they keep their size at every zoom. */
const MAP = { s: null, x: 0, y: 0, on: {}, reg: 'all', sel: null, node: null,
              hideFound: false };
const MAP_COLORS = { chest: '#E2B65B', vault: '#C07CF0', recipe: '#5B8DEF',
                     orb: '#F2665E', obelisk: '#B89CFF', respawn: '#4FD1C5' };

function mapPx(n, x, y) {
  const m = n.meta, k = m.tile_px / m.units_per_tile;
  return [(x - m.tx[0] * m.units_per_tile) * k, (y - m.ty[0] * m.units_per_tile) * k];
}

function buildMap(n) {
  MAP.node = n;
  (n.cats || []).forEach((c) => { if (!(c.v in MAP.on)) MAP.on[c.v] = true; });
  const m = n.meta || {};
  const wrap = el('div', 'mapwrap');
  const vp = el('div', 'mapvp');
  const world = el('div', 'mworld');
  const size = m.tile_px || 512;
  (m.tiles || []).forEach((key) => {
    const [tx, ty] = key.split('_').map(Number);
    const im = document.createElement('img');
    im.className = 'mtile';
    im.dataset.key = key;
    im.alt = '';
    im.style.left = (tx - m.tx[0]) * size + 'px';
    im.style.top = (ty - m.ty[0]) * size + 'px';
    // one pixel of overlap: scaled, tiles laid edge to edge show seams
    im.style.width = im.style.height = (size + 1) + 'px';
    if (window.__MAP__[key]) im.src = window.__MAP__[key];
    world.appendChild(im);
  });
  vp.appendChild(world);
  const marks = el('div', 'mmarks');
  (n.points || []).forEach((p, i) => {
    const d = el('div', 'mk mk-' + p.c + (p.f ? ' found' : ''));
    d.dataset.i = i;
    const [px, py] = mapPx(n, p.x, p.y);
    d.dataset.px = px;
    d.dataset.py = py;
    d.addEventListener('click', (e) => { e.stopPropagation(); MAP.sel = i; mapPopup(); });
    marks.appendChild(d);
  });
  vp.appendChild(marks);
  wrap.appendChild(vp);
  wrap.appendChild(mapPanel(n));
  wrap.appendChild(el('div', 'mpop'));
  mapInteract(vp);
  requestAnimationFrame(() => { if (MAP.s === null) mapFit(); mapApply(); mapPopup(); });
  return wrap;
}

function mapTiles() {
  document.querySelectorAll('.mtile').forEach((im) => {
    const src = window.__MAP__[im.dataset.key];
    if (src && !im.src) im.src = src;
  });
}

function mapPanel(n) {
  const p = el('div', 'mpanel');
  p.appendChild(el('div', 'mtitle', 'Carte de Siagarta'));
  const inReg = (pt) => MAP.reg === 'all' || pt.r === MAP.reg;
  const pts = n.points || [];
  // "found / total" when the progress has been read, the total otherwise
  const tally = (list) => n.known
    ? list.filter((pt) => pt.f).length + ' / ' + list.length : String(list.length);
  if (n.known) {
    const mine = pts.filter(inReg);
    const got = mine.filter((pt) => pt.f).length;
    const pct = mine.length ? Math.round(got / mine.length * 100) : 0;
    const prog = el('div', 'mprog');
    prog.appendChild(el('b', null, got + ' / ' + mine.length));
    prog.appendChild(el('span', null, pct + ' %'));
    p.appendChild(prog);
    const bar_ = el('div', 'collbar');
    const fill = el('i');
    fill.style.width = pct + '%';
    bar_.appendChild(fill);
    p.appendChild(bar_);
  }
  p.appendChild(el('div', 'msync', n.sync || ''));
  const regs = el('div', 'mregs');
  [{ v: 'all', t: 'Tout Siagarta' }].concat(n.regions || []).forEach((r) => {
    const b = el('button', MAP.reg === r.v ? 'on' : '', r.t);
    b.type = 'button';
    b.addEventListener('click', () => { MAP.reg = r.v; mapRefreshPanel(); mapApply(); });
    regs.appendChild(b);
  });
  p.appendChild(regs);
  if (n.known) {
    const hf = el('button', 'mhide' + (MAP.hideFound ? ' on' : ''),
      MAP.hideFound ? '✓ Éléments trouvés masqués' : 'Masquer les éléments trouvés');
    hf.type = 'button';
    hf.addEventListener('click', () => { MAP.hideFound = !MAP.hideFound; mapRefreshPanel(); mapApply(); });
    p.appendChild(hf);
  }
  const all = el('div', 'mall');
  [['Tout afficher', true], ['Tout masquer', false]].forEach(([t, v]) => {
    const b = el('button', null, t);
    b.type = 'button';
    b.addEventListener('click', () => {
      (n.cats || []).forEach((c) => { MAP.on[c.v] = v; });
      mapRefreshPanel(); mapApply();
    });
    all.appendChild(b);
  });
  p.appendChild(all);
  let group = null, grid = null;
  (n.cats || []).forEach((c) => {
    if (c.g !== group) {
      group = c.g;
      const shown = pts.filter((pt) => inReg(pt)
        && (n.cats.find((x) => x.v === pt.c) || {}).g === group);
      const h = el('div', 'mgroup');
      h.appendChild(el('b', null, group));
      h.appendChild(el('span', null, tally(shown)));
      p.appendChild(h);
      grid = el('div', 'mcats');
      p.appendChild(grid);
    }
    const cnt = tally(pts.filter((pt) => pt.c === c.v && inReg(pt)));
    const b = el('button', 'mcat' + (MAP.on[c.v] ? ' on' : ''));
    b.type = 'button';
    b.appendChild(el('i', 'mk mk-' + c.v));
    b.appendChild(el('span', 'ml', c.t));
    b.appendChild(el('span', 'mn', cnt));
    b.addEventListener('click', () => { MAP.on[c.v] = !MAP.on[c.v]; mapRefreshPanel(); mapApply(); });
    grid.appendChild(b);
  });
  p.appendChild(el('div', 'mhint', 'Glisser pour déplacer · molette pour zoomer · clic sur un point pour le détail'));
  return p;
}

function mapRefreshPanel() {
  const old = document.querySelector('.mpanel');
  if (old && MAP.node) old.replaceWith(mapPanel(MAP.node));
}

function mapFit() {
  const vp = document.querySelector('.mapvp');
  const n = MAP.node;
  if (!vp || !n || !(n.points || []).length) return;
  const w = vp.clientWidth, h = vp.clientHeight;
  if (!w || !h) return;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  n.points.forEach((p) => {
    const [px, py] = mapPx(n, p.x, p.y);
    x0 = Math.min(x0, px); y0 = Math.min(y0, py);
    x1 = Math.max(x1, px); y1 = Math.max(y1, py);
  });
  const pad = 40;
  MAP.s = Math.min((w - 2 * pad) / (x1 - x0), (h - 2 * pad) / (y1 - y0));
  MAP.x = (w - (x1 - x0) * MAP.s) / 2 - x0 * MAP.s;
  MAP.y = (h - (y1 - y0) * MAP.s) / 2 - y0 * MAP.s;
}

function mapApply() {
  const world = document.querySelector('.mworld');
  if (!world) return;
  // Framed on the first call that has a real size: a window that was hidden
  // or minimised when the tab opened has none yet.
  if (MAP.s === null) mapFit();
  if (MAP.s === null) return;
  world.style.transform = `translate(${MAP.x}px, ${MAP.y}px) scale(${MAP.s})`;
  const pts = (MAP.node && MAP.node.points) || [];
  document.querySelectorAll('.mmarks .mk').forEach((d) => {
    const p = pts[d.dataset.i];
    const show = p && MAP.on[p.c] && (MAP.reg === 'all' || p.r === MAP.reg)
      && !(MAP.hideFound && p.f);
    d.style.display = show ? '' : 'none';
    if (!show) return;
    d.style.transform = `translate(${d.dataset.px * MAP.s + MAP.x}px, ${d.dataset.py * MAP.s + MAP.y}px)`;
    d.classList.toggle('sel', +d.dataset.i === MAP.sel);
  });
  mapPopupPlace();
}

function mapZoom(f, cx, cy) {
  if (MAP.s === null) mapFit();
  if (MAP.s === null) return;
  const s = Math.min(3, Math.max(0.08, MAP.s * f));
  f = s / MAP.s;
  MAP.x = cx - (cx - MAP.x) * f;
  MAP.y = cy - (cy - MAP.y) * f;
  MAP.s = s;
  mapApply();
}

function mapInteract(vp) {
  let drag = null;
  vp.addEventListener('pointerdown', (e) => {
    if (e.target.classList.contains('mk')) return;
    drag = { x: e.clientX, y: e.clientY, mx: MAP.x, my: MAP.y, moved: false };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add('drag');
  });
  vp.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    MAP.x = drag.mx + dx;
    MAP.y = drag.my + dy;
    mapApply();
  });
  const end = () => {
    if (drag && !drag.moved) { MAP.sel = null; mapPopup(); }
    drag = null;
    vp.classList.remove('drag');
  };
  vp.addEventListener('pointerup', end);
  vp.addEventListener('pointercancel', end);
  vp.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    mapZoom(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  vp.addEventListener('dblclick', (e) => {
    const r = vp.getBoundingClientRect();
    mapZoom(2, e.clientX - r.left, e.clientY - r.top);
  });
}

function mapPopup() {
  const pop = document.querySelector('.mpop');
  const n = MAP.node;
  if (!pop || !n) return;
  const p = MAP.sel !== null ? n.points[MAP.sel] : null;
  pop.textContent = '';
  pop.style.display = p ? '' : 'none';
  if (!p) { mapApply(); return; }
  const c = (n.cats || []).find((x) => x.v === p.c) || {};
  const h = el('div', 'ph');
  h.appendChild(el('i', 'mk mk-' + p.c));
  h.appendChild(el('b', null, c.t || p.c));
  pop.appendChild(h);
  const reg = (n.regions || []).find((r) => r.v === p.r);
  pop.appendChild(el('div', 'pz', [p.z, reg && reg.v !== 'other' ? reg.t : ''].filter(Boolean).join(' — ') || 'Zone inconnue'));
  if (n.known) pop.appendChild(el('div', 'pf' + (p.f ? ' on' : ''), p.f ? '✓ Trouvé' : 'Pas encore trouvé'));
  pop.appendChild(el('div', 'pc', 'n° ' + p.n + ' · x ' + Math.round(p.x) + ', y ' + Math.round(p.y)));
  mapApply();
}

function mapPopupPlace() {
  const pop = document.querySelector('.mpop');
  const n = MAP.node;
  if (!pop || !n || MAP.sel === null || pop.style.display === 'none') return;
  const [px, py] = mapPx(n, n.points[MAP.sel].x, n.points[MAP.sel].y);
  pop.style.transform = `translate(${px * MAP.s + MAP.x + 14}px, ${py * MAP.s + MAP.y - 20}px)`;
}

window.addEventListener('resize', () => { if (document.querySelector('.mapvp')) mapApply(); });

/* The mounts and gliders monsters can drop: each with every monster that can
   drop it, the kills behind it and the chances, per kill and so far. */
function renderFarm(box, n) {
  const all = n.farm || [];
  box.appendChild(el('p', 'note', 'Les montures et planeurs qui tombent sur des monstres, avec le '
    + 'total de tes kills sur tous ceux qui peuvent les donner (survole un portrait pour son nom). '
    + 'Les chances « déjà eue » supposent un tirage indépendant à chaque kill.'));
  const seg = el('div', 'seg farmseg');
  [['missing', 'À obtenir'], ['own', 'Obtenues'], ['all', 'Toutes']].forEach(([v, t]) => {
    const b = el('button', HUNT.farm === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { HUNT.farm = v; rerenderHunt(); });
    seg.appendChild(b);
  });
  box.appendChild(seg);
  const shown = all.filter((m) => HUNT.farm === 'all' || (HUNT.farm === 'own') === m.own);
  box.appendChild(el('div', 'collcount', shown.length + ' objet' + (shown.length > 1 ? 's' : '')));
  const list = el('div', 'farmlist');
  shown.forEach((m) => {
    const card = el('div', 'farmcard' + (m.own ? ' own' : '') + (m.rk ? ' r-' + m.rk : ''));
    const head = el('div', 'fhead');
    const ic = el('span', 'fic');
    const src = (window.__COLL__ || {})[m.id];
    if (src) {
      const im = document.createElement('img');
      im.src = src;
      im.alt = '';
      ic.appendChild(im);
    }
    head.appendChild(ic);
    const t = el('div', 'ft');
    t.appendChild(el('b', 'nm', m.name));
    const nm = (m.mobs || []).length;
    t.appendChild(el('span', 'fam', m.cat + ' · ' + nm + ' monstre' + (nm > 1 ? 's peuvent ' : ' peut ')
      + (m.cat === 'Planeur' ? 'le' : 'la') + ' donner'));
    head.appendChild(t);
    head.appendChild(el('span', 'fstat' + (m.own ? ' own' : ''), m.own ? '✓ obtenue'
      : m.had ? m.had + ' de chances de l’avoir déjà eue' : 'aucun kill'));
    card.appendChild(head);
    // one row of numbers: kills summed over every source, chance per kill
    const stats = el('div', 'fstats');
    [[fmtN(m.kills), m.kills > 1 ? 'kills au total' : 'kill au total'],
     [m.pct, m.odds]].forEach(([v, t]) => {
      const c = el('div', 'fs');
      c.appendChild(el('b', null, v));
      c.appendChild(el('span', null, t));
      stats.appendChild(c);
    });
    card.appendChild(stats);
    // every monster that can drop it, portraits only, name on hover
    const mobs = el('div', 'fmobs');
    (m.mobs || []).forEach((x) => {
      const mb = el('span', 'fmob' + (x.k ? ' seen' : ''));
      mb.title = x.name + ' — ' + fmtN(x.k) + (x.k > 1 ? ' kills' : ' kill');
      mb.appendChild(huntImg(x.img));
      mobs.appendChild(mb);
    });
    card.appendChild(mobs);
    list.appendChild(card);
  });
  if (!shown.length) list.appendChild(el('div', 'empty', 'Rien à afficher.'));
  box.appendChild(list);
}

/* A player's card in a report: their damage and healing, skill by skill. */
function renderPlayerCard() {
  document.querySelectorAll('.pcshade').forEach((x) => x.remove());
  const d = REPORT_SEL.detail && REPORT_SEL.detail[REPORT_SEL.name];
  if (!d) return;
  const phases = d.phases || [];
  if (REPORT_SEL.phase >= phases.length) REPORT_SEL.phase = 0;
  const ph = phases[REPORT_SEL.phase];
  const shade = el('div', 'collshade pcshade');
  const close = () => { REPORT_SEL.name = null; shade.remove(); };
  shade.addEventListener('click', (e) => { if (e.target === shade) close(); });
  const card = el('div', 'colldetail pcard');
  const x = el('button', 'x', '×');
  x.type = 'button';
  x.addEventListener('click', close);
  card.appendChild(x);
  const h = el('div', 'pch');
  const nm = el('h3', null, d.name + ' ');
  if (d.cls || d.ck) nm.appendChild(classEl(d.cls, d.ck, 'big'));
  h.appendChild(nm);
  if (phases.length > 1) {
    const seg = el('div', 'seg');
    phases.forEach((p, i) => {
      const b = el('button', i === REPORT_SEL.phase ? 'on' : '', p.label);
      b.type = 'button';
      b.addEventListener('click', () => { REPORT_SEL.phase = i; renderPlayerCard(); });
      seg.appendChild(b);
    });
    h.appendChild(seg);
  }
  card.appendChild(h);
  const facts = el('div', 'facts');
  (ph.facts || []).forEach(([k, v]) => {
    const f = el('div', 'fact');
    f.appendChild(el('span', null, k));
    f.appendChild(el('b', null, v));
    facts.appendChild(f);
  });
  card.appendChild(facts);

  const table = (title, rows, cols) => {
    card.appendChild(el('div', 'sub2', title));
    if (!rows.length) { card.appendChild(el('p', 'none', 'rien d’enregistré')); return; }
    const t = el('div', 'tbl pctbl');
    const hd = el('div', 'pr h');
    ['Compétence', ''].concat(cols.map((c) => c[0])).forEach((c, i) =>
      hd.appendChild(el('span', i > 1 ? 'num' : '', c)));
    t.appendChild(hd);
    rows.forEach((r) => {
      const row = el('div', 'pr');
      row.appendChild(el('span', 'nm', r.n));
      const b = el('div', 'bar dmg');
      const i = el('i', 'all');
      i.style.width = (Math.max(0.01, r.f) * 100) + '%';
      b.appendChild(i);
      row.appendChild(b);
      cols.forEach((c) => row.appendChild(el('span', 'num', r[c[1]])));
      t.appendChild(row);
    });
    card.appendChild(t);
  };
  table('Sources de dégâts', ph.skills || [],
        [['Part', 'pct'], ['Dégâts', 't'], ['Coups', 'hits'], ['Crit.', 'crit'], ['Moy.', 'avg']]);
  if ((ph.heals || []).length) {
    table('Sources de soins', ph.heals, [['Part', 'pct'], ['Soins', 't'], ['Nombre', 'hits']]);
  }
  if ((ph.elements || []).length) {
    card.appendChild(el('div', 'sub2', 'Dégâts par type'));
    const tb = el('div', 'tbl types');
    ph.elements.forEach((x2) => {
      const r = el('div', 'typ');
      const l = el('span', null, x2.t);
      l.style.color = x2.c;
      r.appendChild(l);
      const b = el('div', 'b');
      b.style.background = x2.c;
      b.style.width = (Math.max(0.02, x2.f) * 100) + '%';
      r.appendChild(b);
      r.appendChild(el('span', 'num', x2.pct));
      tb.appendChild(r);
    });
    card.appendChild(tb);
  }
  shade.appendChild(card);
  document.body.appendChild(shade);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && REPORT_SEL.name) {
    REPORT_SEL.name = null;
    document.querySelectorAll('.pcshade').forEach((x) => x.remove());
  }
});

/* ---- the Character tab ------------------------------------------------- */
function charClass(p) {
  return classEl(p.cls, p.ck, 'cl');
}

function buildCharacter(n) {
  const box = el('div', 'charpage');
  const side = el('div', 'charside');
  side.appendChild(el('div', 'section', 'Joueurs à proximité'));
  if (!n.live) {
    side.appendChild(el('p', 'note', 'Lance le jeu pour voir les joueurs autour de toi.'));
  } else if (!(n.near || []).length) {
    side.appendChild(el('p', 'note', 'Personne à proximité.'));
  }
  const near = el('div', 'charlist');
  (n.near || []).forEach((p) => {
    const row = el('div', 'charrow' + (p.me ? ' me' : ''));
    const t = el('span', 'cn');
    t.appendChild(charClass(p));
    t.appendChild(el('b', null, p.n));
    t.appendChild(el('span', 'lv', 'niv. ' + (p.lvl || '?')));
    row.appendChild(t);
    const b = el('button', 'rowbtn', p.busy ? 'Analyse…' : p.saved ? 'Réanalyser' : 'Analyser');
    b.type = 'button';
    if (p.busy) b.disabled = true;
    b.addEventListener('click', () => notify('char_analyze', { name: p.n }));
    row.appendChild(b);
    near.appendChild(row);
  });
  side.appendChild(near);
  side.appendChild(el('div', 'section', 'Analysés pendant la session'));
  if (!(n.saved || []).length) side.appendChild(el('p', 'note', 'Aucun pour l’instant : analyse un joueur.'));
  const saved = el('div', 'charlist');
  (n.saved || []).forEach((p) => {
    const row = el('div', 'charrow click' + (n.open && n.open.n === p.n ? ' on' : ''));
    const t = el('span', 'cn');
    t.appendChild(charClass(p));
    t.appendChild(el('b', null, p.n));
    t.appendChild(el('span', 'lv', 'niv. ' + (p.lvl || '?')));
    row.appendChild(t);
    row.appendChild(el('span', 'wh', p.when));
    row.addEventListener('click', () => notify('char_open', { name: p.n }));
    saved.appendChild(row);
  });
  side.appendChild(saved);
  box.appendChild(side);

  const main = el('div', 'charmain');
  const o = n.open;
  if (!o) {
    main.appendChild(el('div', 'empty charempty', 'Choisis un joueur à analyser, ou un profil enregistré.'));
  } else {
    const head = el('div', 'charhead');
    const lv = el('div', 'clvl');
    lv.appendChild(el('span', null, 'Niveau'));
    lv.appendChild(el('b', null, String(o.lvl || '?')));
    head.appendChild(lv);
    const ic = el('span', 'cico');
    ic.appendChild(classEl(o.cls, o.ck, 'big'));
    head.appendChild(ic);
    const t = el('div', 'ct');
    t.appendChild(el('b', null, o.n));
    t.appendChild(el('span', null, o.cls + ' · analysé le ' + o.when));
    head.appendChild(t);
    const x = el('button', 'rowbtn', 'Retirer de la liste');
    x.type = 'button';
    x.addEventListener('click', () => notify('char_forget', { name: o.n }));
    head.appendChild(x);
    main.appendChild(head);

    main.appendChild(el('div', 'sub2', 'Équipement'));
    const gear = el('div', 'gearlist');
    (o.gear || []).forEach((g) => {
      const r = el('div', 'gear' + (g.rk ? ' r-' + g.rk : ''));
      const gi = el('span', 'gi');
      if (g.img) {
        const im = document.createElement('img');
        im.src = g.img;
        im.alt = '';
        gi.appendChild(im);
      }
      r.appendChild(gi);
      const gt = el('div', 'gt');
      gt.appendChild(el('b', 'nm', g.name));
      gt.appendChild(el('span', null, [g.type, g.rar, g.lvl ? 'niv. ' + g.lvl : '', g.prism ? 'Prismatique' : ''].filter(Boolean).join(' · ')));
      if (g.up) gt.appendChild(el('span', 'stars', '◆'.repeat(g.up)));
      (g.extras || []).forEach((x2) => {
        const line = el('span', 'gx ' + x2.k);
        line.appendChild(el('b', null, x2.name));
        if (x2.fx) line.appendChild(document.createTextNode(' : ' + x2.fx));
        gt.appendChild(line);
      });
      if (g.inf) {
        const line = el('span', 'gx infu');
        line.appendChild(el('b', null, 'Imprégnation'));
        line.appendChild(document.createTextNode(' : ' + g.inf.name));
        gt.appendChild(line);
        if (g.inf.bonus) {
          const b = el('span', 'gx infb' + (g.inf.on ? '' : ' off'),
            'Bonus (' + g.inf.fac + ') : ' + g.inf.bonus + (g.inf.on ? '' : ' — inactif, faction différente'));
          gt.appendChild(b);
        }
      }
      r.appendChild(gt);
      gear.appendChild(r);
    });
    if (!(o.gear || []).length) gear.appendChild(el('div', 'empty', 'Aucun équipement lu.'));
    main.appendChild(gear);

    if (!o.luck && !o.me) {
      main.appendChild(el('div', 'sub2', 'Chance de butin et statistiques'));
      main.appendChild(el('p', 'note', 'Le jeu ne transmet ces compteurs que pour ton propre '
        + 'personnage : analyse-toi pour les voir.'));
    }
    if (o.luck) {
      main.appendChild(el('div', 'sub2', 'Chance de butin'));
      main.appendChild(el('p', 'note', 'Les compteurs de chance du jeu. Le bonus est lié à '
        + 'l’offrande correspondante du Puits des âmes.'));
      const lk = el('div', 'lucklist');
      o.luck.forEach((l) => {
        const row = el('div', 'luckrow' + (l.on ? ' on' : ''));
        const t = el('div', 'lt');
        t.appendChild(el('b', null, l.t));
        t.appendChild(el('span', null, l.grows
          ? 'Compteur ' + l.n + ' · +' + l.inc + ' par cran · '
            + (l.full ? 'plafond atteint' : l.steps + ' cran' + (l.steps > 1 ? 's' : '') + ' avant le plafond')
          : 'Bonus fixe'));
        row.appendChild(t);
        const v = el('div', 'lv');
        v.appendChild(el('b', null, '+' + l.bonus));
        v.appendChild(el('span', null, 'sur ' + l.cap + ' max'));
        row.appendChild(v);
        row.appendChild(el('span', 'lst' + (l.on ? ' on' : ''), l.on
          ? 'Offrande active' + (l.left != null ? ' · ' + l.left + ' min' : '')
          : 'Pas d’offrande'));
        lk.appendChild(row);
      });
      main.appendChild(lk);
    }
    if ((o.stats || []).length) {
      main.appendChild(el('div', 'sub2', 'Statistiques'));
      const st = el('div', 'cards statcards');
      o.stats.forEach((s) => {
        const c = el('div', 'card');
        c.appendChild(el('div', 't', s.t));
        c.appendChild(el('div', 'v', fmtN(s.v)));
        st.appendChild(c);
      });
      main.appendChild(st);
    }

    if ((o.infusions || []).length) {
      main.appendChild(el('div', 'sub2', 'Imprégnations'));
      const box2 = el('div', 'infulist');
      o.infusions.forEach((s) => {
        const card = el('div', 'infucard');
        const hd = el('div', 'infuhd');
        hd.appendChild(el('b', null, s.name));
        hd.appendChild(el('span', null, [s.fac, s.role].filter(Boolean).join(' · ')));
        hd.appendChild(el('span', 'cnt', s.n + ' pièce' + (s.n > 1 ? 's' : '')));
        card.appendChild(hd);
        s.tiers.forEach((t) => {
          const row = el('div', 'tier' + (t.on ? ' on' : ''));
          row.appendChild(el('span', 'tn', '(' + t.n + ')'));
          row.appendChild(el('span', null, t.txt));
          card.appendChild(row);
        });
        box2.appendChild(card);
      });
      main.appendChild(box2);
    }

    // the action bar, as the game shows it: 1-4, the prayer, A E R G
    if ((o.bar || []).length) {
      main.appendChild(el('div', 'sub2', 'Barre de sorts'));
      const bar = el('div', 'skbar actionbar');
      o.bar.forEach((sk) => {
        if (sk.sep) bar.appendChild(el('span', 'barsep'));
        const cell = el('div', 'barcell' + (sk.key === 'Prière' ? ' prayer' : ''));
        const ic = skillIcon(sk.empty ? null : sk, 'big');
        if (sk.seq) ic.title = 'Prochaine prière : ' + sk.name + ' — séquence : ' + sk.seq.join(' → ');
        cell.appendChild(ic);
        cell.appendChild(el('span', 'bk', sk.key));
        bar.appendChild(cell);
      });
      main.appendChild(bar);
      if ((o.passives || []).length) {
        main.appendChild(el('div', 'sub3', 'Passifs'));
        const pb = el('div', 'skbar');
        o.passives.forEach((sk) => pb.appendChild(skillIcon(sk, 'big')));
        main.appendChild(pb);
      }
    } else if ((o.slots || []).length) {
      main.appendChild(el('div', 'sub2', 'Compétences placées'));
      const bar = el('div', 'skbar');
      o.slots.forEach((sk) => bar.appendChild(skillIcon(sk, 'big')));
      main.appendChild(bar);
    }

    if (o.tree) {
      main.appendChild(el('div', 'sub2', 'Talents (' + o.tree.spent + ' points)'));
      main.appendChild(talentTree(o.tree, o.ranked));
    }

    main.appendChild(el('div', 'sub2', 'Runes (' + (o.runes || []).reduce((a2, r) => a2 + r.runes.length, 0) + ')'));
    const runes = el('div', 'runelist');
    (o.runes || []).forEach((r) => {
      const row = el('div', 'runerow');
      row.appendChild(skillIcon({ id: r.id, name: r.name }));
      row.appendChild(el('b', null, r.name));
      const rl = el('div', 'rl');
      r.runes.forEach((x2) => {
        const c = el('span', 'rune');
        c.appendChild(skillIcon(x2, 'small'));
        c.appendChild(el('span', null, x2.name));
        rl.appendChild(c);
      });
      row.appendChild(rl);
      runes.appendChild(row);
    });
    if (!(o.runes || []).length) runes.appendChild(el('span', 'none', 'aucune'));
    main.appendChild(runes);
  }
  box.appendChild(main);
  return box;
}

function skillIcon(sk, size) {
  const box = el('span', 'skill' + (size ? ' ' + size : '') + (sk ? '' : ' empty'));
  if (!sk) return box;
  box.title = sk.name || sk.id;
  const im = document.createElement('img');
  im.className = 'skic';
  im.dataset.id = sk.id;
  im.alt = '';
  const src = (window.__SKILL__ || {})[sk.id];
  if (src) im.src = src;
  box.appendChild(im);
  return box;
}

/* The talent tree as the game draws it: the root, then tiers 1-4 in three
   branches, each talent with its points (greyed when none). */
function talentTree(t, ranked) {
  const box = el('div', 'ttree');
  const node = (c) => {
    const n = el('span', 'tnode' + (c.pts ? ' on' : '') + (c.gift ? ' gift' : ''));
    n.title = c.name;
    n.appendChild(skillIcon({ id: c.id, name: c.name }));
    if (ranked || !c.pts) n.appendChild(el('b', null, c.pts + '/' + c.max));
    return n;
  };
  const top = el('div', 'trow troot');
  top.appendChild(el('span', 'tcost'));
  const rc = el('div', 'tcell');
  if (t.root) rc.appendChild(node(t.root));
  top.appendChild(rc);
  box.appendChild(top);
  (t.tiers || []).forEach((tier, i) => {
    const row = el('div', 'trow');
    row.appendChild(el('span', 'tcost', t.cost[i] ? t.cost[i] + ' ✦' : ''));
    tier.forEach((cells) => {
      const c = el('div', 'tcell');
      cells.forEach((x) => c.appendChild(node(x)));
      row.appendChild(c);
    });
    box.appendChild(row);
  });
  return box;
}
