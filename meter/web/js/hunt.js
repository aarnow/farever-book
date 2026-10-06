/* The hunting log: monsters, families, and what they drop. */

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

  // a fixed side column; only the list scrolls
  const side = el('aside', 'huntside');
  const main = el('div', 'huntmain');
  box.appendChild(side);
  box.appendChild(main);
  side.appendChild(el('div', 'section', tr('Tableau de chasse')));
  if (n.sync) side.appendChild(el('p', 'note', n.sync));
  const stats = el('div', 'huntstats');
  [[tr('Monstres tués'), fmtN(n.total || 0)],
   [tr('Espèces chassées'), hunted + ' / ' + items.length],
   [tr('Codex maîtrisé'), mastered + ' / ' + items.length]]
    .forEach(([t, v]) => {
      const c = el('div', 'hs');
      c.appendChild(el('span', null, t));
      c.appendChild(el('b', null, v));
      stats.appendChild(c);
    });
  side.appendChild(stats);

  const nav = (cls, opts, cur, set) => {
    const box_ = el('div', 'huntnav ' + cls);
    opts.forEach((o) => {
      const b = el('button', cur === o.v ? 'on' : '');
      b.type = 'button';
      b.appendChild(el('span', null, o.t));
      if (o.n != null) b.appendChild(el('small', null, fmtN(o.n)));
      b.addEventListener('click', () => { set(o.v); rerenderHunt(); });
      box_.appendChild(b);
    });
    return box_;
  };
  side.appendChild(nav('views', [{ v: 'units', t: tr('Monstres') }, { v: 'families', t: tr('Familles') },
    { v: 'farm', t: tr('Montures et planeurs') }, { v: 'boss', t: tr('Boss') }],
  HUNT.view, (v) => { HUNT.view = v; }));
  // the bosses: the monsters' list, only them, each with its dungeon
  const bossView = HUNT.view === 'boss';

  // the list keeps its place across rebuilds, not across a change of list
  const listKey = [HUNT.view, HUNT.reg, HUNT.filter, HUNT.sort, HUNT.q, HUNT.farm].join('|');
  if (HUNT.listKey !== listKey) { HUNT.listKey = listKey; HUNT.top = 0; }
  const keepPlace = (list) => {
    list.addEventListener('scroll', () => { HUNT.top = list.scrollTop; }, { passive: true });
    if (HUNT.top) {
      const top = HUNT.top;
      list.scrollTop = top;
      requestAnimationFrame(() => { list.scrollTop = top; });
    }
  };
  if (HUNT.view === 'families') {
    renderFamilies(main, n);
    keepPlace(main.lastChild);
    return;
  }
  if (HUNT.view === 'farm') {
    renderFarm(main, n);
    keepPlace(main.lastChild);
    return;
  }

  side.appendChild(el('div', 'sub2', tr('Régions')));
  side.appendChild(nav('regs', [{ v: 'all', t: tr('Toutes'), n: items.length }]
    .concat((n.regions || []).map((r) => ({ v: r.v, t: r.t, n: r.n }))),
  HUNT.reg, (v) => { HUNT.reg = v; }));
  box = main;

  const tools = el('div', 'colltools');
  const q = el('input', 'collq huntq');
  q.type = 'text';
  q.placeholder = tr('Rechercher un monstre ou une famille');
  q.value = HUNT.q;
  q.addEventListener('input', () => { HUNT.q = q.value; rerenderHunt(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['all', tr('Tous')], ['none', tr('Jamais tués')], ['doing', tr('En cours')], ['done', tr('Maîtrisés')]]
    .forEach(([v, t]) => {
      const b = el('button', HUNT.filter === v ? 'on' : '', t);
      b.type = 'button';
      b.addEventListener('click', () => { HUNT.filter = v; rerenderHunt(); });
      seg.appendChild(b);
    });
  tools.appendChild(seg);
  const sort = el('div', 'seg');
  [['kills', tr('Plus tués')], ['name', tr('Nom')]].forEach(([v, t]) => {
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
    // its kills are the runs won
    && (!bossView || it.dg)
    && (!needle || it.name.toLowerCase().includes(needle)
        || (it.fam || '').toLowerCase().includes(needle)));
  shown.sort(HUNT.sort === 'name'
    ? (a, b) => a.name.localeCompare(b.name, 'fr')
    : (a, b) => b.kills - a.kills || a.name.localeCompare(b.name, 'fr'));
  box.appendChild(el('div', 'collcount',
    bossView ? tr('{n} boss', { n: shown.length })
      : tr(shown.length > 1 ? '{n} monstres' : '{n} monstre', { n: shown.length })));

  const grid = el('div', 'huntgrid');
  shown.forEach((it) => {
    const card = el('div', 'hitem' + (it.kills ? ' seen' : '') + (it.rank >= it.max ? ' done' : ''));
    card.title = [it.name, it.fam, it.zones].filter(Boolean).join(' — ');
    card.addEventListener('click', () => notify('hunt_open', { id: it.id }));
    const pic = el('span', 'pic');
    pic.appendChild(huntImg(it.id));
    if (it.tier) pic.appendChild(el('span', 'tier', it.tier));
    card.appendChild(pic);
    const body = el('div', 'hb');
    body.appendChild(el('span', 'nm', it.name));
    // a boss: where it waits, rather than its family
    body.appendChild(el('span', 'fam' + (it.dg ? ' dg' : ''), it.dg || it.fam || '—'));
    const k = el('div', 'kills');
    k.appendChild(el('b', null, fmtN(it.kills)));
    k.appendChild(el('span', null, it.kills > 1 ? tr('kills') : tr('kill')));
    body.appendChild(k);
    const pips = el('div', 'pips');
    for (let i = 0; i < it.max; i++) pips.appendChild(el('i', i < it.rank ? 'on' : ''));
    pips.appendChild(el('span', null, it.rank >= it.max ? tr('maîtrisé')
      : it.next ? it.kills + ' / ' + it.next : ''));
    body.appendChild(pips);
    card.appendChild(body);
    grid.appendChild(card);
  });
  if (!shown.length) grid.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  box.appendChild(grid);
  keepPlace(grid);

  if (keepQ) {
    const qi = box.querySelector('.huntq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

/* ---- one monster's page --------------------------------------------------- */
function buildHuntMon(n) {
  const page = el('div', 'huntmon');
  let box = page;
  const head = el('div', 'hmhead' + (n.kills ? ' seen' : ''));
  const pic = el('span', 'pic');
  pic.appendChild(huntImg(n.uid));
  if (n.tier) pic.appendChild(el('span', 'tier', n.tier));
  head.appendChild(pic);
  const t = el('div', 'hmt');
  t.appendChild(el('div', 'hmname', n.name));
  t.appendChild(el('div', 'fam', [n.fam, n.faction, n.lvl ? tr('niveau {n}', { n: n.lvl }) : '']
    .filter(Boolean).join(' · ')));
  const k = el('div', 'kills');
  k.appendChild(el('b', null, fmtN(n.kills)));
  k.appendChild(el('span', null, n.kills > 1 ? tr('kills') : tr('kill')));
  const pips = el('span', 'pips');
  for (let i = 0; i < n.max; i++) pips.appendChild(el('i', i < n.rank ? 'on' : ''));
  pips.appendChild(el('span', null, n.rank >= n.max ? tr('maîtrisé')
    : n.next ? n.kills + ' / ' + n.next : ''));
  k.appendChild(pips);
  t.appendChild(k);
  head.appendChild(t);
  box.appendChild(head);
  const cols = el('div', 'hmcols');
  const first = el('div', 'hmcol hmself');
  const second = el('div', 'hmcol hmplace');
  cols.appendChild(first);
  cols.appendChild(second);
  page.appendChild(cols);
  if (n.desc) first.appendChild(el('p', 'hmdesc', n.desc));
  first.appendChild(huntModel(n));

  second.appendChild(el('div', 'section', tr('Où le trouver')));
  if (n.note) {
    second.appendChild(el('p', 'note', n.note));
  } else {
    second.appendChild(huntWhere(n));
  }
  box = page;

  box.appendChild(el('div', 'section', tr('Butin')));
  box.appendChild(el('p', 'note', tr('Chance par kill. « Famille » : la table commune à toute sa '
    + 'famille, « Ce monstre » et « Boss » : la sienne, en plus.')));
  const tbl = el('div', 'panel droptable mondrop');
  const hd = el('div', 'drow dhead');
  ['', 'Objet', 'Type', 'Source', 'Chance'].forEach((h) => hd.appendChild(el('span', null, h ? tr(h) : '')));
  tbl.appendChild(hd);
  (n.loot || []).forEach((r) => {
    const row = el('div', 'drow' + (r.rk ? ' r-' + r.rk : ''));
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
    row.appendChild(el('span', 'dim', r.src));
    row.appendChild(el('span', 'num', r.chance));
    tbl.appendChild(row);
  });
  if (!(n.loot || []).length) tbl.appendChild(el('div', 'empty', tr('Aucun butin connu.')));
  box.appendChild(tbl);
  return page;
}

/* The monster's 3D model; its portrait while loading or when there is none. */
function huntModel(n) {
  const v = el('div', 'collview hmview');
  const stage = el('div', 'cvstage');
  const pic = el('div', 'cvpic' + (n.kills ? ' own' : ''));
  pic.appendChild(huntImg(n.uid));
  stage.appendChild(pic);
  if (m3dSupported()) {
    stage.classList.add('is3d');
    stage.appendChild(m3dCanvas(n.uid, (st) => { stage.dataset.st = st; }, { anim: true }));
    stage.appendChild(el('div', 'cvwait', tr('Chargement du modèle 3D…')));
    stage.appendChild(el('div', 'cvhint', tr('Glisser pour tourner · molette pour zoomer')));
  }
  v.appendChild(stage);
  return v;
}

/* The zones beside the map (none for dungeon or rift zones). The chosen
   zone is kept per monster so a state push doesn't lose it. */
const HUNTMON_ZONE = {};
function huntWhere(n) {
  const wrap = el('div', 'hmwhere');
  const list = el('div', 'hmzlist');
  const count = {};
  (n.spawns || []).forEach((p) => { count[p.z] = (count[p.z] || 0) + 1; });
  const insts = n.insts || [];
  const doors = insts.reduce((k, i) => k + (i.doors || []).length, 0);
  const map = (n.spawns || []).length || doors ? huntMiniMap(n) : null;
  const pick = (z) => {
    HUNTMON_ZONE[n.uid] = z;
    list.querySelectorAll('.hmz').forEach((b) => b.classList.toggle('on', b.dataset.z === (z || '')));
    if (map) map.focusZone(z);
  };
  const item = (label, z, tag, on) => {
    const b = el('button', 'hmz' + (on ? '' : ' off'), label);
    b.type = 'button';
    b.dataset.z = z;
    b.appendChild(el('span', 'n', tag));
    if (on) b.addEventListener('click', () => pick(z || null));
    list.appendChild(b);
  };
  if (map) item(tr('Tout voir'), '', '', true);
  // the open world's zones with spawns; the instance zones only when no
  // instance says better where they are
  (n.zones || []).forEach((z) => {
    if (count[z]) item(z, z, String(count[z]), true);
    else if (!insts.length) item(z, z, tr('instance'), false);
  });
  // i.kind is the French "Faille" / "Donjon" (compared below): shown through tr()
  insts.forEach((i) => item(i.t.startsWith(i.kind) || i.t.startsWith(tr(i.kind)) ? i.t
    : tr('{kind} : {name}', { kind: tr(i.kind), name: i.t }), i.t,
    (i.doors || []).length ? tr('entrée') : tr('instance'), (i.doors || []).length > 0));
  if ((n.keys || []).length) {
    const ks = el('div', 'hmby');
    ks.appendChild(el('span', 'l', tr('Invoqué à son autel avec')));
    n.keys.forEach((k) => {
      const c = el('span', 'chip key');
      if (k.img) {
        const im = document.createElement('img');
        im.src = k.img;
        im.alt = '';
        c.appendChild(im);
      }
      c.appendChild(document.createTextNode(k.name));
      ks.appendChild(c);
    });
    list.appendChild(ks);
  }
  if ((n.by || []).length) {
    const by = el('div', 'hmby');
    by.appendChild(el('span', 'l', tr('Invoqué par')));
    n.by.forEach((m) => {
      const b = el('button', 'chip', m.name);
      b.type = 'button';
      b.addEventListener('click', () => notify('hunt_open', { id: m.id }));
      by.appendChild(b);
    });
    list.appendChild(by);
  }
  if ((n.regions || []).length) list.appendChild(el('div', 'hmreg', (n.regions || []).join(' · ')));
  wrap.appendChild(list);
  if (map) {
    wrap.appendChild(map);
  } else {
    // nothing to draw: the list alone, and a word why there is no map
    wrap.classList.add('nomap');
    list.appendChild(el('p', 'note', insts.length
      ? tr('Pas de carte : l’entrée de cette instance n’est pas dans le monde ouvert.')
      : tr('Pas de carte : il n’apparaît pas dans le monde ouvert.')));
  }
  const keep = HUNTMON_ZONE[n.uid];
  const known = keep && (count[keep] || insts.some((i) => i.t === keep && (i.doors || []).length));
  requestAnimationFrame(() => pick(known ? keep : null));
  return wrap;
}

/* The Map tab's tiles fitted to the monster's spawns. Neither draggable nor
   zoomable: the wheel must scroll the page. */
function huntMiniMap(n) {
  const m = n.meta || {};
  const size = m.tile_px || 512;
  const vp = el('div', 'hmap');
  const world = el('div', 'mworld');
  (m.tiles || []).forEach((key) => {
    const [tx, ty] = key.split('_').map(Number);
    const im = document.createElement('img');
    im.className = 'mtile';
    im.dataset.key = key;
    im.alt = '';
    im.style.left = (tx - m.tx[0]) * size + 'px';
    im.style.top = (ty - m.ty[0]) * size + 'px';
    im.style.width = im.style.height = (size + 1) + 'px';
    if (window.__MAP__[key]) im.src = window.__MAP__[key];
    world.appendChild(im);
  });
  vp.appendChild(world);
  const pins = (n.spawns || []).map((p) => {
    const d = el('div', 'hpin');
    d.title = p.z;
    d.appendChild(huntImg(n.uid));
    const [px, py] = mapPx(n, p.x, p.y);
    d.dataset.px = px;
    d.dataset.py = py;
    vp.appendChild(d);
    return d;
  });
  (n.insts || []).forEach((i) => (i.doors || []).forEach((p) => {
    const d = el('div', 'hpin hdoor hd-' + (i.cls || (i.kind === 'Faille' ? 'rift' : 'dungeon')));
    d.title = i.t;
    const [px, py] = mapPx(n, p.x, p.y);
    d.dataset.px = px;
    d.dataset.py = py;
    vp.appendChild(d);
    pins.push(d);
  }));
  const v = { s: 1, x: 0, y: 0 };
  const apply = () => {
    world.style.transform = 'translate(' + v.x + 'px,' + v.y + 'px) scale(' + v.s + ')';
    pins.forEach((d) => {
      d.style.left = (v.x + d.dataset.px * v.s) + 'px';
      d.style.top = (v.y + d.dataset.py * v.s) + 'px';
    });
  };
  // frame the map on one zone's spawns (null: all of them), dimming the rest;
  // `span`: the room around a lone spawn
  const fit = (zone, span = 420) => {
    const sel = pins.filter((d) => !zone || d.title === zone);
    pins.forEach((d) => d.classList.toggle('dim', !!zone && d.title !== zone));
    if (!sel.length) return;
    const xs = sel.map((d) => +d.dataset.px);
    const ys = sel.map((d) => +d.dataset.py);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const w = Math.max(x1 - x0, span) * 1.25, h = Math.max(y1 - y0, span) * 1.25;
    const W = vp.clientWidth || 800, H = vp.clientHeight || 380;
    v.s = Math.min(W / w, H / h, 3);
    v.x = W / 2 - (x0 + x1) / 2 * v.s;
    v.y = H / 2 - (y0 + y1) / 2 * v.s;
    apply();
  };
  vp.focusZone = fit;
  return vp;
}

/* The hunting log by family: a family shares its loot table, so its total
   is what counts for a drop any of its species can give. */
function renderFamilies(box, n) {
  const fams = n.families || [];
  box.appendChild(el('p', 'note', tr('Toutes les espèces d’une famille tirent la même table de '
    + 'butin : c’est le total de la famille qui compte pour ses objets rares.')));
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
    k.appendChild(el('span', null, f.kills > 1 ? tr('kills') : tr('kill')));
    t.appendChild(k);
    t.appendChild(el('span', 'fam', tr('{n} / {total} espèces chassées',
      { n: f.hunted, total: f.species })));
    top.appendChild(t);
    card.appendChild(top);
    grid.appendChild(card);
  });
  if (!fams.length) grid.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  box.appendChild(grid);
}

/* The mounts and gliders monsters can drop, with every monster that drops
   each, the kills behind it and the chances. */
function renderFarm(box, n) {
  const all = n.farm || [];
  box.appendChild(el('p', 'note', tr('Les montures et planeurs qui tombent sur des monstres, avec le '
    + 'total de tes kills sur tous ceux qui peuvent les donner (survole un portrait pour son nom). '
    + 'Les chances « déjà eue » supposent un tirage indépendant à chaque kill.')));
  const seg = el('div', 'seg farmseg');
  [['missing', tr('À obtenir')], ['own', tr('Obtenues')], ['all', tr('Toutes')]].forEach(([v, t]) => {
    const b = el('button', HUNT.farm === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { HUNT.farm = v; rerenderHunt(); });
    seg.appendChild(b);
  });
  box.appendChild(seg);
  const shown = all.filter((m) => HUNT.farm === 'all' || (HUNT.farm === 'own') === m.own);
  box.appendChild(el('div', 'collcount',
    tr(shown.length > 1 ? '{n} objets' : '{n} objet', { n: shown.length })));
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
    // m.cat is the French "Monture" / "Planeur" (compared here): shown through tr()
    const glider = m.cat === 'Planeur';
    t.appendChild(el('span', 'fam', tr(nm > 1
      ? (glider ? '{cat} · {n} monstres peuvent le donner' : '{cat} · {n} monstres peuvent la donner')
      : (glider ? '{cat} · {n} monstre peut le donner' : '{cat} · {n} monstre peut la donner'),
    { cat: tr(m.cat), n: nm })));
    head.appendChild(t);
    head.appendChild(el('span', 'fstat' + (m.own ? ' own' : ''), m.own ? tr('✓ obtenue')
      : m.had ? tr('{pct} de chances de l’avoir déjà eue', { pct: m.had }) : tr('aucun kill')));
    card.appendChild(head);
    // one row of numbers: kills summed over every source, chance per kill
    const stats = el('div', 'fstats');
    [[fmtN(m.kills), m.kills > 1 ? tr('kills au total') : tr('kill au total')],
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
      mb.title = tr(x.k > 1 ? '{name} — {n} kills' : '{name} — {n} kill', { name: x.name, n: fmtN(x.k) });
      mb.appendChild(huntImg(x.img));
      mobs.appendChild(mb);
    });
    card.appendChild(mobs);
    list.appendChild(card);
  });
  if (!shown.length) list.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  box.appendChild(list);
}
