/* The Collection page. */

const COLL = { cat: 'mounts', filter: 'all', q: '', open: null, slot: '', cls: '', icat: '', job: '' };
let COLL_NODE = null;
// the categories with 3D models
const COLL_3D = new Set(['mounts', 'gliders', 'pets', 'gears']);
// ...and those that have a skeleton to play their idle animation on
const COLL_ANIM = new Set(['mounts', 'pets']);

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
  // the list keeps its scroll across rebuilds, not across a change of list
  const listKey = [COLL.cat, COLL.filter, COLL.q, COLL.slot, COLL.cls, COLL.icat, COLL.job].join('|');
  if (COLL.listKey !== listKey) { COLL.listKey = listKey; COLL.top = 0; }
  const keepQ = document.activeElement && document.activeElement.classList.contains('collq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  const cats = n.cats || [];
  const total = cats.reduce((a, c) => a + c.n, 0);
  const got = cats.reduce((a, c) => a + c.got, 0);
  const pct = total ? Math.round(got / total * 100) : 0;

  const side = el('aside', 'collside');
  side.appendChild(el('div', 'section', tr('Collection')));
  if (n.sync) side.appendChild(el('p', 'note', n.sync));
  const top = el('div', 'colltotal');
  top.appendChild(el('b', null, got + ' / ' + total));
  top.appendChild(el('span', null, pctTxt(pct)));
  side.appendChild(top);
  const bar_ = el('div', 'collbar');
  const fill = el('i');
  fill.style.width = pct + '%';
  bar_.appendChild(fill);
  side.appendChild(bar_);

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
    t.appendChild(el('small', null,
      tr(c.n - c.got > 1 ? '{n} restants' : '{n} restant', { n: c.n - c.got })));
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
  side.appendChild(cards);

  const list = el('div', 'colllist');
  const tools = el('div', 'colltools');
  const q = el('input', 'collq');
  q.type = 'text';
  q.placeholder = tr('Rechercher par nom');
  q.value = COLL.q;
  q.addEventListener('input', () => { COLL.q = q.value; rerenderCollection(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['all', tr('Tous')], ['missing', tr('Manquants')], ['own', tr('Obtenus')]].forEach(([v, t]) => {
    const b = el('button', COLL.filter === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { COLL.filter = v; rerenderCollection(); });
    seg.appendChild(b);
  });
  tools.appendChild(seg);
  list.appendChild(tools);
  const gears = COLL.cat === 'gears';
  // a filter row: big chips, each led by its icon
  const chips = (opts, cur, set) => {
    const row = el('div', 'collfilters');
    // one line that scrolls sideways: let the wheel do it
    row.addEventListener('wheel', (e) => {
      if (row.scrollWidth <= row.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault();
      row.scrollLeft += e.deltaY;
    }, { passive: false });
    opts.forEach((o) => {
      const b = el('button', 'cfchip' + (cur === o.v ? ' on' : ''));
      b.type = 'button';
      if (o.icon) b.appendChild(o.icon);
      b.appendChild(el('span', null, o.t));
      b.addEventListener('click', () => { set(o.v); rerenderCollection(); });
      row.appendChild(b);
    });
    return row;
  };
  const art = (key) => {
    const src = (window.__SHEET__ || {})[key];
    if (!src) return null;
    const im = el('img', 'cfic');
    im.src = src;
    im.alt = '';
    return im;
  };
  // the recipes: by job, each led by its tool's icon
  const recipes = COLL.cat === 'recipes';
  if (recipes && (n.jobs || []).length) {
    const jobIcon = (src) => {
      if (!src) return null;
      const im = el('img', 'cfic');
      im.src = src;
      im.alt = '';
      return im;
    };
    list.appendChild(chips([{ v: '', t: tr('Tous les métiers') }].concat(n.jobs.map((j) => (
      { v: j.v, t: j.t, icon: jobIcon(j.img) }))), COLL.job, (v) => { COLL.job = v; }));
  }
  if (gears && (n.slots || []).length) {
    list.appendChild(chips([{ v: '', t: tr('Tous') }].concat(n.slots.map((sl) => (
      { v: sl.v, t: sl.t, icon: art('slot_' + sl.v) }))), COLL.slot, (v) => { COLL.slot = v; }));
  }
  if (gears && (n.classes || []).length) {
    list.appendChild(chips([{ v: '', t: tr('Toutes les classes') }].concat(n.classes.map((c) => (
      { v: c.v, t: c.t, icon: classEl(c.t, c.v, 'cfic') }))), COLL.cls, (v) => { COLL.cls = v; }));
  }

  const cat = cats.find((c) => c.v === COLL.cat) || cats[0] || { one: tr('élément') };
  const needle = COLL.q.trim().toLowerCase();
  const shown = (n.items || []).filter((it) => it.c === COLL.cat
    && (COLL.filter === 'all' || (COLL.filter === 'own') === it.own)
    && (!gears || !COLL.slot || it.sl === COLL.slot)
    && (!gears || !COLL.cls || !(it.cls || []).length || it.cls.includes(COLL.cls))
    && (!recipes || !COLL.job || it.job === COLL.job)
    && (!needle || it.name.toLowerCase().includes(needle)));
  const main = el('div', 'collmain');
  main.appendChild(side);
  list.appendChild(el('div', 'collcount', shown.length + ' ' + cat.one + (shown.length > 1 ? 's' : '')));

  // the shown item: the one picked, while the filters keep it, else the first
  let sel = COLL.open && shown.find((it) => it.id === COLL.open);
  if (!sel) sel = shown[0] || null;
  const grid = el('div', 'collgrid');
  // each one numbered by its place in the whole category, as in the game
  const rank = new Map((n.items || []).filter((it) => it.c === COLL.cat)
    .map((it, i) => [it.id, '#' + String(i + 1).padStart(3, '0')]));
  shown.forEach((it) => {
    const card = el('button', 'citem' + (it.own ? ' own' : '') + (it.rk ? ' r-' + it.rk : '')
      + (it === sel ? ' sel' : ''));
    card.type = 'button';
    card.dataset.id = it.id;
    card.title = it.name;
    const pic = el('span', 'pic');
    pic.appendChild(collImg(it.id));
    if (it.own) pic.appendChild(el('span', 'ok', '✓'));
    if (it.count) pic.appendChild(el('span', 'cnt', '×' + fmtN(it.count)));
    card.appendChild(pic);
    card.appendChild(el('span', 'num', rank.get(it.id)));
    if (it.rmax) {
      const pips = el('span', 'cpips');
      for (let i = 0; i < it.rmax; i++) pips.appendChild(el('i', i < it.rank ? 'on' : ''));
      card.appendChild(pips);
    }
    // only the panel and the highlight change: rebuilding the whole page
    // here would cost the list its place
    card.addEventListener('click', () => {
      COLL.open = it.id;
      grid.querySelectorAll('.citem.sel').forEach((x) => x.classList.remove('sel'));
      card.classList.add('sel');
      const old = main.querySelector('.collview');
      const v = collPanel(it, cat, n);
      if (old) old.replaceWith(v); else main.appendChild(v);
      if (document.documentElement.classList.contains('lt-760')) {
        v.scrollIntoView({ block: 'start', behavior: 'smooth' });
      }
    });
    grid.appendChild(card);
  });
  if (!shown.length) grid.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  list.appendChild(grid);
  main.appendChild(list);
  main.appendChild(sel ? collPanel(sel, cat, n) : el('div', 'collview empty'));
  box.appendChild(main);
  grid.addEventListener('scroll', () => { COLL.top = grid.scrollTop; }, { passive: true });
  // once in the page (a fresh node is built before it is placed)
  if (COLL.top) {
    const top = COLL.top;
    grid.scrollTop = top;
    requestAnimationFrame(() => { grid.scrollTop = top; });
  }

  if (keepQ) {
    const qi = box.querySelector('.collq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

/* The panel beside the list: the Encyclopedia's sheet, once the meter has
   sent it (asked for here); the short panel until then. */
function collPanel(it, cat, n) {
  if (n.sel && n.sel.id === it.id) return encSheet(n.sel);
  const want = it.c + ':' + it.id;
  if (COLL.asked !== want) {
    COLL.asked = want;
    notify('coll_open', { cat: it.c, id: it.id });
  }
  return buildCollView(it, cat);
}

/* The short panel: 3D model (else picture) and description. */
function buildCollView(it, cat) {
  const v = el('div', 'collview' + (it.rk ? ' r-' + it.rk : ''));
  const stage = el('div', 'cvstage');
  const pic = el('div', 'cvpic' + (it.own ? ' own' : ''));
  pic.appendChild(collImg(it.id));
  stage.appendChild(pic);
  if (COLL_3D.has(it.c) && view3d()) {
    const wait = el('div', 'cvwait', tr('Chargement du modèle 3D…'));
    const hint = el('div', 'cvhint', tr('Glisser pour tourner · molette pour zoomer'));
    stage.classList.add('is3d');
    // a glider's wings read best from above
    stage.appendChild(m3dCanvas(it.id, (st) => {
      stage.dataset.st = st;
    }, { pitch: it.c === 'gliders' ? 0.6 : 0.18, anim: COLL_ANIM.has(it.c) }));
    stage.appendChild(wait);
    stage.appendChild(hint);
  }
  if (COLL_3D.has(it.c) && m3dSupported()) stage.appendChild(view3dToggle());
  const head = el('div', 'cvhead');
  head.appendChild(el('h3', 'nm', it.name));
  head.appendChild(el('span', 'sub', [cat.t && cat.t.replace(/s$/, ''), it.slot, it.rar].filter(Boolean).join(' · ')));
  head.appendChild(el('span', 'chip' + (it.own ? ' own' : ''), it.own ? tr('✓ Obtenu') : tr('Manquant')));
  stage.appendChild(head);
  v.appendChild(stage);

  const d = el('div', 'cvinfo');
  if (it.rmax) {
    d.appendChild(el('p', 'desc', tr('Obtenu {n} fois · rang {rank} / {max}',
      { n: fmtN(it.count || 0), rank: it.rank || 0, max: it.rmax })
      + (it.uses ? ' · ' + tr(it.uses > 1 ? 'utilisé dans {n} recettes' : 'utilisé dans {n} recette',
        { n: it.uses }) : '')));
  }
  if (it.slot && (it.lvl || it.apt)) {
    d.appendChild(el('p', 'desc', [it.lvl ? tr('Niveau {n}', { n: it.lvl }) : '',
      it.apt ? tr('Classes : {list}', { list: it.apt }) : ''].filter(Boolean).join(' · ')));
  }
  if (it.desc) d.appendChild(el('p', 'desc it', it.desc));
  d.appendChild(el('div', 'sub2', tr("Comment l'obtenir")));
  if (it.src && it.src.length) {
    const ul = el('ul', 'srcs');
    it.src.forEach((s) => ul.appendChild(el('li', null, s)));
    d.appendChild(ul);
  } else {
    d.appendChild(el('p', 'none', tr('Source inconnue. Peut-être indisponible pour le moment.')));
  }
  v.appendChild(d);
  return v;
}
