/* The Collection page. */

const COLL = { cat: 'mounts', filter: 'all', q: '', open: null, slot: '', cls: '' };
let COLL_NODE = null;

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
