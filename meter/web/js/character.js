/* The Inspecter page: the players (on the server, analysed), then one
   open as a build is: its sheet, talents and runes. */

/* ---- the Character tab ------------------------------------------------- */
function charClass(p) {
  return classEl(p.cls, p.ck, 'cl');
}

let CHAR_NODE = null;
let CHAR_VIEW = 'stuff';        // the open profile's tab
let CHAR_JUMP = false;          // a piece was just picked: bring it in view
const CHAR_VIEWS = [['stuff', 'Équipement'], ['talents', 'Talents'], ['runes', 'Runes']];

function buildCharacter(n) {
  CHAR_NODE = n;
  const o = n.open;
  // an analysed player open: the whole width, as an open build
  if (o) {
    const box = el('div', 'charpage open');
    const back = el('button', 'btn bback', tr('‹  Revenir aux joueurs'));
    back.type = 'button';
    back.addEventListener('click', () => notify('char_open', { name: null }));
    box.appendChild(back);
    const main = el('div', 'charmain');
    charOpen(o, main, box);
    box.appendChild(main);
    return box;
  }
  // else the players: those on the server, those analysed
  const box = el('div', 'charpage');
  const lv = (p) => tr('niv. {n}', { n: p.lvl || '?' });
  const side = el('div', 'charcol');
  const sh = el('div', 'section', tr('Joueurs sur le serveur'));
  if (n.live) sh.appendChild(el('span', 'count', String(n.count || 0)));
  side.appendChild(sh);
  if (!n.live) {
    side.appendChild(el('p', 'note', tr('Lance le jeu pour voir les joueurs du serveur.')));
  } else if (!(n.near || []).length) {
    side.appendChild(el('p', 'note', tr('Personne sur le serveur.')));
  }
  const near = el('div', 'charlist');
  (n.near || []).forEach((p) => {
    const row = el('div', 'charrow' + (p.me ? ' me' : ''));
    const t = el('span', 'cn');
    t.appendChild(charClass(p));
    t.appendChild(el('b', null, p.n));
    t.appendChild(el('span', 'lv', lv(p)));
    row.appendChild(t);
    const b = el('button', 'rowbtn', tr(p.busy ? 'Analyse…' : p.saved ? 'Réanalyser' : 'Analyser'));
    b.type = 'button';
    if (p.busy) b.disabled = true;
    b.addEventListener('click', () => notify('char_analyze', { name: p.n }));
    row.appendChild(b);
    near.appendChild(row);
  });
  side.appendChild(near);
  box.appendChild(side);

  const done = el('div', 'charcol');
  done.appendChild(el('div', 'section', tr('Analysés pendant la session')));
  if (!(n.saved || []).length) done.appendChild(el('p', 'note', tr('Aucun pour l’instant : analyse un joueur.')));
  const saved = el('div', 'charlist');
  (n.saved || []).forEach((p) => {
    const row = el('div', 'charrow click');
    const t = el('span', 'cn');
    t.appendChild(charClass(p));
    t.appendChild(el('b', null, p.n));
    t.appendChild(el('span', 'lv', lv(p)));
    row.appendChild(t);
    row.appendChild(el('span', 'wh', p.when));
    row.addEventListener('click', () => notify('char_open', { name: p.n }));
    saved.appendChild(row);
  });
  done.appendChild(saved);
  box.appendChild(done);
  return box;
}

/* Draw the page again from the last state (a local change: a tab, a piece). */
function redrawCharacter(box) {
  const page = buildCharacter(CHAR_NODE);
  box.replaceWith(page);
  NODES.forEach((v) => { if (v.el === box) v.el = page; });   // core.js keeps the page's nodes
}

/* A piece of the sheet by its slot (WEAPON_KEYS' names for the weapons). */
function sheetPiece(sh, slot) {
  if (slot === 'Weapon1') return (sh.weapons || [])[0] || null;
  if (slot === 'OffhandWeapon') return (sh.weapons || [])[1] || null;
  if (slot === 'Weapon2') return sh.arsenal || null;
  return (sh.left || []).concat(sh.right || []).find((c) => c.slot === slot) || null;
}

/* An analysed player, laid out as a build: the gear around the hero in 3D
   with the spell bar and the passives, the talents, the runes. A click on a
   piece shows it beside the gear. */
function charOpen(o, main, box) {
  const head = el('div', 'charhead');
  const lv = el('div', 'clvl');
  lv.appendChild(el('span', null, tr('Niveau')));
  lv.appendChild(el('b', null, String(o.lvl || '?')));
  head.appendChild(lv);
  const ic = el('span', 'cico');
  ic.appendChild(classEl(o.cls, o.ck, 'big'));
  head.appendChild(ic);
  const t = el('div', 'ct');
  t.appendChild(el('b', null, o.n));
  t.appendChild(el('span', null, tr('{cls} · analysé le {when}', { cls: o.cls, when: o.when })));
  head.appendChild(t);
  const mk = el('button', 'rowbtn', tr('Créer un build'));
  mk.type = 'button';
  mk.title = tr('Reprendre ce personnage dans un nouveau build modifiable');
  mk.addEventListener('click', () => notify('char_to_build', { name: o.n }));
  head.appendChild(mk);
  // one's own character stays: it is kept on disk, not in the session's list
  if (!o.me) {
    const x = el('button', 'rowbtn', tr('Retirer de la liste'));
    x.type = 'button';
    x.addEventListener('click', () => notify('char_forget', { name: o.n }));
    head.appendChild(x);
  }
  main.appendChild(head);

  const tabs = el('div', 'btabs');
  CHAR_VIEWS.forEach(([k, label]) => {
    const b = el('button', 'btab' + (CHAR_VIEW === k ? ' on' : ''), tr(label));
    b.type = 'button';
    b.addEventListener('click', () => {
      if (CHAR_VIEW === k) return;
      CHAR_VIEW = k;
      redrawCharacter(box);
    });
    tabs.appendChild(b);
  });
  main.appendChild(tabs);

  if (CHAR_VIEW === 'talents') {
    if (o.tree) {
      main.appendChild(el('div', 'sub2', tr('Talents ({n} points)', { n: o.tree.spent })));
      main.appendChild(talentTree(o.tree, o.ranked));
    } else {
      main.appendChild(el('p', 'note', tr('Talents indisponibles pour ce profil.')));
    }
    return;
  }
  if (CHAR_VIEW === 'runes') {
    main.appendChild(runeList(o.runes || []));
    return;
  }

  const pick = CHAR_PICK && CHAR_PICK[0] === o.n ? CHAR_PICK[1] : null;
  const piece = pick ? sheetPiece(o.sheet || {}, pick) : null;
  let center = null;
  if (piece && piece.g) {
    center = el('div', 'spanel cpiece');
    const ph = el('div', 'bedhead');
    ph.appendChild(el('b', null, piece.label || piece.g.name));
    const close = el('button', 'hclose', '×');
    close.type = 'button';
    close.title = tr('Revenir au personnage');
    close.addEventListener('click', () => { CHAR_PICK = null; redrawCharacter(box); });
    ph.appendChild(close);
    center.appendChild(ph);
    const gl = el('div', 'gearlist one');
    gl.appendChild(gearRow(piece.g));
    center.appendChild(gl);
    if (CHAR_JUMP) {
      CHAR_JUMP = false;
      requestAnimationFrame(() => center.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
    }
  }
  const bar = el('div', 'spanel bbar');
  bar.appendChild(el('div', 'sptitle', tr('Barre de sorts')));
  if ((o.bar || []).length) bar.appendChild(spellBar(o.bar));
  else if ((o.slots || []).length) bar.appendChild(spellBar(o.slots.map((s) => Object.assign({ key: '' }, s))));
  else bar.appendChild(el('p', 'anote', tr('Barre de sorts indisponible pour ce profil.')));
  main.appendChild(charSheet({ n: o.n, cls: o.cls, ck: o.ck, lvl: o.lvl, sheet: o.sheet, atbs: o.atbs },
    (slot) => {
      CHAR_PICK = pick === slot ? null : [o.n, slot];
      CHAR_JUMP = !!CHAR_PICK;
      redrawCharacter(box);
    },
    { below: bar, arms: buildPassives(o.passives || []), center: center, model: o.model,
      active: pick, hint: tr('Clique sur une pièce pour voir son détail.') }));
  if ((o.infusions || []).length) {
    main.appendChild(el('div', 'sub2', tr('Imprégnations')));
    main.appendChild(infusionCards(o.infusions));
  }
}

/* The runes chosen, each under its skill. */
function runeList(list) {
  const box = el('div', 'bruneswrap');
  const n = list.reduce((a, r) => a + r.runes.length, 0);
  box.appendChild(el('div', 'sub2', tr('Runes ({n})', { n: n })));
  const runes = el('div', 'runelist');
  list.forEach((r) => {
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
  if (!list.length) runes.appendChild(el('span', 'none', tr('aucune')));
  box.appendChild(runes);
  return box;
}

/* ---- the spell bar and the skills' tooltips -------------------------- */
/* A skill's tooltip, as the game shows it: its icon, name and cooldown,
   then its description, or for a weapon skill each rank (R1 from the
   start, R2 and R3 after so many kills with the weapon). */
function skillTipEl(tip) {
  const box = el('div', 'sktip');
  const head = el('div', 'skth');
  head.appendChild(skillIcon({ id: tip.id, name: tip.name }, 'big'));
  const ht = el('div', 'sktt');
  ht.appendChild(el('b', null, tip.name));
  if (tip.cd) ht.appendChild(el('span', null, tip.cd));
  head.appendChild(ht);
  box.appendChild(head);
  if (tip.desc) box.appendChild(el('p', 'skd', tip.desc));
  (tip.rows || []).forEach((r) => {
    const row = el('div', 'skr');
    row.appendChild(el('span', 'skrk', r.r));
    row.appendChild(el('span', 'skwn', r.when));
    row.appendChild(el('span', 'sktx', r.t));
    box.appendChild(row);
  });
  return box;
}

/* A tooltip while the pointer is over `anchor`: `make()` builds it, shown
   over the whole page (no panel clips it) under the pointer and following
   it, flipped left or above when the window ends. Positions are divided by
   the page's zoom (core.js setZoom): the box is placed in unzoomed pixels.
   A click (it picks and redraws) or the pointer leaving hides it. */
function attachFloat(anchor, make) {
  anchor.classList.add('hastip');
  anchor.removeAttribute('title');
  anchor.querySelectorAll('[title]').forEach((x) => x.removeAttribute('title'));
  let shown = null;
  const hide = () => {
    if (shown) { shown.remove(); shown = null; }
    if (TIP_HIDE === hide) TIP_HIDE = null;
  };
  const place = (e) => {
    if (!shown) return;
    const z = parseFloat(document.documentElement.style.zoom) || 1;
    const w = shown.offsetWidth * z, h = shown.offsetHeight * z;
    let x = e.clientX + 16, y = e.clientY + 20;
    if (x + w > window.innerWidth - 8) x = Math.max(8, e.clientX - 16 - w);
    if (y + h > window.innerHeight - 8) y = Math.max(8, e.clientY - 12 - h);
    shown.style.left = (x / z) + 'px';
    shown.style.top = (y / z) + 'px';
  };
  anchor.addEventListener('mouseenter', (e) => {
    if (TIP_HIDE) TIP_HIDE();
    TIP_HIDE = hide;
    TIP_ANCHOR = anchor;
    shown = make();
    document.body.appendChild(shown);
    place(e);
  });
  anchor.addEventListener('mousemove', place);
  anchor.addEventListener('mouseleave', hide);
  anchor.addEventListener('click', hide);
}

/* A skill's tooltip (skillTipEl) on its icon. */
function attachTip(anchor, tip, id) {
  if (tip) attachFloat(anchor, () => skillTipEl(Object.assign({ id: id }, tip)));
}

/* The tooltip shown, and its anchor: a redraw removes the anchor without a
   mouseleave, so the pointer moving off it (or a scroll) hides it. */
let TIP_HIDE = null, TIP_ANCHOR = null;
['mouseover', 'scroll', 'wheel'].forEach((ev) => document.addEventListener(ev, (e) => {
  if (!TIP_HIDE) return;
  if (ev !== 'mouseover' || !TIP_ANCHOR.isConnected || !TIP_ANCHOR.contains(e.target)) TIP_HIDE();
}, true));

/* A piece's tooltip, the skill tooltip's look: its icon and name (in its
   rarity's colour), what it is, then its item level and stats, its
   augments and infusion. */
function pieceTipEl(g) {
  const box = el('div', 'sktip ptip r-' + (g.rk || 'common'));
  const head = el('div', 'skth');
  const ic = el('span', 'ptic');
  if (g.img) { const im = el('img'); im.src = g.img; im.alt = ''; ic.appendChild(im); }
  head.appendChild(ic);
  const ht = el('div', 'sktt');
  ht.appendChild(el('b', 'nm', g.name));
  ht.appendChild(el('span', null, [g.type, g.rar, g.lvl ? tr('niv. {n}', { n: g.lvl }) : '',
    g.prism ? tr('Prismatique') : ''].filter(Boolean).join(' · ')));
  head.appendChild(ht);
  box.appendChild(head);
  if (g.il) box.appendChild(el('div', 'ptil', tr('Niveau d’objet {n}', { n: g.il })));
  (g.stats || []).forEach((x) => {
    const r = el('div', 'ptst');
    r.appendChild(el('span', null, x.t));
    r.appendChild(el('b', null, '+' + fmtN(x.v)));
    box.appendChild(r);
  });
  (g.extras || []).forEach((x) => {
    const r = el('div', 'ptx' + (x.off ? ' off' : ''));
    r.appendChild(el('b', null, x.name));
    if (x.fx) r.appendChild(el('span', null, x.fx));
    box.appendChild(r);
  });
  if (g.inf && g.inf.name) {
    box.appendChild(el('div', 'ptx inf' + (g.inf.on ? '' : ' off'), tr(g.inf.on
      ? 'Imprégnation : {name}' : 'Imprégnation : {name} (bonus inactif)', { name: g.inf.name })));
  }
  return box;
}

/* A piece's tooltip (pieceTipEl) on its icon or its line. */
function attachCard(anchor, g) {
  if (g) attachFloat(anchor, () => pieceTipEl(g));
}

/* One cell of the spell bar: the skill, its key in the corner. */
function barCell(c) {
  const cell = el('div', 'barcell' + (c.big ? ' big' : ''));
  const ic = skillIcon(c.empty || !c.id ? null : { id: c.id, name: c.name }, 'big');
  cell.appendChild(ic);
  if (c.key) cell.appendChild(el('span', 'bk', c.key));
  if (c.seq) cell.title = tr('Prochaine prière : {name} — séquence : {seq}',
    { name: c.name, seq: c.seq.join(' → ') });
  if (c.id) attachTip(cell, c.tip, c.id);
  return cell;
}

/* The action bar as the game draws it: 1-4, then the prayer, then the
   class's four. */
function spellBar(cells) {
  const bar = el('div', 'skbar actionbar gamebar');
  cells.forEach((c) => {
    if (c.sep) bar.appendChild(el('span', 'barsep'));
    bar.appendChild(barCell(c));
  });
  return bar;
}

/* The character sheet, laid out like the game's: the attributes, the gear
   in two columns around the hero, the weapons with their skills. A click on
   a piece shows what it carries underneath. */
let CHAR_PICK = null;           // [player, slot] of the piece shown in detail
const SHEET_ART = window.__SHEET__ || {};
const ATTRS = [['Vitality', 'Vitalité'], ['Strength', 'Force'], ['Dexterity', 'Dextérité'],
               ['Faith', 'Foi'], ['Intelligence', 'Intelligence']];  // stat_ art keys

/* The attributes' colours, the shared build picture's (buildcard.py
   ATB_COLOR), keyed like the sheet's art. */
const ATB_COLOR = { Vitality: '#F26D85', Strength: '#F29A4A', Dexterity: '#7BD88F',
                    Faith: '#F2C94C', Intelligence: '#9D8CF7' };

/* An attribute's icon in its colour: the sheet's art as a mask. */
function atbIcon(k, cls) {
  const key = k === 'Intellect' ? 'Intelligence' : k;
  const i = el('i', 'atbi' + (cls ? ' ' + cls : ''));
  if (SHEET_ART['stat_' + key]) {
    const u = 'url(' + SHEET_ART['stat_' + key] + ')';
    i.style.webkitMaskImage = u;
    i.style.maskImage = u;
  }
  i.style.background = ATB_COLOR[key] || '#fff';
  return i;
}

function artImg(key, cls) {
  const im = el('img', cls || null);
  im.src = SHEET_ART[key];
  im.alt = '';
  return im;
}

/* The infusions worn: one card each, its 2 / 4 / 6 piece tiers lit when
   reached. */
function infusionCards(list) {
  const box = el('div', 'infulist');
  list.forEach((s) => {
    const card = el('div', 'infucard');
    const hd = el('div', 'infuhd');
    hd.appendChild(el('b', null, s.name));
    hd.appendChild(el('span', null, [s.fac, s.role].filter(Boolean).join(' · ')));
    hd.appendChild(el('span', 'cnt', tr(s.n > 1 ? '{n} pièces' : '{n} pièce', { n: s.n })));
    card.appendChild(hd);
    s.tiers.forEach((t) => {
      const row = el('div', 'tier' + (t.on ? ' on' : ''));
      row.appendChild(el('span', 'tn', '(' + t.n + ')'));
      row.appendChild(el('span', null, t.txt));
      card.appendChild(row);
    });
    box.appendChild(card);
  });
  return box;
}

/* The sheet of a build or of an analysed player: the hero in 3D on the
   left, the gear in three lines (armour, jewels, weapons) with what `extra`
   adds under it ({center}: the piece open, {below}: the spell bar, {arms}:
   the passives), the attributes on the right. Every slot, empty or not,
   calls `onSlot` with its name; {active} is the one open, {hint} what a
   click does, {model} the hero's 3D model. */
const WEAPON_KEYS = { w0: 'Weapon1', w1: 'OffhandWeapon', ars: 'Weapon2' };
const CARD_ARMOUR = ['Head', 'Shoulders', 'Chest', 'Back', 'Hands', 'Waist', 'Legs', 'Feet'];
const CARD_JEWELS = ['Neck', 'FingerLeft', 'FingerRight', 'Trinket'];

function charSheet(o, onSlot, extra) {
  const sh = o.sheet || { left: [], right: [], weapons: [], arsenal: null };
  const box = el('div', 'charsheet');
  const wrap = el('div', 'sheet card');

  const attrs = el('div', 'spanel sattrs');
  attrs.appendChild(el('div', 'sptitle', tr('Attributs')));
  const av = o.atbs || null;
  const pv = {};
  ((av && av.primary) || []).forEach((x) => { pv[x.k] = x.v; });
  ATTRS.forEach(([k, label]) => {
    const r = el('div', 'attr');
    const ic = el('span', 'aic');
    if (SHEET_ART['stat_' + k]) ic.appendChild(atbIcon(k));
    r.appendChild(ic);
    r.appendChild(el('span', 'an', tr(label)));
    r.appendChild(el('b', 'av', pv[k === 'Intelligence' ? 'Intellect' : k] || '—'));
    attrs.appendChild(r);
  });
  if (av && (av.secondary || []).length) {
    attrs.appendChild(el('div', 'sptitle sub', tr('Plus de stats')));
    av.secondary.forEach((x) => {
      const r = el('div', 'attr sec');
      r.appendChild(el('span', 'an', x.t));
      const v = el('b', 'av', x.v);
      if (x.sub) v.appendChild(el('small', null, ' (' + x.sub + ')'));
      r.appendChild(v);
      attrs.appendChild(r);
    });
  } else {
    attrs.appendChild(el('p', 'anote', tr('Attributs indisponibles pour ce profil.')));
  }

  const slotEl = (c, key) => {
    const g = c.g;
    const b = el('button', 'slot edit' + (g ? ' r-' + (g.rk || 'common') : ' empty')
      + (extra.active && extra.active === (WEAPON_KEYS[key] || key) ? ' on' : ''));
    b.type = 'button';
    b.title = g ? g.name + (g.rar ? ' (' + g.rar + ')' : '') : tr('{slot} : vide', { slot: c.label });
    if (g && g.img) {
      const im = el('img');
      im.src = g.img;
      im.alt = '';
      b.appendChild(im);
    } else if (!g && SHEET_ART['slot_' + c.icon]) {
      b.appendChild(artImg('slot_' + c.icon, 'ghost'));
    }
    if (g && g.prism) b.appendChild(el('i', 'prism', '✦'));
    // one chip per augment slot, stacked down the top right corner
    ((g && g.chips) || []).forEach((chip, i) => {
      const ch = el('span', 'chip' + (chip.img ? '' : ' none'));
      ch.style.top = (-4 + i * 19) + 'px';
      ch.title = chip.img ? chip.name : tr('Emplacement d’augmentation vide');
      if (chip.img) {
        const im = el('img');
        im.src = chip.img;
        im.alt = '';
        ch.appendChild(im);
      }
      b.appendChild(ch);
    });
    if (g && g.up) b.appendChild(el('i', 'up', '+' + g.up));
    if (g && g.lvl) b.appendChild(el('i', 'lv', 'lv.' + g.lvl));
    if (g && g.inf && g.inf.id) {
      const ic = el('img', 'infic skic' + (g.inf.on ? '' : ' off'));
      ic.dataset.id = g.inf.id;
      ic.alt = '';
      ic.title = tr(g.inf.on ? 'Imprégnation : {name}' : 'Imprégnation : {name} (bonus inactif)',
        { name: g.inf.name });
      const src = (window.__SKILL__ || {})[g.inf.id];
      if (src) ic.src = src;
      b.appendChild(ic);
    }
    b.addEventListener('click', () => onSlot(WEAPON_KEYS[key] || key));
    // what it is, while hovered
    if (g) attachCard(b, g);
    return b;
  };

  // the hero in 3D, its name over its feet
  const hero = el('div', 'shero' + (o.ck ? ' c-' + o.ck : ''));
  const hid = el('div', 'hid');
  if (o.ck) hid.appendChild(classEl(o.cls, o.ck, 'big'));
  hid.appendChild(el('b', 'hn', o.n));
  hid.appendChild(el('span', 'hl', tr('{cls} · niveau {lvl}', { cls: o.cls, lvl: o.lvl || '?' })));
  if (extra.hint) hid.appendChild(el('span', 'hhint', extra.hint));
  if (extra.model && m3dSupported()) {
    hero.classList.add('is3d');
    hero.appendChild(m3dCanvas(extra.model, (st) => { hero.dataset.st = st; },
      { pitch: 0.1, dist: 0.68, lift: -0.08, spin: false, yaw: 1.75 }));
    hero.appendChild(el('div', 'cvwait', tr('Chargement du modèle 3D…')));
    hero.appendChild(el('div', 'cvhint', tr('Glisser pour tourner · molette pour zoomer')));
  }
  hero.appendChild(hid);
  wrap.appendChild(hero);

  const mid = el('div', 'scolumn sgearcol');
  const gear = el('div', 'spanel sgear');
  const cells = {};
  (sh.left || []).concat(sh.right || []).forEach((c) => { cells[c.slot] = c; });
  const line = (title, slots) => {
    gear.appendChild(el('div', 'sptitle', title));
    const row = el('div', 'sgrow');
    slots.forEach((k) => { if (cells[k]) row.appendChild(slotEl(cells[k], k)); });
    gear.appendChild(row);
  };
  line(tr('Équipement'), CARD_ARMOUR);
  line(tr('Accessoires'), CARD_JEWELS);
  // the weapons as the rest: a slot each (their skills are on the bar)
  gear.appendChild(el('div', 'sptitle', tr('Armes')));
  const wrow = el('div', 'sgrow');
  (sh.weapons || []).forEach((w, i) => wrow.appendChild(slotEl({ g: w.g, label: w.label, icon: '' }, 'w' + i)));
  if (sh.arsenal) wrow.appendChild(slotEl({ g: sh.arsenal.g, label: sh.arsenal.label, icon: '' }, 'ars'));
  gear.appendChild(wrow);
  // under the hero's feet, faded over them: name, class, level, pieces worn
  const all15 = Object.values(cells).map((c) => c.g)
    .concat((sh.weapons || []).map((w) => w.g), sh.arsenal ? [sh.arsenal.g] : []);
  const plate = el('div', 'hplate');
  plate.appendChild(el('b', 'pn', o.n));
  const pc = el('div', 'pc');
  if (o.ck) pc.appendChild(classEl(o.cls, o.ck));
  pc.appendChild(el('span', null, o.cls || ''));
  plate.appendChild(pc);
  const chips = el('div', 'pchips');
  chips.appendChild(el('span', null, tr('Niveau {n}', { n: o.lvl || '?' })));
  chips.appendChild(el('span', null, tr('{n} / {all} pièces',
    { n: all15.filter(Boolean).length, all: all15.length })));
  plate.appendChild(chips);
  hero.appendChild(plate);
  mid.appendChild(gear);
  if (extra.center) mid.appendChild(extra.center);
  if (extra.below) mid.appendChild(extra.below);
  if (extra.arms) mid.appendChild(extra.arms);
  wrap.appendChild(mid);
  wrap.appendChild(attrs);
  box.appendChild(wrap);
  return box;
}

/* A small menu under `anchor`; `fill(box, close)` puts its content in. */
function gearPop(anchor, fill) {
  document.querySelectorAll('.gpop').forEach((x) => x.remove());
  const box = el('div', 'gpop');
  const close = () => { box.remove(); document.removeEventListener('mousedown', out, true); };
  const out = (e) => { if (!box.contains(e.target) && e.target !== anchor) close(); };
  fill(box, close);
  // beside the anchor, in its own line: right whatever the page's zoom
  box.style.left = anchor.offsetLeft + 'px';
  box.style.top = (anchor.offsetTop + anchor.offsetHeight + 4) + 'px';
  anchor.parentNode.appendChild(box);
  document.addEventListener('mousedown', out, true);
}

/* A piece. With `ed` (the build's editor), its rarity, level, quality and
   upgrade are set right on it: ed = {rars, rar, onRar, lvl, maxLvl, onLvl,
   up, maxUp, onUp, prism (null when it cannot be), onPrism}. */
function gearRow(g, ed) {
  const r = el('div', 'gear' + (g.rk ? ' r-' + g.rk : '') + (ed ? ' editing' : ''));
  const gi = el('span', 'gi');
  if (g.img) {
    const im = document.createElement('img');
    im.src = g.img;
    im.alt = '';
    gi.appendChild(im);
  }
  r.appendChild(gi);
  const gt = el('div', 'gt');
  if (ed && ed.onWhere) {
    // the name, and how to get the piece
    const nr = el('div', 'nmrow');
    nr.appendChild(el('b', 'nm', g.name));
    const wb = el('button', 'whbtn', tr('Comment l’obtenir'));
    wb.type = 'button';
    wb.addEventListener('click', ed.onWhere);
    nr.appendChild(wb);
    gt.appendChild(nr);
  } else {
    gt.appendChild(el('b', 'nm', g.name));
  }
  if (ed) gt.appendChild(gearEditLine(g, ed));
  else gt.appendChild(el('span', null, [g.type, g.rar, g.lvl ? tr('niv. {n}', { n: g.lvl }) : '', g.prism ? tr('Prismatique') : ''].filter(Boolean).join(' · ')));
  if (ed && ed.maxUp) {
    const pips = el('span', 'stars edit');
    pips.title = tr('Amélioration +{n} — clique pour changer', { n: ed.up || 0 });
    for (let i = 1; i <= ed.maxUp; i++) {
      const pip = el('button', 'pipb' + (i <= (ed.up || 0) ? ' on' : ''));
      pip.type = 'button';
      if (SHEET_ART.upgrade_pip) pip.appendChild(artImg('upgrade_pip', 'pip'));
      else pip.textContent = '◆';
      pip.addEventListener('click', () => ed.onUp(i === ed.up ? i - 1 : i));
      pips.appendChild(pip);
    }
    gt.appendChild(pips);
  } else if (g.up) {
    const pips = el('span', 'stars');
    pips.title = tr('Amélioration {n}', { n: g.up });
    for (let i = 0; i < g.up; i++) {
      if (SHEET_ART.upgrade_pip) pips.appendChild(artImg('upgrade_pip', 'pip'));
      else pips.appendChild(document.createTextNode('◆'));
    }
    gt.appendChild(pips);
  }
  if ((g.stats || []).length) {
    const st = el('div', 'gstats');
    if (g.il) st.appendChild(el('span', 'gil', tr('Niveau d’objet {n}', { n: g.il })));
    if (g.eff) st.appendChild(el('span', 'geff', tr('Efficacité des stats de l’arsenal : {n} %', { n: g.eff })));
    g.stats.forEach((x) => {
      const r = el('div', 'gstat');
      r.appendChild(el('span', null, x.t));
      r.appendChild(el('b', null, '+' + fmtN(x.v)));
      st.appendChild(r);
    });
    // a weapon's upgrade effect, right under its attributes (the editor's
    // too)
    const up = (g.extras || []).find((x) => x.k === 'upgrade');
    if (up) {
      const r = el('div', 'gupg' + (up.off ? ' off' : ''));
      r.appendChild(el('b', null, up.name));
      r.appendChild(document.createTextNode(tr(' : {text}', { text: up.fx })));
      st.appendChild(r);
    }
    gt.appendChild(st);
  }
  if (ed && ed.lines) {          // the editor: its own lines for these
    const box = el('div', 'gchoices');
    ed.lines.forEach((x) => box.appendChild(x));
    gt.appendChild(box);
    r.appendChild(gt);
    return r;
  }
  (g.extras || []).filter((x2) => !(x2.k === 'upgrade' && (g.stats || []).length)).forEach((x2) => {
    const line = el('span', 'gx ' + x2.k + (x2.off ? ' off' : ''));
    line.appendChild(el('b', null, x2.name));
    if (x2.fx) line.appendChild(document.createTextNode(tr(' : {text}', { text: x2.fx })));
    gt.appendChild(line);
  });
  if (!g.inf && g.plan) {
    gt.appendChild(el('span', 'gx infb off', tr('Bonus d’imprégnation : {plan} — inactif sans imprégnation',
      { plan: g.plan })));
  }
  if (g.inf) {
    const line = el('span', 'gx infu');
    line.appendChild(el('b', null, tr('Imprégnation')));
    line.appendChild(document.createTextNode(tr(' : {text}', { text: g.inf.name })));
    gt.appendChild(line);
    if (g.inf.bonus) {
      const bonus = tr('Bonus ({fac}) : {bonus}',
        { fac: g.inf.fac, bonus: g.inf.bonus + (g.inf.val ? ' +' + g.inf.val : '') });
      gt.appendChild(el('span', 'gx infb' + (g.inf.on ? '' : ' off'),
        g.inf.on ? bonus : tr('{text} — inactif, faction différente', { text: bonus })));
    }
  }
  r.appendChild(gt);
  return r;
}

/* "Torse · Légendaire · niv. 25 · Prismatique", each part a control. */
function gearEditLine(g, ed) {
  const line = el('div', 'gedline');
  if (g.type) line.appendChild(el('span', null, g.type));
  const btn = (txt, cls, onClick) => {
    const b = el('button', 'gedit' + (cls ? ' ' + cls : ''), txt);
    b.type = 'button';
    b.addEventListener('click', () => onClick(b));
    line.appendChild(b);
    return b;
  };
  const rar = (ed.rars || []).find((x) => x.v === ed.rar);
  if (!ed.onRar) {
    line.appendChild(el('span', 'gedrar r-' + String(ed.rar || '').toLowerCase(), rar ? rar.t : ed.rar));
  } else btn((rar ? rar.t : ed.rar) + ' ▾', 'r-' + String(ed.rar || '').toLowerCase(), (b) => gearPop(b, (box, close) => {
    (ed.rars || []).forEach((x) => {
      const o = el('button', 'gpopi r-' + x.v.toLowerCase() + (x.v === ed.rar ? ' on' : ''), x.t);
      o.type = 'button';
      o.addEventListener('click', () => { close(); if (x.v !== ed.rar) ed.onRar(x.v); });
      box.appendChild(o);
    });
  }));
  btn(tr('niv. {n}', { n: ed.lvl }) + ' ▾', null, (b) => gearPop(b, (box, close) => {
    box.classList.add('lvl');
    box.appendChild(el('span', 'gpopt', tr('Niveau de la pièce')));
    box.appendChild(slider(1, ed.maxLvl, ed.lvl, (v) => { close(); ed.onLvl(v); }, tr('niv. ')));
  }));
  if (ed.prism !== null && ed.prism !== undefined) {
    const p = btn(tr(ed.prism ? '✦ Prismatique' : '✧ Non prismatique'), 'prism' + (ed.prism ? ' on' : ''),
      () => ed.onPrism(!ed.prism));
    p.title = tr('Prismatique : le bonus d’imprégnation s’applique quelle que soit la faction');
  }
  return line;
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

/* The talent tree as the game draws it: the root, a bar to the three
   branches, then per branch tiers 1-4 in the game's own shapes (a small
   diamond, a large one, a triangle, a diamond), linked top to bottom, the
   points a tier needs in its branch on the left. A shape with no point in
   it is grey, a talent with none too; one given by the gear has a pink ring.
   `onPoint(id, delta)`, when given, makes the tree editable: a click adds a
   point where the rules allow it (c.add), a right click takes one back
   (c.remove). Laid out on a 640 x 700 grid, scaled to its width. */
const TREE_COLS = [205, 380, 555];
const TREE_ROWS = [205, 345, 492, 632];
const TREE_SHAPES = ['small', 'large', 'triangle', 'diamond'];

function talentTree(t, ranked, onPoint) {
  const wrap = el('div', 'ttreew');
  const box = el('div', 'ttree' + (onPoint ? ' edit' : ''));
  wrap.appendChild(box);
  const at = (e, x, y) => { e.style.left = (x / 6.4) + '%'; e.style.top = (y / 7) + '%'; return e; };

  // the bands behind every other tier, and what each tier needs
  [1, 3].forEach((i) => {
    const b = el('div', 'tband');
    b.style.top = ((TREE_ROWS[i] - 70) / 7) + '%';
    box.appendChild(b);
  });
  (t.cost || []).forEach((c, i) => {
    if (c) box.appendChild(at(el('span', 'tcost', c + ' ✦'), 42, TREE_ROWS[i]));
  });

  // the links, behind the shapes
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 640 700');
  svg.setAttribute('class', 'tlinks');
  const [l, c, r] = TREE_COLS;
  const bar = 132;
  const d = [`M${c},72 V${TREE_ROWS[0]}`,
    `M${l},${TREE_ROWS[0]} V${bar + 14} Q${l},${bar} ${l + 14},${bar} H${r - 14} Q${r},${bar} ${r},${bar + 14} V${TREE_ROWS[0]}`]
    .concat(TREE_COLS.map((x) => `M${x},${TREE_ROWS[0]} V${TREE_ROWS[3]}`));
  d.forEach((p) => {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', p);
    svg.appendChild(path);
  });
  box.appendChild(svg);

  const node = (x) => {
    const n = el('span', 'tnode' + (x.pts ? ' on' : '') + (x.gift ? ' gift' : '')
      + (onPoint && x.add ? ' can' : '') + (onPoint && !x.add && !x.pts ? ' locked' : ''));
    n.title = x.name + (onPoint ? '\n' + (x.add ? tr('Clic : +1') : '')
      + (x.remove ? (x.add ? ' · ' : '') + tr('Clic droit : −1') : '') : '');
    if (onPoint) {
      n.addEventListener('click', () => { if (x.add) onPoint(x.id, 1); });
      n.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        if (x.remove) onPoint(x.id, -1);
      });
    }
    n.appendChild(skillIcon({ id: x.id, name: x.name }));
    n.appendChild(el('b', null, x.pts + '/' + x.max));
    return n;
  };
  // where the talents sit in a shape, around its centre
  const spots = (shape, n) => {
    if (shape === 'triangle') {
      return [[[0, -22]], [[-23, -27], [23, -27]], [[-23, -27], [23, -27], [0, 12]]][Math.min(n, 3) - 1]
        || [];
    }
    if (n === 1) return [[0, 0]];
    if (n === 2) return [[-24, 0], [24, 0]];
    return Array.from({ length: n }, (_, i) => [(i - (n - 1) / 2) * 46, 0]);
  };
  const group = (cells, shape, x, y) => {
    const any = cells.some((v) => v.pts);
    const g = el('div', 'tshape ' + shape + (any ? '' : ' dim'));
    const art = shape === 'root' ? 'diamond' : shape;
    if (SHEET_ART['talent_box_' + art]) g.appendChild(artImg('talent_box_' + art, 'tbg'));
    // the grey goes over the shape, inside its rim
    if (!any && SHEET_ART['talent_dim_' + art]) g.appendChild(artImg('talent_dim_' + art, 'tbg'));
    box.appendChild(at(g, x, y));
    spots(shape, cells.length).forEach(([dx, dy], i) => {
      if (cells[i]) box.appendChild(at(node(cells[i]), x + dx, y + dy));
    });
  };
  if (t.root) group([t.root], 'root', c, 72);
  (t.tiers || []).forEach((tier, i) => {
    tier.forEach((cells, b) => {
      if (cells.length) group(cells, TREE_SHAPES[i], TREE_COLS[b], TREE_ROWS[i]);
    });
  });
  return wrap;
}
