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
 *   {k:"list", id, h?, grow?, rows:[{t?, name?, cls?, meta?, btns?}], empty?}
 *   {k:"support", ...}
 * Live and report nodes:
 *   {k:"toolbar", btns}          {k:"cards", items}
 *   {k:"meter", title, heal, rows, empty}
 *   {k:"detail", name, cls, stats, dmg, heal, elements, empty}
 *   {k:"events", rows}           {k:"report", title, when, phases}
 */
'use strict';

const $ = (sel) => document.querySelector(sel);

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
  const row = el('div', 'row');
  if (r.name !== undefined) row.appendChild(el('span', 'name', r.name));
  if (r.cls !== undefined) row.appendChild(el('span', 'cls', r.cls));
  if (r.t !== undefined) row.appendChild(el('span', 'name', r.t));
  if (r.meta !== undefined) row.appendChild(el('span', 'meta', r.meta));
  (r.btns || []).forEach((b) => {
    const btn = el('button', 'rowbtn', b.t);
    btn.addEventListener('click', () => notify(b.id, b.p || {}));
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
    line.appendChild(el('span', 'cls', r.cls));
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
  if (n.cls) name.appendChild(el('span', 'cls', n.cls));
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

function rankTable(rows, rateLabel) {
  const box = el('div', 'tbl');
  const h = el('div', 'rk h');
  ['', 'Joueur', rateLabel, 'Total', 'Part'].forEach((t, i) =>
    h.appendChild(el('span', i > 1 ? 'num' : '', t)));
  box.appendChild(h);
  rows.forEach((r) => {
    const row = el('div', 'rk' + (r.zero ? ' zero' : r.rank <= 3 ? ' top' : ''));
    row.appendChild(el('span', null, r.rank));
    const nm = el('span', 'nm', r.name);
    if (r.cls) nm.appendChild(el('span', 'cl', r.cls));
    row.appendChild(nm);
    row.appendChild(el('span', 'num', r.rate));
    row.appendChild(el('span', 'num', r.total));
    row.appendChild(el('span', 'num', r.pct));
    box.appendChild(row);
  });
  return box;
}

function buildReport(n) {
  const p = el('div', 'panel report');
  const t = el('div', 'rtitle');
  t.appendChild(el('b', null, n.title));
  t.appendChild(el('span', null, n.when));
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
      if (ph.mvp.cls) mn.appendChild(el('small', null, ph.mvp.cls));
      m.appendChild(mn);
      m.appendChild(el('div', 'v', ph.mvp.v));
      podium.appendChild(m);
      if (ph.healer) {
        const h = el('div', 'mvpbox heal');
        h.appendChild(el('span', 'lbl', 'MVP soins'));
        const hn = el('div', 'healer', '✚ ' + ph.healer.name + ' ');
        if (ph.healer.cls) hn.appendChild(el('small', null, ph.healer.cls));
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
    case 'button': return button(n);
    case 'support': {
      const card = el('div', 'support');
      const b = el('button', 'linkcard');
      if (n.img) {
        const im = el('img');
        im.src = n.img;
        im.alt = n.t || '';
        b.appendChild(im);
      } else {
        b.appendChild(el('span', 'wordmark', n.t || 'Soutenir'));
      }
      b.addEventListener('click', () => {
        notify(n.id, {});
        if (n.toast) showToast(n.toast);
      });
      card.appendChild(b);
      (n.paras || []).forEach((p) => card.appendChild(inline(el('p'), p)));
      return card;
    }
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
  if (s.quit !== prev.quit || s.quitArmed !== prev.quitArmed) {
    const q = $('#quit');
    q.textContent = s.quit || 'Quitter';
    q.className = 'btn warn' + (s.quitArmed ? ' armed' : '');
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
