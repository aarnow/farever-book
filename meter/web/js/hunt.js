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
    card.addEventListener('click', () => notify('hunt_open', { id: it.id }));
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

/* ---- one monster's page --------------------------------------------------- */
/* Who it is, where it spawns in the open world (the Map tab's tiles, framed
   on its spawns, each one a pin with its portrait) and what a kill gives. */
function buildHuntMon(n) {
  const box = el('div', 'huntmon');
  const head = el('div', 'hmhead' + (n.kills ? ' seen' : ''));
  const pic = el('span', 'pic');
  pic.appendChild(huntImg(n.uid));
  if (n.tier) pic.appendChild(el('span', 'tier', n.tier));
  head.appendChild(pic);
  const t = el('div', 'hmt');
  t.appendChild(el('div', 'hmname', n.name));
  t.appendChild(el('div', 'fam', [n.fam, n.lvl ? 'niveau ' + n.lvl : '']
    .filter(Boolean).join(' · ')));
  const k = el('div', 'kills');
  k.appendChild(el('b', null, fmtN(n.kills)));
  k.appendChild(el('span', null, n.kills > 1 ? 'kills' : 'kill'));
  const pips = el('span', 'pips');
  for (let i = 0; i < n.max; i++) pips.appendChild(el('i', i < n.rank ? 'on' : ''));
  pips.appendChild(el('span', null, n.rank >= n.max ? 'maîtrisé'
    : n.next ? n.kills + ' / ' + n.next : ''));
  k.appendChild(pips);
  t.appendChild(k);
  head.appendChild(t);
  box.appendChild(head);

  box.appendChild(el('div', 'section', 'Où le trouver'));
  if (!(n.zones || []).length && !(n.spawns || []).length) {
    box.appendChild(el('p', 'note', 'Zone inconnue : il n’apparaît que lors d’événements '
      + '(failles, invasions) ou comme invocation.'));
  } else {
    box.appendChild(huntWhere(n));
  }

  box.appendChild(el('div', 'section', 'Butin'));
  box.appendChild(el('p', 'note', 'Chance par kill. « Famille » : la table commune à toute sa '
    + 'famille ; « Ce monstre » et « Boss » : la sienne, en plus.'));
  const tbl = el('div', 'panel droptable mondrop');
  const hd = el('div', 'drow dhead');
  ['', 'Objet', 'Type', 'Source', 'Chance'].forEach((h) => hd.appendChild(el('span', null, h)));
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
  if (!(n.loot || []).length) tbl.appendChild(el('div', 'empty', 'Aucun butin connu.'));
  box.appendChild(tbl);
  return box;
}

/* The zones in one column, the map in the other. A zone with spawns in the
   open world frames the map on them; a dungeon or rift zone has no map. The
   chosen zone is kept per monster, so a state push doesn't lose it. */
const HUNTMON_ZONE = {};
function huntWhere(n) {
  const wrap = el('div', 'hmwhere');
  const list = el('div', 'hmzlist');
  const count = {};
  (n.spawns || []).forEach((p) => { count[p.z] = (count[p.z] || 0) + 1; });
  const map = (n.spawns || []).length ? huntMiniMap(n) : null;
  const pick = (z) => {
    HUNTMON_ZONE[n.uid] = z;
    list.querySelectorAll('.hmz').forEach((b) => b.classList.toggle('on', b.dataset.z === (z || '')));
    if (map) map.focusZone(z);
  };
  if (map) {
    const all = el('button', 'hmz', 'Toutes les zones');
    all.type = 'button';
    all.dataset.z = '';
    all.appendChild(el('span', 'n', String((n.spawns || []).length)));
    all.addEventListener('click', () => pick(null));
    list.appendChild(all);
  }
  (n.zones || []).forEach((z) => {
    const b = el('button', 'hmz' + (count[z] ? '' : ' off'), z);
    b.type = 'button';
    b.dataset.z = z;
    if (count[z]) {
      b.appendChild(el('span', 'n', String(count[z])));
      b.addEventListener('click', () => pick(z));
    } else {
      b.title = 'Donjon ou faille : pas de carte';
      b.appendChild(el('span', 'n', 'instance'));
    }
    list.appendChild(b);
  });
  if ((n.regions || []).length) list.appendChild(el('div', 'hmreg', (n.regions || []).join(' · ')));
  wrap.appendChild(list);
  wrap.appendChild(map || el('div', 'hmap hmnomap',
    'Pas de carte : il n’apparaît qu’en donjon ou en faille.'));
  const keep = HUNTMON_ZONE[n.uid];
  requestAnimationFrame(() => pick(keep && count[keep] ? keep : null));
  return wrap;
}

/* The open world around the monster's spawns: the Map tab's tiles, fitted to
   its spawns (with some room around a lone one). Not draggable nor zoomable:
   it sits in a scrolling page, and the wheel must scroll the page. The pins
   are placed in screen pixels, so they keep their size. */
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
  const v = { s: 1, x: 0, y: 0 };
  const apply = () => {
    world.style.transform = 'translate(' + v.x + 'px,' + v.y + 'px) scale(' + v.s + ')';
    pins.forEach((d) => {
      d.style.left = (v.x + d.dataset.px * v.s) + 'px';
      d.style.top = (v.y + d.dataset.py * v.s) + 'px';
    });
  };
  // frame the map on one zone's spawns (null: all of them), dimming the rest
  const fit = (zone) => {
    const sel = pins.filter((d) => !zone || d.title === zone);
    pins.forEach((d) => d.classList.toggle('dim', !!zone && d.title !== zone));
    if (!sel.length) return;
    const xs = sel.map((d) => +d.dataset.px);
    const ys = sel.map((d) => +d.dataset.py);
    const span = 420;                       // room around a lone spawn
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
