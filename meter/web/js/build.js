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
  const band = el('div', 'bband');
  band.appendChild(el('b', 'bbt', 'Mes builds'));
  (n.list || []).forEach((b) => {
    const chip = el('button', 'bbuild' + (b.on ? ' on' : ''));
    chip.type = 'button';
    chip.appendChild(charClass(b));
    chip.appendChild(el('span', null, b.name));
    chip.appendChild(el('small', null, 'niv. ' + (b.lvl || '?')));
    chip.addEventListener('click', () => notify('build_open', { file: b.file }));
    band.appendChild(chip);
  });
  const nb = el('button', 'btn bnew', '+ Nouveau build');
  nb.type = 'button';
  nb.addEventListener('click', () => notify('build_new', {}));
  band.appendChild(nb);
  box.appendChild(band);
  if (!(n.list || []).length) {
    box.appendChild(el('p', 'note', 'Aucun build pour l’instant. Crée-en un, ou pars d’un '
      + 'joueur analysé dans Inspecter (« Créer un build »).'));
  }

  const main = el('div', 'charmain');
  const o = n.open;
  if (!o) {
    main.appendChild(el('div', 'empty charempty', 'Choisis un build, ou crée-en un nouveau.'));
    box.appendChild(main);
    return box;
  }
  main.appendChild(buildHead(o));
  main.appendChild(charSheet({ n: o.name, cls: o.clsFr, ck: o.ck, lvl: o.lvl,
                               sheet: o.sheet, atbs: o.atbs },
                             (slot) => notify('build_slot', { slot: slot }),
                             { below: buildBar(o.bar || []), arms: buildPassives(o.passives || []) }));
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
  (sk.lines || []).forEach((l) => {
    item('Moyenne ' + l.avg + (sk.lines.length > 1 ? ' (' + l.kind.toLowerCase() + ')' : ''));
    if (l.mit) item('Réduction cible −' + l.mit);
  });
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

/* ---- the piece editor (a window over the page) ------------------------- */
function renderBuildEditor() {
  const ed = STATE.tab === 'Build' && BUILD_NODE && BUILD_NODE.open
    ? BUILD_NODE.open.editor : null;
  let m = $('#buildmodal');
  if (!ed) {
    if (m) m.remove();
    BUILD_Q = '';
    return;
  }
  if (!m) {
    m = el('div', 'modalback');
    m.id = 'buildmodal';
    m.addEventListener('mousedown', (e) => {
      if (e.target === m) notify('build_slot_close', {});
    });
    document.body.appendChild(m);
  }
  const sig = JSON.stringify(ed);
  if (m.dataset.sig === sig) return;
  m.dataset.sig = sig;
  const keepScroll = m.querySelector('.blist') ? m.querySelector('.blist').scrollTop : 0;
  m.textContent = '';
  const box = el('div', 'modal bmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = 'Fermer';
  x.addEventListener('click', () => notify('build_slot_close', {}));
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, ed.label));
  box.appendChild(head);

  const body = el('div', 'bedbody');
  // the pieces the slot can take
  const left = el('div', 'bedleft');
  if (BUILD_F.slot !== ed.slot) {      // another slot: fresh filters
    BUILD_F.slot = ed.slot;
    BUILD_F.stats = new Set();
    BUILD_F.fac = '';
  }
  const q = el('input', 'bsearch');
  q.type = 'search';
  q.placeholder = 'Rechercher…';
  q.value = BUILD_Q;
  left.appendChild(q);
  const filters = el('div', 'bfilters');
  const statRow = el('div', 'bchips small');
  (ed.stats || []).forEach((s) => {
    const c = el('button', 'bchip' + (BUILD_F.stats.has(s.k) ? ' on' : ''), s.t);
    c.type = 'button';
    c.addEventListener('click', () => {
      if (BUILD_F.stats.has(s.k)) BUILD_F.stats.delete(s.k); else BUILD_F.stats.add(s.k);
      c.classList.toggle('on', BUILD_F.stats.has(s.k));
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
  left.appendChild(filters);
  const count = el('div', 'bcount');
  left.appendChild(count);
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
      const row = el('div', 'bitem r-' + (it.rk || 'common')
        + (ed.piece && ed.piece.id === it.id ? ' on' : ''));
      const ic = el('span', 'gi');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; ic.appendChild(im); }
      row.appendChild(ic);
      const t = el('div', 'bt');
      t.appendChild(el('b', 'nm', it.name));
      t.appendChild(el('span', null, it.type));
      row.appendChild(t);
      row.addEventListener('click', () => notify('build_pick', { id: it.id }));
      list.appendChild(row);
    });
  };
  q.addEventListener('input', () => { BUILD_Q = q.value; fill(); });
  fill();
  left.appendChild(list);
  body.appendChild(left);

  // the chosen piece's settings
  const right = el('div', 'bedright');
  const p = ed.piece;
  if (!p) {
    right.appendChild(el('p', 'note', 'Choisis une pièce dans la liste.'));
  } else {
    const field = (label, ctl) => {
      const f = el('div', 'bfield');
      f.appendChild(el('span', 'bl', label));
      f.appendChild(ctl);
      right.appendChild(f);
    };
    const chips = el('div', 'bchips');
    (ed.rarities || []).forEach((r) => {
      const c = el('button', 'bchip' + (p.rar === r.v ? ' on' : '') + ' r-' + r.v.toLowerCase(), r.t);
      c.type = 'button';
      c.addEventListener('click', () => notify('build_piece', { field: 'rar', value: r.v }));
      chips.appendChild(c);
    });
    field('Rareté', chips);
    field('Niveau', slider(1, ed.maxLvl, p.lvl, (v) => notify('build_piece', { field: 'lvl', value: v }), 'Niveau '));
    if (p.maxUp) {
      field('Amélioration', slider(0, p.maxUp, p.up, (v) => notify('build_piece', { field: 'up', value: v }), '+'));
    }
    if (p.infusable) {
      const lab = el('label', 'bcheck');
      const cb = el('input');
      cb.type = 'checkbox';
      cb.checked = !!p.prism;
      cb.addEventListener('change', () => notify('build_piece', { field: 'prism', value: cb.checked }));
      lab.appendChild(cb);
      lab.appendChild(el('span', null, 'Prismatique'));
      lab.appendChild(el('small', null, ' — le bonus d’imprégnation s’applique quelle que soit la faction'));
      field('Qualité', lab);
    }
    (p.augs || []).forEach((a) => {
      field(a.t, select(a.options, a.v, (v) => notify('build_piece', { field: 'aug:' + a.kind, value: v })));
    });
    if (p.infusable) {
      field('Imprégnation', select(p.infOptions, p.inf, (v) => notify('build_piece', { field: 'inf', value: v })));
      field('Bonus d’imprégnation' + (p.inf ? '' : ' (prévisionnel)'),
        select(p.statOptions, p.istat, (v) => notify('build_piece', { field: 'istat', value: v })));
    } else if (['Head', 'Shoulders', 'Chest', 'Back', 'Hands', 'Waist', 'Legs', 'Feet'].includes(ed.slot)) {
      right.appendChild(el('p', 'note', 'Imprégnation : sur une armure de faction, à partir de la rareté '
        + (ed.infusionMin || 'Épique').toLowerCase() + '.'));
    }
    if (p.g) {
      const pv = el('div', 'gearlist one');
      pv.appendChild(gearRow(p.g));
      right.appendChild(pv);
    }
    const rm = el('button', 'rowbtn', 'Retirer la pièce');
    rm.type = 'button';
    rm.addEventListener('click', () => notify('build_unequip', {}));
    right.appendChild(rm);
  }
  body.appendChild(right);
  box.appendChild(body);
  m.appendChild(box);
  const bl = m.querySelector('.blist');
  if (bl) bl.scrollTop = keepScroll;
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
  if (e.key === 'Escape' && $('#buildmodal')) notify('build_slot_close', {});
});
