/* The Encyclopedia: its subjects first (tiles), then a subject's entries
   (items, monsters, companions, characters) with their filters in the
   aside, and the sheet of the one picked (app.py _page_encyclopedia,
   views.encyclopedia_item). A trail at the top leads back. The list is
   filtered here; the sheet comes from the meter. */

const ENC = { topic: null, q: '', type: '', rar: '', top: 0, listKey: '' };
let ENC_NODE = null;
let ENC_ICONS = false;
const ENC_BATCH = 60;          // cards made at a time
// the subjects where a rarity tells something
const ENC_RARITY = new Set(['equipment', 'mounts', 'gliders', 'augments', 'resources']);
// the character sheet's slots, in its order
const ENC_SLOTS = ['Head', 'Shoulders', 'Chest', 'Hands', 'Waist', 'Legs', 'Feet', 'Back',
  'GearNeck', 'GearFinger', 'GearTrinket'];

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

/* An entry's picture, by where the window keeps it: [namespace, key] —
   item (icons, sent on demand), best (the Codex's), coll (the
   Collection's), port (portraits). */
function encPic(pic, cls) {
  if (!pic) return el('span', 'noimg', '?');
  const [ns, k] = pic;
  if (ns === 'item') return encIcon(k, cls);
  const src = ((ns === 'best' ? window.__BEST__ : ns === 'coll' ? window.__COLL__
    : window.__PORTRAITS__) || {})[k];
  if (!src) return el('span', 'noimg', '?');
  const im = el('img', cls || null);
  im.src = src;
  im.alt = '';
  im.loading = 'lazy';
  return im;
}

function buildEncyclo(n) {
  ENC_NODE = n;
  // the tab clicked again: back on its subjects, filters off
  if (n.reset !== ENC.reset) {
    ENC.reset = n.reset;
    Object.assign(ENC, { topic: null, q: '', type: '', rar: '', top: 0 });
  }
  // an entry opened from a sheet: its subject, filters off, its card in view
  if (n.jump !== ENC.jump) {
    ENC.jump = n.jump;
    const it = n.jump && n.sel && (n.items || []).find((x) => x.id === n.sel.id);
    if (it) {
      Object.assign(ENC, { topic: it.c, q: '', type: '', rar: '', top: 0 });
      ENC.reveal = it.id;
    }
  }
  if (!ENC_ICONS) { ENC_ICONS = true; notify('encyclo_icons', {}); }
  encFaces(n);
  const box = el('div', 'coll enc');
  renderEncyclo(box, n);
  return box;
}

/* The characters the game gives no portrait (those in the hero's body):
   their face photographed off their model, once, in the background, then
   kept by the meter with the portraits (and shown at once here). */
const ENC_FACES = { busy: false, done: {} };
async function encFaces(n) {
  if (ENC_FACES.busy || !m3dSupported()) return;
  const todo = (n.items || []).filter((it) => it.snap && !ENC_FACES.done[it.snap[1]]
    && !(window.__PORTRAITS__ || {})[it.snap[1]]);
  if (!todo.length) return;
  ENC_FACES.busy = true;
  try {
    for (const it of todo) {
      const [model, key] = it.snap;
      ENC_FACES.done[key] = true;
      const url = await m3dPortrait(model, 192);
      if (!url) continue;
      (window.__PORTRAITS__ = window.__PORTRAITS__ || {})[key] = url;
      it.pic = ['port', key];
      notify('npc_portrait', { key: key, data: url });
    }
  } finally { ENC_FACES.busy = false; }
  rerenderEncyclo();
}

function rerenderEncyclo() {
  const box = document.querySelector('.coll.enc');
  if (box && ENC_NODE) renderEncyclo(box, ENC_NODE);
}

// `go`: an item named in a sheet, the Encyclopedia opened on it
function encOpen(id, go) {
  notify('encyclo_open', { id: id, go: !!go });
}

// a subject opened: its first entry shown at once
function encTopic(v, items) {
  Object.assign(ENC, { topic: v, q: '', type: '', rar: '', top: 0 });
  rerenderEncyclo();
  const first = items.find((it) => it.c === v);
  if (first) encOpen(first.id);
}

/* The trail at the top: Encyclopédie › subject › filter › entry, each but
   the last leading back. */
function encTrail(n, topic, typeLabel) {
  const bar = el('nav', 'enctrail');
  const step = (t, go) => {
    if (bar.children.length) bar.appendChild(el('span', 'sep', '›'));
    const b = el(go ? 'button' : 'span', go ? 'step' : 'step cur', t);
    if (go) { b.type = 'button'; b.addEventListener('click', go); }
    bar.appendChild(b);
  };
  const sel = topic && n.sel && (n.items || []).some((x) => x.id === n.sel.id && x.c === topic.v)
    ? n.sel : null;
  step(tr('Encyclopédie'), topic || ENC.q ? () => {
    Object.assign(ENC, { topic: null, q: '', type: '', rar: '', top: 0 });
    rerenderEncyclo();
  } : null);
  if (topic) {
    step(topic.t, (ENC.type || ENC.rar || sel) ? () => {
      Object.assign(ENC, { type: '', rar: '' });
      rerenderEncyclo();
    } : null);
  }
  if (topic && typeLabel) step(typeLabel, sel ? () => {} : null);
  if (sel) step(sel.name, null);
  return bar;
}

/* The first page: a tile per subject, its picture and name. */
function encHome(box, n) {
  const home = el('div', 'enchome');
  const grid = el('div', 'enctiles');
  (n.cats || []).forEach((c) => {
    const t = el('button', 'enctile');
    t.type = 'button';
    const pic = el('span', 'tpic');
    pic.appendChild(encPic(c.pic));
    t.appendChild(pic);
    t.appendChild(el('b', null, c.t));
    t.addEventListener('click', () => encTopic(c.v, n.items || []));
    grid.appendChild(t);
  });
  home.appendChild(grid);
  box.appendChild(home);
}

function renderEncyclo(box, n) {
  const listKey = [ENC.topic, ENC.q, ENC.type, ENC.rar].join('|');
  if (ENC.listKey !== listKey) { ENC.listKey = listKey; ENC.top = 0; }
  const keepQ = document.activeElement && document.activeElement.classList.contains('collq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  const cats = n.cats || [];
  const items = n.items || [];
  const topic = cats.find((c) => c.v === ENC.topic) || null;
  if (!topic) ENC.topic = null;
  const needle = ENC.q.trim().toLowerCase();

  // a subject's kinds, for its aside: label and picture
  const pool = needle ? items : items.filter((it) => topic && it.c === topic.v);
  const types = [];
  if (topic && !needle && topic.v !== 'weapons') {
    pool.forEach((it) => {
      if (it.tk && !types.some((x) => x.v === it.tk)) {
        types.push({ v: it.tk, t: it.tf || it.type, pic: it.tpic || it.pic });
      }
    });
    if (types.every((x) => ENC_SLOTS.includes(x.v))) {
      types.sort((a, b) => ENC_SLOTS.indexOf(a.v) - ENC_SLOTS.indexOf(b.v));
    } else {
      types.sort((a, b) => a.t.localeCompare(b.t));
    }
  }
  const typeLabel = (types.find((x) => x.v === ENC.type) || {}).t || '';
  // the subjects: the page's title, as on the others; within one, the trail
  if (!topic && !needle) box.appendChild(el('div', 'section enctitle', tr('Encyclopédie')));
  else box.appendChild(encTrail(n, topic, typeLabel));

  // the search: through every subject
  const tools = el('div', 'colltools');
  const q = el('input', 'collq');
  q.type = 'text';
  q.placeholder = tr('Rechercher dans l’encyclopédie');
  q.value = ENC.q;
  q.addEventListener('input', () => { ENC.q = q.value; rerenderEncyclo(); });
  tools.appendChild(q);

  // the subjects alone: no search there
  if (!topic && !needle) {
    encHome(box, n);
    return;
  }

  // the aside: back to the subjects, the subject's kinds, its rarities
  const side = el('aside', 'collside encside');
  const back = el('button', 'encback', tr('‹ Tous les sujets'));
  back.type = 'button';
  back.addEventListener('click', () => {
    Object.assign(ENC, { topic: null, q: '', type: '', rar: '', top: 0 });
    rerenderEncyclo();
  });
  side.appendChild(back);
  side.appendChild(el('div', 'section', needle ? tr('Recherche') : topic.t));
  const filter = (label, list, cur, set) => {
    if (list.length < 2) return;
    side.appendChild(el('div', 'encsideh', label));
    const box_ = el('div', 'encfilter');
    list.forEach((o) => {
      const b = el('button', 'encf' + (o.cls ? ' ' + o.cls : '') + (cur === o.v ? ' on' : ''));
      b.type = 'button';
      if (o.icon) b.appendChild(o.icon);
      b.appendChild(el('span', null, o.t));
      if (o.n !== undefined) b.appendChild(el('small', null, fmtN(o.n)));
      b.addEventListener('click', () => { set(o.v); ENC.first = true; rerenderEncyclo(); });
      box_.appendChild(b);
    });
    side.appendChild(box_);
  };
  if (needle) {
    // a search: its results by subject
    filter(tr('Sujets'), [{ v: '', t: tr('Tous'), n: items.filter((it) => it.name.toLowerCase().includes(needle)).length }]
      .concat(cats.map((c) => ({ v: c.v, t: c.t,
        n: items.filter((it) => it.c === c.v && it.name.toLowerCase().includes(needle)).length }))
        .filter((o) => o.n)), ENC.type, (v) => { ENC.type = v; });
  } else {
    const slotArt = (k) => {
      const src = (window.__SHEET__ || {})['slot_' + k.replace(/^Gear/, '')];
      if (!src) return null;
      const im = el('img', 'encfic');
      im.src = src;
      im.alt = '';
      return im;
    };
    filter(tr('Type'), [{ v: '', t: tr('Tous'), n: pool.length }].concat(types.map((x) => ({
      v: x.v, t: x.t, n: pool.filter((it) => it.tk === x.v).length,
      icon: (topic.v === 'equipment' && slotArt(x.v)) || (x.pic ? encPic(x.pic, 'encfic') : null) }))),
    ENC.type, (v) => { ENC.type = v; });
    if (ENC_RARITY.has(topic.v)) {
      const rars = [];
      pool.forEach((it) => { if (it.rk && !rars.some((x) => x.v === it.rk)) rars.push({ v: it.rk, t: it.rar }); });
      const order = ['common', 'uncommon', 'rare', 'epic', 'legendary'];
      rars.sort((a, b) => order.indexOf(a.v) - order.indexOf(b.v));
      filter(tr('Rareté'), [{ v: '', t: tr('Toutes') }].concat(rars.map((x) => (
        { v: x.v, t: x.t, cls: 'r-' + x.v }))), ENC.rar, (v) => { ENC.rar = v; });
    }
  }

  // the list
  const list = el('div', 'colllist');
  list.appendChild(tools);
  const shown = pool.filter((it) => (!ENC.type || (needle ? it.c === ENC.type : it.tk === ENC.type))
    && (!ENC.rar || it.rk === ENC.rar)
    && (!needle || it.name.toLowerCase().includes(needle)));
  // a filter changed: the first entry it keeps shown
  if (ENC.first) {
    ENC.first = false;
    if (shown[0] && !(n.sel && n.sel.id === shown[0].id)) encOpen(shown[0].id);
  }
  const one = topic && !needle ? topic.one : tr('résultat');
  list.appendChild(el('div', 'collcount', fmtN(shown.length) + ' ' + one + (shown.length > 1 && !/s$/.test(one) ? 's' : '')));
  const sel = n.sel ? n.sel.id : null;
  const grid = el('div', 'collgrid encgrid');
  const card = (it) => {
    const row = el('button', 'encitem' + (it.rk ? ' r-' + it.rk : '') + (it.id === sel ? ' sel' : ''));
    row.type = 'button';
    const pic = el('span', 'encpic' + (it.pic && it.pic[0] !== 'item' ? ' face' : ''));
    pic.appendChild(encPic(it.pic));
    row.appendChild(pic);
    const t = el('span', 'enct');
    t.appendChild(el('b', 'nm', it.name));
    // an augment: its effect (the corrupted gifts share one name)
    t.appendChild(el('span', null, it.fx || [it.type, it.lvl ? tr('niv. {n}', { n: it.lvl }) : ''].filter(Boolean).join(' · ')));
    row.appendChild(t);
    row.addEventListener('click', () => {
      grid.querySelectorAll('.encitem.sel').forEach((x) => x.classList.remove('sel'));
      row.classList.add('sel');
      encOpen(it.id);
    });
    return row;
  };
  // the cards made a batch at a time, the next as the end of the list
  // comes into view (546 armours at once cost a moment); as many as were
  // shown before a rebuild, so the scroll comes back to its place
  const end = el('div', 'encmore');
  let made = 0;
  const more = (upTo) => {
    const stop = Math.min(shown.length, upTo || made + ENC_BATCH);
    const frag = document.createDocumentFragment();
    for (; made < stop; made++) frag.appendChild(card(shown[made]));
    grid.insertBefore(frag, end);
    ENC.made = made;
    end.hidden = made >= shown.length;
  };
  grid.appendChild(end);
  more(Math.max(ENC_BATCH, ENC.listKeyMade === listKey ? ENC.made || 0 : 0));
  ENC.listKeyMade = listKey;
  // the item opened from a sheet: its card made, then brought into view
  if (ENC.reveal) {
    const i = shown.findIndex((it) => it.id === ENC.reveal);
    ENC.reveal = null;
    if (i >= made) more(i + 1);
    if (i >= 0) {
      requestAnimationFrame(() => {
        const c = grid.querySelector('.encitem.sel');
        if (c) c.scrollIntoView({ block: 'center' });
      });
    }
  }
  if (!shown.length) grid.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  list.appendChild(grid);

  const main = el('div', 'collmain');
  main.appendChild(side);
  main.appendChild(list);
  // the sheet: the picked entry's, when it is among the subject's
  const showSel = n.sel && (needle || items.some((x) => x.id === n.sel.id && x.c === ENC.topic));
  main.appendChild(showSel ? encSheet(n.sel) : el('div', 'collview empty'));
  box.appendChild(main);
  // the next batch once the list's end is near the window's bottom, the
  // list scrolling on its own or with the page (a narrow window)
  let placed = false;                    // built before it is in the page
  const check = () => {
    if (end.isConnected) placed = true;
    if ((placed && !end.isConnected) || made >= shown.length) {
      document.removeEventListener('scroll', check, true);
      return;
    }
    if (placed && end.getBoundingClientRect().top < window.innerHeight + 400) {
      more();
      requestAnimationFrame(check);      // still near (a tall window): again
    }
  };
  if (made < shown.length) {
    document.addEventListener('scroll', check, { capture: true, passive: true });
    requestAnimationFrame(check);
  }
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

/* A price: each currency's amount and icon. */
function encCost(costs) {
  const p = el('small', 'enccost');
  p.appendChild(document.createTextNode(tr('Prix :') + ' '));
  costs.forEach((c, i) => {
    if (i) p.appendChild(document.createTextNode(' + '));
    const one = el('span', 'enccur');
    if (c.n) one.appendChild(el('b', null, fmtN(c.n)));
    if (c.img) { const im = el('img'); im.src = c.img; im.alt = ''; im.title = c.name; one.appendChild(im); }
    else one.appendChild(document.createTextNode(c.name));
    p.appendChild(one);
  });
  return p;
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
  // a merchant's article: its price and the reputation it asks
  if ((x.costs || []).length) t.appendChild(encCost(x.costs));
  if (x.rep) t.appendChild(el('small', 'encrep', x.rep));
  b.appendChild(t);
  if (x.id) b.addEventListener('click', () => encOpen(x.id, true));
  return b;
}

/* The picked item's sheet: what it is, its description, its attributes,
   how to get it, what it is used for, what it holds. */
// a sheet's picture: its icon, else the Collection's (a companion); `big`:
// the Collection's first, larger than the icon
function encSheetImg(s, big) {
  const coll = (s.coll && (window.__COLL__ || {})[s.coll])
    || (s.best && (window.__BEST__ || {})[s.best])
    || (s.port && (window.__PORTRAITS__ || {})[s.port]);
  // large: the game's own size first (never blown up past it)
  const src = big ? (s.art || coll || s.img) : (s.img || coll);
  if (!src) return null;
  const im = el('img');
  im.src = src;
  im.alt = '';
  return im;
}

function encSheet(s) {
  const v = el('div', 'collview encview' + (s.rk ? ' r-' + s.rk : ''));
  const head = el('div', 'enchead');
  const pic = el('span', 'encpic big');
  const hi = encSheetImg(s);
  if (hi) pic.appendChild(hi);
  head.appendChild(pic);
  const ht = el('div', 'enct');
  ht.appendChild(el('h3', 'nm', s.name));
  ht.appendChild(el('span', null, [s.type, s.tabs ? '' : s.rar, s.lvl ? tr('niv. {n}', { n: s.lvl }) : ''].filter(Boolean).join(' · ')));
  // in the Collection: got or not
  if (s.owned) ht.appendChild(el('span', 'chip' + (s.own ? ' own' : ''), s.own ? tr('✓ Obtenu') : tr('Manquant')));
  head.appendChild(ht);
  v.appendChild(head);

  const body = el('div', 'encbody');
  // its model (gear, mount, glider), to turn and zoom (the Collection's viewer); its
  // icon until the model is in
  // in 2D (Réglages, or the switch): its picture, large
  if (s.m3d && m3dSupported()) {
    const on = view3d();
    const stage = el('div', 'cvstage encstage' + (on ? ' is3d' : ''));
    const pic = el('div', 'cvpic own');
    const si = encSheetImg(s, true);
    if (si) pic.appendChild(si);
    else if (!on) pic.appendChild(el('span', 'encnopic', tr('Pas d’image : aperçu en 3D seulement.')));
    stage.appendChild(pic);
    if (on) {
      stage.appendChild(m3dCanvas(s.model || s.id, (st) => { stage.dataset.st = st; },
        Object.assign({ pitch: 0.18 }, s.m3dView || {})));
      stage.appendChild(el('div', 'cvwait', tr('Chargement du modèle 3D…')));
      stage.appendChild(el('div', 'cvhint', tr('Glisser pour tourner · clic droit pour déplacer · molette pour zoomer')));
    }
    stage.appendChild(view3dToggle());
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
  if (s.fx) {
    // an augment's effect on the piece it is set in
    body.appendChild(el('div', 'sub2', tr('Effet')));
    body.appendChild(el('p', 'desc encfx', s.fx));
  }
  if (s.desc) body.appendChild(el('p', 'desc it', s.desc));
  if (s.makes) {
    // a recipe: what it makes, from what
    body.appendChild(el('div', 'sub2', tr('Fabrique')));
    const box = el('div', 'enclinks');
    box.appendChild(encLink(s.makes));
    body.appendChild(box);
    body.appendChild(el('div', 'sub2', tr('Ingrédients')));
    const parts = el('div', 'encparts');
    (s.makes.parts || []).forEach((p) => parts.appendChild(encLink(p)));
    body.appendChild(parts);
  }
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
    if (s.spawn) return;            // a monster, a character: below
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
        : tr('Source inconnue. Peut-être indisponible pour le moment.')));
    }
  };
  draw();

  // a monster, a character: where they stand, what they drop or sell
  if (s.spawn) {
    body.appendChild(el('div', 'sub2', tr('Où le trouver')));
    if (s.spawn.length) {
      const ul = el('div', 'encwhere');
      s.spawn.forEach((r) => ul.appendChild(encWhereRow(r, s.meta)));
      body.appendChild(ul);
    } else {
      body.appendChild(el('p', 'none', tr('Source inconnue. Peut-être indisponible pour le moment.')));
    }
  }
  [['loot', tr('Butin')], ['sells', tr('Vend')]].forEach(([k, t]) => {
    if (!(s[k] || []).length) return;
    body.appendChild(el('div', 'sub2', t));
    const box = el('div', 'enclinks');
    s[k].forEach((x) => box.appendChild(encLink(x, x.sub)));
    body.appendChild(box);
  });
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
    else if (r.mob) {
      // a monster (or its family): the Codex's picture
      const f = el('span', 'encface mob');
      const src = (window.__BEST__ || {})[r.mob];
      if (src) { const im = el('img'); im.src = src; im.alt = ''; f.appendChild(im); }
      line.appendChild(f);
    } else if (r.ach) {
      // an achievement: its category's crest, on the Succès tab's shield
      const ic = el('span', 'enccrest cat-' + r.ach);
      const src = (window.__COLL__ || {})['achcat_' + r.ach];
      if (src) { const im = el('img'); im.src = src; im.alt = ''; ic.appendChild(im); }
      line.appendChild(ic);
    } else if (mk) {
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
    // a monster's spawns: its own picture on each (the Codex's map)
    pin.addEventListener('click', () => encMapModal(r.title || r.who || r.t,
      r.place || (r.title ? r.t : ''), r.pins, meta, r.pinCls,
      r.mob && !r.pinCls ? r.mob : null, r.face || r.npc,
      r.pet ? (window.__COLL__ || {})[r.pet] : null));
    line.appendChild(pin);
  }
  t.appendChild(line);
  if ((r.costs || []).length) t.appendChild(encCost(r.costs));
  else if (r.cost) t.appendChild(el('small', null, tr('Prix : {cost}', { cost: r.cost })));
  if (r.rep) t.appendChild(el('small', 'encrep', r.rep));
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
function encMapModal(title, sub, pins, meta, cls, mob, face, img) {
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
  // an NPC: its portrait on each place
  // a companion: its picture
  const portrait = img || (face && (window.__PORTRAITS__ || {})[face]);
  const map = (mob || portrait)
    ? huntMiniMap({ meta: meta, uid: mob, pinImg: portrait || null,
      spawns: pins.map((p) => ({ x: p.x, y: p.y, z: p.t })) })
    : huntMiniMap({ meta: meta,
      insts: pins.map((p) => ({ t: p.t, cls: cls || 'merchant', doors: [p] })) });
  map.classList.add('encmodalmap');
  box.appendChild(map);
  back.appendChild(box);
  document.body.appendChild(back);
  mapIcons();
  requestAnimationFrame(() => map.focusZone(null, 900));
}

