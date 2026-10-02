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
