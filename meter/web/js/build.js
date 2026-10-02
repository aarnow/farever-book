/* The Build tab: the saved builds, and the one being edited — class and
   level, the gear on the character sheet (a slot opens the piece editor),
   the talent tree, the skills. Everything is computed by the meter
   (meter/buildtab.py); this only draws it and sends the changes. */

let BUILD_NODE = null;
let BUILD_Q = '';               // the piece editor's search
// the piece editor's filters: stats the piece must all give, its family
const BUILD_F = { slot: null, stats: new Set(), fac: '' };

function buildBuild(n) {
  BUILD_NODE = n;
  const box = el('div', 'buildpage');
  const o = n.open;
  if (!o) return buildList(n, box);
  const back = el('button', 'btn bback', '‹  Revenir aux builds');
  back.type = 'button';
  back.addEventListener('click', () => notify('build_close', {}));
  box.appendChild(back);

  const main = el('div', 'charmain');
  main.appendChild(buildHead(o));
  main.appendChild(charSheet({ n: o.name, cls: o.clsFr, ck: o.ck, lvl: o.lvl,
                               sheet: o.sheet, atbs: o.atbs },
                             (slot) => notify('build_slot', { slot: slot }),
                             { below: buildBar(o.bar || []), arms: buildPassives(o.passives || []),
                               center: o.editor ? editorPanel(o.editor) : null,
                               active: o.editor ? o.editor.slot : null }));
  if ((o.infusions || []).length) {
    main.appendChild(el('div', 'sub2', 'Imprégnations'));
    main.appendChild(infusionCards(o.infusions));
  }

  const th = el('div', 'sub2 bsub');
  th.appendChild(document.createTextNode('Talents — ' + o.points.used + ' / '
    + o.points.total + ' points'));
  if (o.points.used) {
    const r = el('button', 'rowbtn', 'Tout retirer');
    r.type = 'button';
    r.addEventListener('click', () => notify('build_talents_reset', {}));
    th.appendChild(r);
  }
  main.appendChild(th);
  if (!o.points.total) {
    main.appendChild(el('p', 'note', 'Les talents se débloquent au niveau ' + o.points.from + '.'));
  } else {
    main.appendChild(el('p', 'note', 'Clic sur un talent : +1 point. Clic droit : −1. Un palier '
      + 's’ouvre avec les points dépensés plus haut dans sa branche (chiffre à gauche).'));
  }
  if (o.tree) {
    main.appendChild(talentTree(o.tree, true,
      (id, delta) => notify('build_talent', { id: id, delta: delta })));
  }

  if (o.sim) main.appendChild(buildSim(o.sim));
  box.appendChild(main);
  return box;
}

/* The simulation: what the build deals and heals against a target whose
   armour is set in %, and what an incoming hit leaves. */
function buildSim(s) {
  const wrap = el('div', 'bsim');
  wrap.appendChild(el('div', 'sub2', 'Simulation'));
  const ctl = el('div', 'bsimctl');
  const num = (label, v, field, min, max, step) => {
    const f = el('label', 'bsimf');
    f.appendChild(el('span', null, label));
    const i = el('input');
    i.type = 'number'; i.min = min; i.max = max; i.step = step || 1; i.value = v;
    i.addEventListener('change', () => notify('build_sim', { field: field, value: Number(i.value) }));
    f.appendChild(i);
    return f;
  };
  const arm = el('div', 'bsimf wide');
  arm.appendChild(el('span', null, 'Armure de la cible'));
  arm.appendChild(slider(0, 80, s.armor, (v) => notify('build_sim', { field: 'armor', value: v }), '', ' %'));
  ctl.appendChild(arm);
  ctl.appendChild(num('Niveau de l’ennemi', s.enemy, 'enemy', 1, s.maxLvl));
  ctl.appendChild(num('Coup reçu', s.hit, 'hit', 0, 100000, 10));
  wrap.appendChild(ctl);
  wrap.appendChild(el('p', 'note', 'Critique ' + s.crit + ' · bonus critique ' + s.critMult
    + ' (CC : coup critique). La moyenne tient compte des chances de critique. Les effets propres '
    + 'à certains sorts (bonus conditionnels, cumuls, effets spéciaux codés dans le jeu) ne sont pas simulés.'));

  const out = el('div', 'bsimgrid');
  const t = el('div', 'bsimcards');
  if (!(s.skills || []).length) {
    t.appendChild(el('p', 'anote', 'Équipe une arme ou place des compétences.'));
  }
  (s.skills || []).forEach((sk) => t.appendChild(spellCard(sk)));
  out.appendChild(t);
  const d = s.defense;
  const c = el('div', 'spanel bsimdef');
  c.appendChild(el('div', 'sptitle', 'Ce que tu reçois'));
  const line = (label, v, sub) => {
    const r = el('div', 'attr sec');
    r.appendChild(el('span', 'an', label));
    const b = el('b', 'av', v);
    if (sub) b.appendChild(el('small', null, ' (' + sub + ')'));
    r.appendChild(b);
    c.appendChild(r);
  };
  line('Coup reçu', d.hit);
  line('Physique', d.phys, 'armure −' + d.physMit);
  line('Magique', d.magic, 'réduction −' + d.magicMit);
  line('Dégâts reçus (ferveur)', d.taken);
  line('Points de vie', d.hp);
  line('Coups physiques encaissés', d.hitsPhys);
  out.appendChild(c);
  wrap.appendChild(out);
  if ((s.runes || []).length) wrap.appendChild(runesSection(s.runes));
  return wrap;
}

/* A spell as a card: its icon and name, each effect normal and critical,
   then its cooldown, range and average. */
function spellCard(sk) {
  const card = el('div', 'spell');
  const head = el('div', 'sphead');
  head.appendChild(skillIcon({ id: sk.id, name: sk.name }, 'big'));
  head.appendChild(el('b', null, sk.name));
  card.appendChild(head);
  if (!(sk.lines || []).length) {
    card.appendChild(el('p', 'anote', 'Pas de dégâts ni de soin chiffrés (effet de script ou de rune).'));
  }
  (sk.lines || []).forEach((l) => {
    const row = el('div', 'spline k-' + l.k);
    const left = el('div', 'spk');
    left.appendChild(el('b', null, l.kind));
    if (l.aff) left.appendChild(el('small', null, l.aff));
    row.appendChild(left);
    const v = el('div', 'spv');
    v.appendChild(el('b', null, l.normal));
    v.appendChild(el('span', 'cc', l.crit + ' CC'));
    row.appendChild(v);
    card.appendChild(row);
  });
  const foot = el('ul', 'spfoot');
  const item = (t) => foot.appendChild(el('li', null, t));
  if (sk.cd) item('Recharge ' + sk.cd);
  if (sk.range) item('Portée ' + sk.range);
  // the averages: one when they are all the same, else one per line
  const lines = sk.lines || [];
  const avgs = [...new Set(lines.map((l) => l.avg))];
  if (avgs.length === 1) item('Moyenne ' + avgs[0]);
  else lines.forEach((l) => item('Moyenne ' + l.avg + ' (' + l.kind.toLowerCase() + ')'));
  [...new Set(lines.map((l) => l.mit).filter(Boolean))]
    .forEach((m) => item('Réduction cible −' + m));
  card.appendChild(foot);
  return card;
}

/* The runes: for each skill on the bar, its three, one to pick. */
function runesSection(list) {
  const box = el('div', 'bruneswrap');
  box.appendChild(el('div', 'sub2', 'Runes'));
  box.appendChild(el('p', 'note', 'Une rune par compétence. Clic pour la poser, re-clic pour l’enlever. '
    + 'Ses effets chiffrés entrent dans la simulation.'));
  list.forEach((g) => {
    const blk = el('div', 'brunes');
    blk.appendChild(el('b', 'brn', g.name));
    const row = el('div', 'brunerow');
    g.runes.forEach((r) => {
      const c = el('button', 'brune' + (r.on ? ' on' : ''));
      c.type = 'button';
      c.appendChild(skillIcon({ id: r.id, name: r.name }));
      const t = el('div', 'brt');
      t.appendChild(el('b', null, r.name));
      t.appendChild(el('span', null, r.desc));
      c.appendChild(t);
      c.addEventListener('click', () => notify('build_rune', { skill: g.skill, rune: r.id }));
      row.appendChild(c);
    });
    blk.appendChild(row);
    box.appendChild(blk);
  });
  return box;
}

/* The action bar under the hero: 1-2 the weapons' skills (set by the
   weapons), 3-4 the arsenal's and A E R G the class's (a click picks). */
function buildBar(cells) {
  const p = el('div', 'spanel bbar');
  p.appendChild(el('div', 'sptitle', 'Barre de sorts'));
  const bar = el('div', 'skbar actionbar');
  cells.forEach((c) => {
    if (c.sep) bar.appendChild(el('span', 'barsep'));
    const cell = el('div', 'barcell' + (c.options ? ' pick' : '') + (c.open ? '' : ' locked'));
    const ic = skillIcon(c.id ? { id: c.id, name: c.name } : null, 'big');
    if (!c.open) ic.title = 'Débloqué au ' + (c.lock || 'niveau suivant');
    else if (c.options) ic.title = (c.name ? c.name + '\n' : '') + 'Clic : choisir';
    cell.appendChild(ic);
    cell.appendChild(el('span', 'bk', c.lock || c.key));
    if (c.options) cell.addEventListener('click', () => skillMenu(cell, c));
    bar.appendChild(cell);
  });
  p.appendChild(bar);
  return p;
}

/* The skills a bar slot can take, in a window over the page: a click
   places one in THIS slot (and only there), or empties it. */
const SLOT_TITLES = { arsenal: 'Compétence d’arsenal', class: 'Compétence de classe' };

function skillMenu(_anchor, c) {
  const close = () => { const x = $('#skillmodal'); if (x) x.remove(); };
  close();
  const back = el('div', 'modalback');
  back.id = 'skillmodal';
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  const box = el('div', 'modal skmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = 'Fermer';
  x.addEventListener('click', close);
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, (SLOT_TITLES[c.group] || 'Compétence') + ' — case ' + c.key));
  box.appendChild(head);
  const body = el('div', 'skbody');
  const choose = (id) => {
    close();
    notify('build_skill', { group: c.group, index: c.index, value: id });
  };
  if (!(c.options || []).length) body.appendChild(el('p', 'note', 'Rien à placer ici pour l’instant.'));
  const grid = el('div', 'skgrid');
  (c.options || []).forEach((s) => {
    const card = el('button', 'skcard' + (s.id === c.id ? ' on' : ''));
    card.type = 'button';
    card.appendChild(skillIcon(s, 'big'));
    card.appendChild(el('span', null, s.name));
    card.addEventListener('click', () => choose(s.id));
    grid.appendChild(card);
  });
  body.appendChild(grid);
  const foot = el('div', 'skfoot');
  if (c.id) {
    const clear = el('button', 'rowbtn', 'Vider la case');
    clear.type = 'button';
    clear.addEventListener('click', () => choose(''));
    foot.appendChild(clear);
  }
  const cancel = el('button', 'rowbtn', 'Annuler');
  cancel.type = 'button';
  cancel.addEventListener('click', close);
  foot.appendChild(cancel);
  body.appendChild(foot);
  box.appendChild(body);
  back.appendChild(box);
  document.body.appendChild(back);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('#skillmodal')) $('#skillmodal').remove();
});

/* The passives at work, under the weapons. */
function buildPassives(list) {
  const p = el('div', 'spanel bpass');
  p.appendChild(el('div', 'sptitle', 'Passifs'));
  if (!list.length) p.appendChild(el('p', 'anote', 'Aucun passif.'));
  list.forEach((s) => {
    const row = el('div', 'bprow');
    row.appendChild(skillIcon(s));
    row.appendChild(el('span', null, s.name));
    p.appendChild(row);
  });
  return p;
}

/* The builds, as cards: a click opens one. */
function buildList(n, box) {
  const head = el('div', 'blisthead');
  head.appendChild(el('div', 'section', 'Mes builds'));
  const nb = el('button', 'btn bnew', '+ Nouveau build');
  nb.type = 'button';
  nb.addEventListener('click', () => notify('build_new', {}));
  head.appendChild(nb);
  box.appendChild(head);
  if (!(n.list || []).length) {
    box.appendChild(el('p', 'note', 'Aucun build pour l’instant. Crée-en un, ou pars d’un '
      + 'joueur analysé dans Inspecter (« Créer un build »).'));
    return box;
  }
  const grid = el('div', 'bcards');
  n.list.forEach((b) => {
    const c = el('button', 'bcard');
    c.type = 'button';
    c.appendChild(classEl(b.cls, b.ck, 'big'));
    const t = el('div', 'bct');
    t.appendChild(el('b', null, b.name));
    t.appendChild(el('span', null, b.cls + ' · niveau ' + (b.lvl || '?')));
    c.appendChild(t);
    c.addEventListener('click', () => notify('build_open', { file: b.file }));
    grid.appendChild(c);
  });
  box.appendChild(grid);
  return box;
}

/* Name, class, level, and the build's own buttons. */
function buildHead(o) {
  const head = el('div', 'bhead');
  const name = el('input', 'bname');
  name.type = 'text';
  name.value = o.name;
  name.maxLength = 60;
  name.addEventListener('change', () => notify('build_rename', { value: name.value }));
  head.appendChild(name);

  const cls = el('select', 'bcls');
  (o.classes || []).forEach((c) => {
    const op = el('option', null, c.t);
    op.value = c.v;
    cls.appendChild(op);
  });
  cls.value = o.cls;
  cls.addEventListener('change', () => notify('build_class', { value: cls.value }));
  head.appendChild(cls);

  const lv = el('div', 'blvl');
  const out = el('b', null, 'Niveau ' + o.lvl);
  const r = el('input');
  r.type = 'range';
  r.min = 1; r.max = o.maxLvl; r.value = o.lvl;
  r.addEventListener('input', () => { out.textContent = 'Niveau ' + r.value; });
  r.addEventListener('change', () => notify('build_level', { value: Number(r.value) }));
  lv.appendChild(out);
  lv.appendChild(r);
  head.appendChild(lv);

  const btns = el('div', 'bbtns');
  const dup = el('button', 'rowbtn', 'Dupliquer');
  dup.type = 'button';
  dup.addEventListener('click', () => notify('build_dup', {}));
  btns.appendChild(dup);
  const del = el('button', 'rowbtn' + (o.confirmDelete ? ' armed' : ''),
    o.confirmDelete ? 'Confirmer la suppression' : 'Supprimer');
  del.type = 'button';
  del.addEventListener('click', () => notify('build_delete', {}));
  btns.appendChild(del);
  if (o.confirmDelete) {
    const no = el('button', 'rowbtn', 'Annuler');
    no.type = 'button';
    no.addEventListener('click', () => notify('build_delete_cancel', {}));
    btns.appendChild(no);
  }
  head.appendChild(btns);
  return head;
}

/* ---- the piece editor, in the sheet's centre -------------------------- */
let BUILD_SCROLL = { list: 0, right: 0 };   // kept across re-renders
let BUILD_DROP = false;         // the piece list is open
let BUILD_SUB = null;           // the augment / infusion whose choices are open

/* Redraw the piece editor alone, from the last state (a local change). */
function refreshEditor() {
  const old = document.querySelector('.bedpanel');
  const ed = BUILD_NODE && BUILD_NODE.open ? BUILD_NODE.open.editor : null;
  if (old && ed) old.replaceWith(editorPanel(ed));
}

function renderBuildEditor() {
  // the editor lives in the sheet now: just forget the search when closed
  const ed = STATE.tab === 'Build' && BUILD_NODE && BUILD_NODE.open
    ? BUILD_NODE.open.editor : null;
  if (!ed) {
    BUILD_Q = '';
    BUILD_SCROLL = { list: 0, right: 0 };
  }
}

function editorPanel(ed) {
  const box = el('div', 'bedpanel');
  if (BUILD_F.slot !== ed.slot) {      // another slot: fresh search, list open if empty
    BUILD_F.slot = ed.slot;
    BUILD_F.stats = new Set();
    BUILD_F.fac = '';
    BUILD_Q = '';
    BUILD_DROP = !ed.piece;
    BUILD_SUB = null;
  }
  const p = ed.piece;

  // the head: the slot, then the piece as a drop-down, and its buttons
  const head = el('div', 'bedhead');
  head.appendChild(el('b', null, ed.label));
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = 'Revenir au personnage';
  x.addEventListener('click', () => notify('build_slot_close', {}));
  head.appendChild(x);
  box.appendChild(head);

  const pick = el('div', 'bpick');
  const ic = el('span', 'gi');
  const cur = p ? (ed.options || []).find((o) => o.id === p.id) : null;
  if (cur && cur.img) { const im = el('img'); im.src = cur.img; im.alt = ''; ic.appendChild(im); }
  pick.appendChild(ic);
  const combo = el('button', 'bcombo' + (BUILD_DROP ? ' open' : '')
    + (p ? ' r-' + (p.rar || 'Common').toLowerCase() : ''));
  combo.type = 'button';
  combo.appendChild(el('span', null, p ? p.name : 'Choisir une pièce…'));
  combo.appendChild(el('i', 'chev', '▾'));
  combo.addEventListener('click', () => { BUILD_DROP = !BUILD_DROP; renderDrop(); combo.classList.toggle('open', BUILD_DROP); });
  pick.appendChild(combo);
  if (p) {
    const rm = el('button', 'bsquare', '×');
    rm.type = 'button';
    rm.title = 'Retirer la pièce';
    rm.addEventListener('click', () => notify('build_unequip', {}));
    pick.appendChild(rm);
  }
  box.appendChild(pick);

  // the drop-down: search, filters, the pieces
  const drop = el('div', 'bdrop');
  const q = el('input', 'bsearch');
  q.type = 'search';
  q.placeholder = 'Rechercher…';
  q.value = BUILD_Q;
  drop.appendChild(q);
  const filters = el('div', 'bfilters');
  const statRow = el('div', 'bchips small');
  (ed.stats || []).forEach((st) => {
    const c = el('button', 'bchip' + (BUILD_F.stats.has(st.k) ? ' on' : ''), st.t);
    c.type = 'button';
    c.addEventListener('click', () => {
      if (BUILD_F.stats.has(st.k)) BUILD_F.stats.delete(st.k); else BUILD_F.stats.add(st.k);
      c.classList.toggle('on', BUILD_F.stats.has(st.k));
      fill();
    });
    statRow.appendChild(c);
  });
  filters.appendChild(statRow);
  if ((ed.factions || []).length) {
    const fs = select([{ v: '', t: 'Toutes les familles' }]
      .concat(ed.factions.map((f) => ({ v: f, t: f }))), BUILD_F.fac,
      (v) => { BUILD_F.fac = v; fill(); });
    fs.classList.add('bfac');
    filters.appendChild(fs);
  }
  drop.appendChild(filters);
  const count = el('div', 'bcount');
  drop.appendChild(count);
  const list = el('div', 'blist');
  const fill = () => {
    list.textContent = '';
    const needle = BUILD_Q.toLowerCase();
    const shown = (ed.options || []).filter((it) => (!needle
      || it.name.toLowerCase().includes(needle) || it.type.toLowerCase().includes(needle))
      && (!BUILD_F.fac || it.fac === BUILD_F.fac)
      && [...BUILD_F.stats].every((k) => (it.stats || []).includes(k)));
    count.textContent = shown.length + ' / ' + (ed.options || []).length + ' pièces';
    if (!shown.length) list.appendChild(el('div', 'empty', ed.options.length
      ? 'Aucune pièce ne correspond.' : 'Aucune pièce possible ici pour l’instant.'));
    shown.forEach((it) => {
      const row = el('div', 'bitem r-' + (it.rk || 'common') + (p && p.id === it.id ? ' on' : ''));
      const ii = el('span', 'gi');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; ii.appendChild(im); }
      row.appendChild(ii);
      const t = el('div', 'bt');
      t.appendChild(el('b', 'nm', it.name));
      t.appendChild(el('span', null, it.type));
      row.appendChild(t);
      row.addEventListener('click', () => { BUILD_DROP = false; notify('build_pick', { id: it.id }); });
      list.appendChild(row);
    });
  };
  q.addEventListener('input', () => { BUILD_Q = q.value; fill(); });
  fill();
  drop.appendChild(list);
  box.appendChild(drop);
  const renderDrop = () => { drop.style.display = BUILD_DROP ? '' : 'none'; if (BUILD_DROP) q.focus(); };
  drop.style.display = BUILD_DROP ? '' : 'none';

  // the piece's settings, the whole width
  const body = el('div', 'bedcfg');
  if (!p) {
    body.appendChild(el('p', 'note', 'Choisis une pièce dans la liste.'));
  } else {
    // augments and infusion: lines of the card, waiting for a choice;
    // a click opens the choices right there
    const g = p.g || {};
    const choices = [];
    (p.augs || []).forEach((a) => choices.push({
      key: 'aug:' + a.kind, t: a.t, v: a.v, options: a.options, none: 'Aucun',
    }));
    if (p.infusable) {
      choices.push({ key: 'inf', t: 'Imprégnation', v: p.inf, options: p.infOptions, none: 'Aucune' });
      choices.push({ key: 'istat', t: 'Bonus d’imprégnation', v: p.istat, options: p.statOptions,
        none: 'Aucun', small: true,
        fx: g.inf && g.inf.bonus
          ? '+' + (g.inf.val || '') + (g.inf.on ? '' : ' — inactif, faction différente')
          : (g.plan ? g.plan + ' — inactif sans imprégnation' : '') });
    }
    const lines = choices.map((c) => (c.key === BUILD_SUB ? choiceList(c) : choiceLine(c)));
    if (p.g) {
      const pv = el('div', 'gearlist one');
      pv.appendChild(gearRow(p.g, {
        rars: ed.rarities, rar: p.rar, onRar: (v) => notify('build_piece', { field: 'rar', value: v }),
        lvl: p.lvl, maxLvl: ed.maxLvl, onLvl: (v) => notify('build_piece', { field: 'lvl', value: v }),
        up: p.up, maxUp: p.maxUp, onUp: (v) => notify('build_piece', { field: 'up', value: v }),
        prism: p.infusable ? !!p.prism : null,
        onPrism: (v) => notify('build_piece', { field: 'prism', value: v }),
        lines,
      }));
      body.appendChild(pv);
    } else {
      lines.forEach((x) => body.appendChild(x));
    }
    if (!p.infusable && ['Head', 'Shoulders', 'Chest', 'Back', 'Hands', 'Waist', 'Legs', 'Feet'].includes(ed.slot)) {
      body.appendChild(el('p', 'note', 'Imprégnation : sur une armure de faction, à partir de la rareté '
        + (ed.infusionMin || 'Épique').toLowerCase() + '.'));
    }
  }
  box.appendChild(body);
  // keep where the list and the settings were scrolled, across re-renders
  list.addEventListener('scroll', () => { BUILD_SCROLL.list = list.scrollTop; });
  body.addEventListener('scroll', () => { BUILD_SCROLL.right = body.scrollTop; });
  requestAnimationFrame(() => {
    list.scrollTop = BUILD_SCROLL.list;
    body.scrollTop = BUILD_SCROLL.right;
  });
  return box;
}

/* One choice row: its icon, then its name and what it does. */
function choiceRow(o, none, on) {
  const row = el('button', 'bchoice' + (on ? ' on' : '') + (o && o.v ? '' : ' none'));
  row.type = 'button';
  const ic = el('span', 'gi');
  if (o && o.img) { const im = el('img'); im.src = o.img; im.alt = ''; ic.appendChild(im); }
  row.appendChild(ic);
  const t = el('div', 'bct');
  t.appendChild(el('b', null, o && o.v ? o.t : none));
  if (o && o.sub) t.appendChild(el('span', 'sub', o.sub));
  if (o && o.fx) t.appendChild(el('span', 'fx', o.fx));
  ((o && o.tiers) || []).forEach((x, i) => t.appendChild(el('span', 'fx tier', '(' + [2, 4, 6][i] + ') ' + x)));
  row.appendChild(t);
  return row;
}

/* An augment / the infusion, a line of the piece's card: what is set,
   or waiting for a choice; a click shows the choices. */
function choiceLine(c) {
  const cur = (c.options || []).find((o) => o.v && o.v === c.v);
  const row = el('button', 'gchoice' + (cur ? '' : ' wait'));
  row.type = 'button';
  if (!c.small) {
    const ic = el('span', 'gi');
    if (cur && cur.img) { const im = el('img'); im.src = cur.img; im.alt = ''; ic.appendChild(im); }
    row.appendChild(ic);
  }
  const t = el('div', 'bct');
  t.appendChild(el('span', 'k', c.t));
  if (cur) {
    const nm = el('b', null, cur.t);
    if (cur.sub) nm.appendChild(el('small', null, '  ' + cur.sub));
    t.appendChild(nm);
    if (cur.fx || c.fx) t.appendChild(el('span', 'fx', cur.fx || c.fx));
  } else {
    t.appendChild(el('b', 'wait', 'En attente de sélection'));
    if (c.fx) t.appendChild(el('span', 'fx off', c.fx));
  }
  row.appendChild(t);
  row.appendChild(el('i', 'chev', '›'));
  row.addEventListener('click', () => { BUILD_SUB = c.key; refreshEditor(); });
  return row;
}

/* Its choices, in the block: pick one, or go back. */
function choiceList(c) {
  const box = el('div', 'bchoices' + (c.small ? ' small' : ''));
  const back = el('button', 'bsubback', '‹  ' + c.t);
  back.type = 'button';
  back.addEventListener('click', () => { BUILD_SUB = null; refreshEditor(); });
  box.appendChild(back);
  (c.options || []).forEach((o) => {
    const row = choiceRow(o, c.none, o.v === (c.v || ''));
    row.addEventListener('click', () => {
      BUILD_SUB = null;
      notify('build_piece', { field: c.key, value: o.v });
    });
    box.appendChild(row);
  });
  return box;
}

function slider(min, max, v, onChange, prefix, suffix) {
  const w = el('div', 'bslider');
  const r = el('input');
  r.type = 'range';
  r.min = min; r.max = max; r.value = v;
  suffix = suffix || '';
  const out = el('b', null, prefix + v + suffix);
  r.addEventListener('input', () => { out.textContent = prefix + r.value + suffix; });
  r.addEventListener('change', () => onChange(Number(r.value)));
  w.appendChild(r);
  w.appendChild(out);
  return w;
}

function select(options, v, onChange) {
  const s = el('select');
  (options || []).forEach((o) => {
    const op = el('option', null, o.t);
    op.value = o.v;
    s.appendChild(op);
  });
  s.value = v || '';
  s.addEventListener('change', () => onChange(s.value));
  return s;
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#skillmodal') && BUILD_NODE && BUILD_NODE.open && BUILD_NODE.open.editor) {
    notify('build_slot_close', {});
  }
});
