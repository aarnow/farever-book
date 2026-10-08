/* The Encyclopedia: every item of the game, by category, and the sheet of
   the one picked (app.py _page_encyclopedia, views.encyclopedia_item). The
   list is filtered here; the sheet comes from the meter. */

const ENC = { cat: 'weapons', q: '', type: '', rar: '', top: 0, listKey: '' };
let ENC_NODE = null;
let ENC_ICONS = false;

// an item's icon: sent once the page is opened (menu_host "icons")
function encIcon(id, cls) {
  const im = el('img', 'itic' + (cls ? ' ' + cls : ''));
  im.dataset.id = id;
  im.alt = '';
  im.loading = 'lazy';
  const src = (window.__ITEM__ || {})[id];
  if (src) im.src = src;
  return im;
}

function buildEncyclo(n) {
  ENC_NODE = n;
  if (!ENC_ICONS) { ENC_ICONS = true; notify('encyclo_icons', {}); }
  const box = el('div', 'coll enc');
  renderEncyclo(box, n);
  return box;
}

function rerenderEncyclo() {
  const box = document.querySelector('.coll.enc');
  if (box && ENC_NODE) renderEncyclo(box, ENC_NODE);
}

function encOpen(id) {
  notify('encyclo_open', { id: id });
}

function renderEncyclo(box, n) {
  const listKey = [ENC.cat, ENC.q, ENC.type, ENC.rar].join('|');
  if (ENC.listKey !== listKey) { ENC.listKey = listKey; ENC.top = 0; }
  const keepQ = document.activeElement && document.activeElement.classList.contains('collq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  const cats = n.cats || [];
  if (!cats.some((c) => c.v === ENC.cat) && cats.length) ENC.cat = cats[0].v;
  const items = n.items || [];

  // the categories, each shown by one of its items
  const side = el('aside', 'collside');
  side.appendChild(el('div', 'section', tr('Encyclopédie')));
  side.appendChild(el('p', 'note', tr('Tous les objets du jeu, d’après ses données : '
    + 'ce qu’ils sont et comment les obtenir.')));
  const list_ = el('div', 'collcats');
  cats.forEach((c) => {
    const card = el('button', 'collcat enccat' + (ENC.cat === c.v ? ' on' : ''));
    card.type = 'button';
    const first = items.find((it) => it.c === c.v && it.rk === 'legendary')
      || items.find((it) => it.c === c.v);
    const th = el('span', 'encth');
    if (first) th.appendChild(encIcon(first.id));
    card.appendChild(th);
    const t = el('div', 'tx');
    t.appendChild(el('b', null, c.t));
    t.appendChild(el('span', null, fmtN(c.n)));
    card.appendChild(t);
    card.addEventListener('click', () => {
      if (ENC.cat === c.v) return;
      ENC.cat = c.v; ENC.type = ''; ENC.rar = '';
      rerenderEncyclo();
    });
    list_.appendChild(card);
  });
  side.appendChild(list_);

  // the list: search, the category's types, the rarities
  const list = el('div', 'colllist');
  const tools = el('div', 'colltools');
  const q = el('input', 'collq');
  q.type = 'text';
  q.placeholder = tr('Rechercher un objet');
  q.value = ENC.q;
  q.addEventListener('input', () => { ENC.q = q.value; rerenderEncyclo(); });
  tools.appendChild(q);
  list.appendChild(tools);
  const needle = ENC.q.trim().toLowerCase();
  // a search looks through every category
  const pool = needle ? items : items.filter((it) => it.c === ENC.cat);
  const chips = (opts, cur, set) => {
    const row = el('div', 'collfilters');
    row.addEventListener('wheel', (e) => {
      if (row.scrollWidth <= row.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault();
      row.scrollLeft += e.deltaY;
    }, { passive: false });
    opts.forEach((o) => {
      const b = el('button', 'cfchip' + (o.cls ? ' ' + o.cls : '') + (cur === o.v ? ' on' : ''));
      b.type = 'button';
      if (o.icon) b.appendChild(o.icon);
      b.appendChild(el('span', null, o.t));
      b.addEventListener('click', () => { set(o.v); rerenderEncyclo(); });
      row.appendChild(b);
    });
    return row;
  };
  // the weapons: few of each kind, no filter
  const bare = !needle && ENC.cat === 'weapons';
  if (bare) { ENC.type = ''; ENC.rar = ''; }
  const types = [];
  pool.forEach((it) => { if (it.tk && !types.some((x) => x.v === it.tk)) types.push({ v: it.tk, t: it.type, id: it.id }); });
  // the slots in the character sheet's order
  const SLOTS = ['Head', 'Shoulders', 'Chest', 'Hands', 'Waist', 'Legs', 'Feet', 'Back',
    'GearNeck', 'GearFinger', 'GearTrinket'];
  if (types.every((x) => SLOTS.includes(x.v))) types.sort((a, b) => SLOTS.indexOf(a.v) - SLOTS.indexOf(b.v));
  if (types.length > 1 && !bare) {
    // an armour slot: the build's empty slot art, neutral; another kind:
    // one of its items
    const slotArt = (k) => {
      const src = (window.__SHEET__ || {})['slot_' + k];
      if (!src) return null;
      const im = el('img', 'cfic');
      im.src = src;
      im.alt = '';
      return im;
    };
    list.appendChild(chips([{ v: '', t: tr('Tous') }].concat(types.map((x) => (
      { v: x.v, t: x.t, icon: ((ENC.cat === 'armor' || ENC.cat === 'jewels') && slotArt(x.v.replace(/^Gear/, ''))) || encIcon(x.id, 'cfic') }))),
    ENC.type, (v) => { ENC.type = v; }));
  }
  const rars = [];
  pool.forEach((it) => { if (it.rk && !rars.some((x) => x.v === it.rk)) rars.push({ v: it.rk, t: it.rar }); });
  const order = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
  rars.sort((a, b) => order.indexOf(a.v) - order.indexOf(b.v));
  if (rars.length > 1 && !bare) {
    list.appendChild(chips([{ v: '', t: tr('Toutes les raretés') }].concat(rars.map((x) => (
      { v: x.v, t: x.t, cls: 'r-' + x.v }))), ENC.rar, (v) => { ENC.rar = v; }));
  }
  const shown = pool.filter((it) => (!ENC.type || it.tk === ENC.type)
    && (!ENC.rar || it.rk === ENC.rar)
    && (!needle || it.name.toLowerCase().includes(needle)));
  list.appendChild(el('div', 'collcount', tr(shown.length > 1 ? '{n} objets' : '{n} objet',
    { n: fmtN(shown.length) })));
  const sel = n.sel ? n.sel.id : null;
  const grid = el('div', 'collgrid encgrid');
  shown.forEach((it) => {
    const row = el('button', 'encitem' + (it.rk ? ' r-' + it.rk : '') + (it.id === sel ? ' sel' : ''));
    row.type = 'button';
    const pic = el('span', 'encpic');
    pic.appendChild(encIcon(it.id));
    row.appendChild(pic);
    const t = el('span', 'enct');
    t.appendChild(el('b', 'nm', it.name));
    t.appendChild(el('span', null, [it.type, it.lvl ? tr('niv. {n}', { n: it.lvl }) : ''].filter(Boolean).join(' · ')));
    row.appendChild(t);
    row.addEventListener('click', () => {
      grid.querySelectorAll('.encitem.sel').forEach((x) => x.classList.remove('sel'));
      row.classList.add('sel');
      encOpen(it.id);
    });
    grid.appendChild(row);
  });
  if (!shown.length) grid.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  list.appendChild(grid);

  const main = el('div', 'collmain');
  main.appendChild(side);
  main.appendChild(list);
  main.appendChild(n.sel ? encSheet(n.sel) : el('div', 'collview empty'));
  box.appendChild(main);
  grid.addEventListener('scroll', () => { ENC.top = grid.scrollTop; }, { passive: true });
  if (ENC.top) {
    const top = ENC.top;
    grid.scrollTop = top;
    requestAnimationFrame(() => { grid.scrollTop = top; });
  }
  if (keepQ) {
    const qi = box.querySelector('.collq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}

/* An item named in a sheet (an ingredient, what a recipe makes, what a
   cache holds): its icon and name, a click opens its own sheet. */
function encLink(x, extra) {
  const b = el('button', 'enclink' + (x.rk ? ' r-' + x.rk : ''));
  b.type = 'button';
  const pic = el('span', 'encpic sm');
  if (x.img) { const im = el('img'); im.src = x.img; im.alt = ''; pic.appendChild(im); }
  b.appendChild(pic);
  const t = el('span', 'enct');
  t.appendChild(el('b', 'nm', (x.n > 1 ? x.n + ' × ' : '') + x.name));
  if (extra) t.appendChild(el('span', null, extra));
  b.appendChild(t);
  if (x.id) b.addEventListener('click', () => encOpen(x.id));
  return b;
}

/* The picked item's sheet: what it is, its description, its attributes,
   how to get it, what it is used for, what it holds. */
function encSheet(s) {
  const v = el('div', 'collview encview' + (s.rk ? ' r-' + s.rk : ''));
  const head = el('div', 'enchead');
  const pic = el('span', 'encpic big');
  if (s.img) { const im = el('img'); im.src = s.img; im.alt = ''; pic.appendChild(im); }
  head.appendChild(pic);
  const ht = el('div', 'enct');
  ht.appendChild(el('h3', 'nm', s.name));
  ht.appendChild(el('span', null, [s.type, s.tabs ? '' : s.rar, s.lvl ? tr('niv. {n}', { n: s.lvl }) : ''].filter(Boolean).join(' · ')));
  head.appendChild(ht);
  v.appendChild(head);

  const body = el('div', 'encbody');
  // a weapon: its model, to turn and zoom (the Collection's viewer); its
  // icon until the model is in
  if (s.m3d && m3dSupported()) {
    const stage = el('div', 'cvstage encstage is3d');
    const pic = el('div', 'cvpic own');
    if (s.img) { const im = el('img'); im.src = s.img; im.alt = ''; pic.appendChild(im); }
    stage.appendChild(pic);
    stage.appendChild(m3dCanvas(s.id, (st) => { stage.dataset.st = st; }, { pitch: 0.18 }));
    stage.appendChild(el('div', 'cvwait', tr('Chargement du modèle 3D…')));
    stage.appendChild(el('div', 'cvhint', tr('Glisser pour tourner · molette pour zoomer')));
    body.appendChild(stage);
  }
  const tags = [];
  if (s.fac) tags.push(tr('Faction : {name}', { name: s.fac }));
  if ((s.apt || []).length) tags.push(tr('Classes : {list}', { list: s.apt.join(', ') }));
  if (tags.length) {
    const row = el('div', 'enctags');
    tags.forEach((t) => row.appendChild(el('span', 'enctag', t)));
    body.appendChild(row);
  }
  if (s.desc) body.appendChild(el('p', 'desc it', s.desc));
  // the attributes and the ways to get it, at the rarity of the tab picked
  const tabs = s.tabs || [];
  if (ENC.tabFor !== s.id) {
    ENC.tabFor = s.id;
    // no tabs: the one rarity it comes at
    ENC.rtab = tabs.length ? tabs[0].v
      : (Object.keys(s.levels || s.pieces || {})[0] || '');
    ENC.lvl = s.lvl0 || 0;      // the piece's own level, else the top
  }
  const part = el('div', 'encpart');
  body.appendChild(part);
  const draw = () => {
    part.textContent = '';
    const cur = ENC.rtab;
    const piece = (s.pieces && s.pieces[cur]) || s.piece;
    // a weapon, dropped at any level: its attributes at the level picked
    const levels = s.levels && s.levels[cur];
    if (levels && (!ENC.lvl || ENC.lvl > levels.length)) ENC.lvl = levels.length;
    if (piece) {
      const holder = el('div', 'enctip');
      const paint = () => {
        let g = piece;
        if (levels) {
          const at = levels[ENC.lvl - 1];
          g = Object.assign({}, piece, { il: at.il, stats: at.stats,
            note: tr('Attributs d’une pièce de niveau {n}', { n: ENC.lvl }) });
        }
        const tip = pieceTipEl(g);
        tip.classList.add('inline');
        // its head repeats the sheet's: the attributes alone
        const h = tip.querySelector('.skth');
        if (h) h.remove();
        holder.replaceChildren(tip);
      };
      paint();
      part.appendChild(el('div', 'sub2', tr('Attributs')));
      if (tabs.length) part.appendChild(encTabs(tabs, cur, draw));
      if (levels) part.appendChild(encLevel(levels.length, paint));
      part.appendChild(holder);
    }
    part.appendChild(el('div', 'sub2', tr("Comment l'obtenir")));
    if (tabs.length && !piece) part.appendChild(encTabs(tabs, cur, draw));
    const rows = (s.where || []).filter((r) => !tabs.length || (r.rars || []).includes(cur));
    if (rows.length) {
      const ul = el('div', 'encwhere');
      rows.forEach((r) => ul.appendChild(encWhereRow(r, s.meta, cur)));
      part.appendChild(ul);
    } else {
      part.appendChild(el('p', 'none', (s.where || []).length
        ? tr('Aucune source connue à cette rareté.')
        : tr("Aucune source dans les données du jeu : récompense "
          + "spéciale (événement, précommande…), boutique, ou pas encore disponible.")));
    }
  };
  draw();

  if ((s.uses || []).length) {
    body.appendChild(el('div', 'sub2', tr('Utilisé dans')));
    const box = el('div', 'enclinks');
    s.uses.forEach((u) => box.appendChild(encLink({ id: u.id, name: u.name, img: u.img },
      tr('{n} × pour {job} niv. {lvl}', { n: u.n, job: u.job, lvl: u.lvl }))));
    body.appendChild(box);
  }

  if (s.gives) {
    body.appendChild(el('div', 'sub2', tr('Contenu possible')));
    const facts = [s.gives.rar ? tr('Rareté {rar} au moins', { rar: s.gives.rar.toLowerCase() }) : '',
      s.gives.lvl && s.gives.lvl[0] ? (s.gives.lvl[0] === s.gives.lvl[1]
        ? tr('Niveau {n}', { n: s.gives.lvl[0] })
        : tr('Niveau {a} à {b}', { a: s.gives.lvl[0], b: s.gives.lvl[1] })) : ''].filter(Boolean);
    if (facts.length) body.appendChild(el('p', 'desc', facts.join(' · ')));
    const box = el('div', 'enclinks');
    s.gives.items.forEach((x) => box.appendChild(encLink(x)));
    body.appendChild(box);
  }
  v.appendChild(body);
  return v;
}

/* The level a weapon's attributes are shown at: a slider, the attributes
   following it as it moves. */
function encLevel(max, redraw) {
  const w = el('div', 'bslider enclvl');
  w.appendChild(el('span', null, tr('Niveau')));
  const r = el('input');
  r.type = 'range';
  r.min = 1; r.max = max; r.value = ENC.lvl;
  const out = el('b', null, String(ENC.lvl));
  r.addEventListener('input', () => {
    ENC.lvl = Number(r.value);
    out.textContent = r.value;
    redraw();
  });
  w.appendChild(r);
  w.appendChild(out);
  return w;
}

/* The rarities an item is got at, one tab each (the attributes and the
   sources follow the one picked). */
function encTabs(tabs, cur, redraw) {
  const row = el('div', 'enctabs');
  tabs.forEach((t) => {
    const b = el('button', 'enctab r-' + t.rk + (t.v === cur ? ' on' : ''), t.t);
    b.type = 'button';
    b.addEventListener('click', () => { if (ENC.rtab !== t.v) { ENC.rtab = t.v; redraw(); } });
    row.appendChild(b);
  });
  return row;
}

// a round portrait: a dungeon's boss, a merchant (window.__PORTRAITS__)
function encFace(unit) {
  const f = el('span', 'encface');
  const src = (window.__PORTRAITS__ || {})[unit];
  if (src) { const im = el('img'); im.src = src; im.alt = ''; f.appendChild(im); }
  return f;
}

/* One way to get it: a merchant (portrait, name in game, towns, price, a
   pin opening the map), a dungeon (its boss's portrait first), a cache
   (its icon first), or the source's text. */
function encWhereRow(r, meta, rar) {
  const row = el('div', 'encw');
  if (r.k) row.appendChild(el('span', 'tag', r.k));
  const t = el('div', 'encwt');
  const line = el('div', 'encwl');
  if (r.who) {
    line.appendChild(encFace(r.npc));
    const who = el('div', 'encwho');
    who.appendChild(el('b', 'who', r.who));
    if (r.place) who.appendChild(el('small', null, r.place));
    line.appendChild(who);
  } else if (r.dungeon) {
    // a dungeon: its boss, its name, its difficulties at this rarity
    line.appendChild(encFace(r.boss));
    const d = el('div', 'encwho');
    const diff = (r.diffs || {})[rar] || Object.values(r.diffs || {})[0];
    d.appendChild(el('b', 'who', r.dungeon + (diff ? ' (' + diff + ')' : '')));
    if (r.where) d.appendChild(el('small', null, r.where));
    line.appendChild(d);
  } else {
    // an open world chest: the map's chest marker
    const mk = r.mapIcon && (window.__MAP__ || {})['icon_' + r.mapIcon];
    if (r.boss) line.appendChild(encFace(r.boss));
    else if (mk) {
      const ic = el('span', 'encmk');
      const im = el('img'); im.src = mk; im.alt = ''; ic.appendChild(im);
      line.appendChild(ic);
    } else if (r.img) {
      const ic = el('span', 'encpic sm');
      const im = el('img'); im.src = r.img; im.alt = ''; ic.appendChild(im);
      line.appendChild(ic);
    }
    line.appendChild(el('span', null, r.t));
  }
  if ((r.pins || []).length && meta) {
    const pin = el('button', 'encpin');
    pin.type = 'button';
    pin.title = tr('Voir sur la carte');
    pin.appendChild(svgIcon(PIN_ICON));
    pin.addEventListener('click', () => encMapModal(r.who || r.t, r.place || '', r.pins, meta, r.pinCls));
    line.appendChild(pin);
  }
  t.appendChild(line);
  if ((r.costs || []).length) {
    // the price, each currency by its icon
    const p = el('small', 'enccost');
    p.appendChild(document.createTextNode(tr('Prix :') + ' '));
    r.costs.forEach((c, i) => {
      if (i) p.appendChild(document.createTextNode(' + '));
      const one = el('span', 'enccur');
      if (c.n) one.appendChild(el('b', null, fmtN(c.n)));
      if (c.img) { const im = el('img'); im.src = c.img; im.alt = ''; im.title = c.name; one.appendChild(im); }
      else one.appendChild(document.createTextNode(c.name));
      p.appendChild(one);
    });
    t.appendChild(p);
  } else if (r.cost) t.appendChild(el('small', null, tr('Prix : {cost}', { cost: r.cost })));
  if (r.sub) t.appendChild(el('small', null, r.sub));
  if ((r.parts || []).length) {
    const parts = el('div', 'encparts');
    r.parts.forEach((p) => parts.appendChild(encLink({ id: p.id, name: p.t, img: p.img, n: p.n })));
    t.appendChild(parts);
  }
  row.appendChild(t);
  return row;
}

const PIN_ICON = 'M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z';

/* The world map in a window over the app, framed on the pins: a merchant's
   towns, a rift's spots. Closed by its cross, a click beside or Échap. */
function encMapModal(title, sub, pins, meta, cls) {
  const old = $('#encmapmodal');
  if (old) old.remove();
  const back = el('div', 'modalback');
  back.id = 'encmapmodal';
  const close = () => { back.remove(); document.removeEventListener('keydown', esc, true); };
  const esc = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', esc, true);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  const box = el('div', 'modal riftmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', close);
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, title));
  if (sub) head.appendChild(el('div', 'rmwhere', sub));
  box.appendChild(head);
  const map = huntMiniMap({ meta: meta,
    insts: pins.map((p) => ({ t: p.t, cls: cls || 'merchant', doors: [p] })) });
  map.classList.add('encmodalmap');
  box.appendChild(map);
  back.appendChild(box);
  document.body.appendChild(back);
  mapIcons();
  requestAnimationFrame(() => map.focusZone(null, 900));
}

