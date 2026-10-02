/* The Inspecter page: players, the character sheet, talents and runes. */

/* ---- the Character tab ------------------------------------------------- */
function charClass(p) {
  return classEl(p.cls, p.ck, 'cl');
}

function buildCharacter(n) {
  const box = el('div', 'charpage');
  const side = el('div', 'charside');
  const sh = el('div', 'section', 'Joueurs sur le serveur');
  if (n.live) sh.appendChild(el('span', 'count', String(n.count || 0)));
  side.appendChild(sh);
  if (!n.live) {
    side.appendChild(el('p', 'note', 'Lance le jeu pour voir les joueurs du serveur.'));
  } else if (!(n.near || []).length) {
    side.appendChild(el('p', 'note', 'Personne sur le serveur.'));
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
    const mk = el('button', 'rowbtn', 'Créer un build');
    mk.type = 'button';
    mk.title = 'Reprendre ce personnage dans un nouveau build modifiable';
    mk.addEventListener('click', () => notify('char_to_build', { name: o.n }));
    head.appendChild(mk);
    const x = el('button', 'rowbtn', 'Retirer de la liste');
    x.type = 'button';
    x.addEventListener('click', () => notify('char_forget', { name: o.n }));
    head.appendChild(x);
    main.appendChild(head);

    main.appendChild(charSheet(o));

    if ((o.infusions || []).length) {
      main.appendChild(el('div', 'sub2', 'Imprégnations'));
      main.appendChild(infusionCards(o.infusions));
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

/* The character sheet, laid out like the game's: the attributes, the gear
   in two columns around the hero, the weapons with their skills. A click on
   a piece shows what it carries underneath. */
let CHAR_PICK = null;           // [player, slot] of the piece shown in detail
const SHEET_ART = window.__SHEET__ || {};
const ATTRS = [['Vitality', 'Vitalité'], ['Strength', 'Force'], ['Dexterity', 'Dextérité'],
               ['Faith', 'Foi'], ['Intelligence', 'Intelligence']];  // stat_ art keys

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
    hd.appendChild(el('span', 'cnt', s.n + ' pièce' + (s.n > 1 ? 's' : '')));
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

/* `onSlot`, when given, makes the sheet an editor (the Build tab): every
   slot, empty or not, calls it with the slot's name. `extra` adds
   elements under the hero ({below}) and under the weapons ({arms}). */
const WEAPON_KEYS = { w0: 'Weapon1', w1: 'OffhandWeapon', ars: 'Weapon2' };

function charSheet(o, onSlot, extra) {
  const sh = o.sheet || { left: [], right: [], weapons: [], arsenal: null };
  const box = el('div', 'charsheet');
  const wrap = el('div', 'sheet');

  const attrs = el('div', 'spanel sattrs');
  attrs.appendChild(el('div', 'sptitle', 'Attributs'));
  const av = o.atbs || null;
  const pv = {};
  ((av && av.primary) || []).forEach((x) => { pv[x.k] = x.v; });
  ATTRS.forEach(([k, label]) => {
    const r = el('div', 'attr');
    const ic = el('span', 'aic');
    if (SHEET_ART['stat_' + k]) ic.appendChild(artImg('stat_' + k));
    r.appendChild(ic);
    r.appendChild(el('span', 'an', label));
    r.appendChild(el('b', 'av', pv[k === 'Intelligence' ? 'Intellect' : k] || '—'));
    attrs.appendChild(r);
  });
  if (av && (av.secondary || []).length) {
    attrs.appendChild(el('div', 'sptitle sub', 'Plus de stats'));
    av.secondary.forEach((x) => {
      const r = el('div', 'attr sec');
      r.appendChild(el('span', 'an', x.t));
      const v = el('b', 'av', x.v);
      if (x.sub) v.appendChild(el('small', null, ' (' + x.sub + ')'));
      r.appendChild(v);
      attrs.appendChild(r);
    });
  } else {
    attrs.appendChild(el('p', 'anote', 'Attributs indisponibles pour ce profil.'));
  }
  wrap.appendChild(attrs);

  const pick = CHAR_PICK && CHAR_PICK[0] === o.n ? CHAR_PICK[1] : null;
  const slotEl = (c, key) => {
    const g = c.g;
    const b = el('button', 'slot' + (g ? ' r-' + (g.rk || 'common') : ' empty')
      + (pick === key || (extra && extra.active
        && extra.active === (WEAPON_KEYS[key] || key)) ? ' on' : ''));
    b.type = 'button';
    b.title = g ? g.name + (g.rar ? ' (' + g.rar + ')' : '') : c.label + ' : vide';
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
      ch.title = chip.img ? chip.name : 'Emplacement d’augmentation vide';
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
      ic.title = 'Imprégnation : ' + g.inf.name + (g.inf.on ? '' : ' (bonus inactif)');
      const src = (window.__SKILL__ || {})[g.inf.id];
      if (src) ic.src = src;
      b.appendChild(ic);
    }
    if (onSlot) {
      b.classList.add('edit');
      if (!g) b.title = c.label + ' : choisir une pièce';
      b.addEventListener('click', () => onSlot(WEAPON_KEYS[key] || key));
    } else if (g) b.addEventListener('click', () => {
      CHAR_PICK = pick === key ? null : [o.n, key];
      box.replaceWith(charSheet(o));
    });
    return b;
  };

  const doll = el('div', 'spanel sdoll');
  const colL = el('div', 'scol');
  (sh.left || []).forEach((c) => colL.appendChild(slotEl(c, c.slot)));
  const colR = el('div', 'scol');
  (sh.right || []).forEach((c) => colR.appendChild(slotEl(c, c.slot)));
  // the hero, or the picked piece in its place
  const all = {};
  (sh.left || []).concat(sh.right || []).forEach((c) => { all[c.slot] = c.g; });
  (sh.weapons || []).forEach((w, i) => { all['w' + i] = w.g; });
  if (sh.arsenal) all.ars = sh.arsenal.g;
  const picked = pick ? all[pick] : null;
  const center = extra && extra.center;
  const hero = el('div', 'shero' + (o.ck ? ' c-' + o.ck : '') + (picked ? ' detail' : '')
    + (center ? ' editing' : ''));
  if (center) {
    hero.appendChild(center);
  } else if (picked) {
    const x = el('button', 'hclose', '×');
    x.type = 'button';
    x.title = 'Revenir au personnage';
    x.addEventListener('click', () => { CHAR_PICK = null; box.replaceWith(charSheet(o)); });
    hero.appendChild(x);
    hero.appendChild(gearRow(picked));
  } else {
    const hid = el('div', 'hid');
    if (o.ck) hid.appendChild(classEl(o.cls, o.ck, 'big'));
    hid.appendChild(el('b', 'hn', o.n));
    hid.appendChild(el('span', 'hl', o.cls + ' · niveau ' + (o.lvl || '?')));
    hid.appendChild(el('span', 'hhint', onSlot
      ? 'Clique sur un emplacement pour choisir ou régler une pièce.'
      : 'Clique sur une pièce pour voir son détail ici.'));
    hero.appendChild(hid);
  }
  doll.appendChild(colL);
  doll.appendChild(hero);
  doll.appendChild(colR);
  if (extra && extra.below) {
    const col = el('div', 'scolumn');
    col.appendChild(doll);
    col.appendChild(extra.below);
    wrap.appendChild(col);
  } else {
    wrap.appendChild(doll);
  }

  const arms = el('div', 'spanel sarms');
  const weaponCard = (w, key) => {
    const c = el('div', 'wcard');
    const top = el('div', 'wtop');
    top.appendChild(slotEl({ g: w.g, label: w.label, icon: '' }, key));
    const t = el('div', 'wt');
    t.appendChild(el('b', null, w.label));
    t.appendChild(el('span', null, w.g ? w.g.name : 'vide'));
    top.appendChild(t);
    c.appendChild(top);
    if ((w.skills || []).length) {
      const sk = el('div', 'wsk');
      w.skills.forEach((x) => sk.appendChild(skillIcon(x)));
      c.appendChild(sk);
    }
    return c;
  };
  arms.appendChild(el('div', 'sptitle', 'Armes'));
  (sh.weapons || []).forEach((w, i) => arms.appendChild(weaponCard(w, 'w' + i)));
  if (sh.arsenal) {
    arms.appendChild(el('div', 'sptitle', 'Arsenal'));
    arms.appendChild(weaponCard(sh.arsenal, 'ars'));
  }
  if (extra && extra.arms) {
    const col = el('div', 'scolumn');
    col.appendChild(arms);
    col.appendChild(extra.arms);
    wrap.appendChild(col);
  } else {
    wrap.appendChild(arms);
  }
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
  gt.appendChild(el('b', 'nm', g.name));
  if (ed) gt.appendChild(gearEditLine(g, ed));
  else gt.appendChild(el('span', null, [g.type, g.rar, g.lvl ? 'niv. ' + g.lvl : '', g.prism ? 'Prismatique' : ''].filter(Boolean).join(' · ')));
  if (ed && ed.maxUp) {
    const pips = el('span', 'stars edit');
    pips.title = 'Amélioration +' + (ed.up || 0) + ' — clique pour changer';
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
    pips.title = 'Amélioration ' + g.up;
    for (let i = 0; i < g.up; i++) {
      if (SHEET_ART.upgrade_pip) pips.appendChild(artImg('upgrade_pip', 'pip'));
      else pips.appendChild(document.createTextNode('◆'));
    }
    gt.appendChild(pips);
  }
  if ((g.stats || []).length) {
    const st = el('div', 'gstats');
    if (g.il) st.appendChild(el('span', 'gil', 'Niveau d’objet ' + g.il));
    if (g.eff) st.appendChild(el('span', 'geff', 'Efficacité des stats de l’arsenal : ' + g.eff + ' %'));
    g.stats.forEach((x) => {
      const r = el('div', 'gstat');
      r.appendChild(el('span', null, x.t));
      r.appendChild(el('b', null, '+' + fmtN(x.v)));
      st.appendChild(r);
    });
    gt.appendChild(st);
  }
  if (ed && ed.lines) {          // the editor: its own lines for these
    const box = el('div', 'gchoices');
    ed.lines.forEach((x) => box.appendChild(x));
    gt.appendChild(box);
    r.appendChild(gt);
    return r;
  }
  (g.extras || []).forEach((x2) => {
    const line = el('span', 'gx ' + x2.k);
    line.appendChild(el('b', null, x2.name));
    if (x2.fx) line.appendChild(document.createTextNode(' : ' + x2.fx));
    gt.appendChild(line);
  });
  if (!g.inf && g.plan) {
    gt.appendChild(el('span', 'gx infb off', 'Bonus d’imprégnation : ' + g.plan
      + ' — inactif sans imprégnation'));
  }
  if (g.inf) {
    const line = el('span', 'gx infu');
    line.appendChild(el('b', null, 'Imprégnation'));
    line.appendChild(document.createTextNode(' : ' + g.inf.name));
    gt.appendChild(line);
    if (g.inf.bonus) {
      gt.appendChild(el('span', 'gx infb' + (g.inf.on ? '' : ' off'),
        'Bonus (' + g.inf.fac + ') : ' + g.inf.bonus + (g.inf.val ? ' +' + g.inf.val : '')
        + (g.inf.on ? '' : ' — inactif, faction différente')));
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
  btn((rar ? rar.t : ed.rar) + ' ▾', 'r-' + String(ed.rar || '').toLowerCase(), (b) => gearPop(b, (box, close) => {
    (ed.rars || []).forEach((x) => {
      const o = el('button', 'gpopi r-' + x.v.toLowerCase() + (x.v === ed.rar ? ' on' : ''), x.t);
      o.type = 'button';
      o.addEventListener('click', () => { close(); if (x.v !== ed.rar) ed.onRar(x.v); });
      box.appendChild(o);
    });
  }));
  btn('niv. ' + ed.lvl + ' ▾', null, (b) => gearPop(b, (box, close) => {
    box.classList.add('lvl');
    box.appendChild(el('span', 'gpopt', 'Niveau de la pièce'));
    box.appendChild(slider(1, ed.maxLvl, ed.lvl, (v) => { close(); ed.onLvl(v); }, 'niv. '));
  }));
  if (ed.prism !== null && ed.prism !== undefined) {
    const p = btn(ed.prism ? '✦ Prismatique' : '✧ Non prismatique', 'prism' + (ed.prism ? ' on' : ''),
      () => ed.onPrism(!ed.prism));
    p.title = 'Prismatique : le bonus d’imprégnation s’applique quelle que soit la faction';
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
    n.title = x.name + (onPoint ? '\n' + (x.add ? 'Clic : +1' : '')
      + (x.remove ? (x.add ? ' · ' : '') + 'Clic droit : −1' : '') : '');
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
