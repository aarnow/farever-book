/* The Build tab: the saved builds, and the one being edited — class and
   level, the gear on the character sheet (a slot opens the piece editor),
   the talent tree, the skills. Everything is computed by the meter
   (meter/buildtab.py); this only draws it and sends the changes. */

let BUILD_NODE = null;
const BUILD_VIEWS = [['stuff', 'Équipement'], ['talents', 'Talents'], ['runes', 'Runes'], ['sim', 'Simulation'],
  ['settings', 'Paramètres']];
let BUILD_VIEW = 'stuff';        // the open build's tab
let BUILD_FADE = false;         // the next draw follows a tab change
let BUILD_Q = '';               // the piece editor's search
// the piece editor's filters: stats the piece must all give, its family
const BUILD_F = { slot: null, stats: new Set(), fac: '' };

function buildBuild(n) {
  BUILD_NODE = n;
  const box = el('div', 'buildpage');
  const o = n.open;
  if (n.cmp) return buildCompare(n.cmp, box);
  if (!o) { BUILD_VIEW = 'stuff'; return buildList(n, box); }
  const back = el('button', 'btn bback', tr('‹  Revenir aux builds'));
  back.type = 'button';
  back.addEventListener('click', () => notify('build_close', {}));
  box.appendChild(back);

  const main = el('div', 'charmain');

  // the build in tabs: the gear, the talents, the runes, the simulation;
  // its settings (name, class, level, copy, delete) last, at the right
  const tabs = el('div', 'btabs');
  BUILD_VIEWS.forEach(([k, t]) => {
    const b = el('button', 'btab' + (BUILD_VIEW === k ? ' on' : '') + (k === 'settings' ? ' bset' : ''), tr(t));
    b.type = 'button';
    b.addEventListener('click', () => {
      if (BUILD_VIEW === k) return;
      BUILD_VIEW = k;
      BUILD_FADE = true;
      if (o.editor) notify('build_slot_close', {});
      const page = buildBuild(BUILD_NODE);
      box.replaceWith(page);
      NODES.forEach((v) => { if (v.el === box) v.el = page; });   // core.js keeps the page's nodes
    });
    tabs.appendChild(b);
  });
  main.appendChild(tabs);
  const shown = main.children.length;     // what comes after the tabs

  if (BUILD_VIEW === 'stuff') {
    main.appendChild(charSheet({ n: o.name, cls: o.clsFr, ck: o.ck, lvl: o.lvl,
                                 sheet: o.sheet, atbs: o.atbs },
                               (slot) => { BUILD_JUMP = true; notify('build_slot', { slot: slot }); },
                               { below: buildBar(o.bar || []), arms: buildPassives(o.passives || []),
                                 center: o.editor ? editorPanel(o.editor) : null,
                                 model: o.model,
                                 active: o.editor ? o.editor.slot : null,
                                 hint: tr('Clique sur un emplacement pour choisir ou régler une pièce.') }));
    if ((o.infusions || []).length) {
      main.appendChild(el('div', 'sub2', tr('Imprégnations')));
      main.appendChild(infusionCards(o.infusions));
    }
  } else if (BUILD_VIEW === 'talents') {
    buildTalents(o, main);
  } else if (BUILD_VIEW === 'runes') {
    main.appendChild(runesSection((o.sim && o.sim.runes) || []));
  } else if (BUILD_VIEW === 'settings') {
    main.appendChild(buildSettings(o));
  } else if (o.sim) {
    main.appendChild(buildSim(o.sim));
  } else {
    main.appendChild(el('p', 'note', tr('La simulation a besoin d’une classe et d’un équipement.')));
  }
  // a tab change eases in the tab's content only: the head and the tabs
  // themselves stay put
  if (BUILD_FADE) {
    Array.from(main.children).slice(shown).forEach((c) => c.classList.add('fadein'));
    BUILD_FADE = false;
  }
  box.appendChild(main);
  return box;
}

/* The talents tab: the points spent, then the tree. */
function buildTalents(o, main) {
  const left = el('div', 'btleft');
  const th = el('div', 'sub2 bsub');
  th.appendChild(document.createTextNode(tr('Talents — {used} / {total} points',
    { used: o.points.used, total: o.points.total })));
  if (o.points.used) {
    const r = el('button', 'rowbtn', tr('Tout retirer'));
    r.type = 'button';
    r.addEventListener('click', () => notify('build_talents_reset', {}));
    th.appendChild(r);
  }
  left.appendChild(th);
  if (!o.points.total) {
    left.appendChild(el('p', 'note', tr('Les talents se débloquent au niveau {n}.', { n: o.points.from })));
  } else {
    left.appendChild(el('p', 'note', tr('Clic sur un talent : +1 point. Clic droit : −1. Un palier '
      + 's’ouvre avec les points dépensés plus haut dans sa branche (chiffre à gauche).')));
  }
  if (o.tree) {
    left.appendChild(talentTree(o.tree, true,
      (id, delta) => notify('build_talent', { id: id, delta: delta })));
  }
  main.appendChild(left);
}

/* The simulation: what the build deals and heals against a target whose
   armour is set in %, and what an incoming hit leaves. */
function buildSim(s) {
  const wrap = el('div', 'bsim');
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
  arm.appendChild(el('span', null, tr('Réduction cible')));
  arm.appendChild(slider(0, 80, s.armor, (v) => notify('build_sim', { field: 'armor', value: v }), '', ' %'));
  ctl.appendChild(arm);
  ctl.appendChild(num(tr('Niveau de l’ennemi'), s.enemy, 'enemy', 1, s.maxLvl));
  ctl.appendChild(num(tr('Coup reçu'), s.hit, 'hit', 0, 100000, 10));
  wrap.appendChild(ctl);
  wrap.appendChild(el('p', 'note', tr('Critique {crit} · bonus critique {mult}'
    + ' (CC : coup critique). La moyenne tient compte des chances de critique. Les effets propres '
    + 'à certains sorts (bonus conditionnels, cumuls, effets spéciaux codés dans le jeu) ne sont pas simulés.',
    { crit: s.crit, mult: s.critMult })));

  // a block per source: the weapons worn, the arsenal's, the class's skills
  const out = el('div', 'bsimgrid');
  const t = el('div', 'bsimgroups');
  const groups = (s.groups || []).filter((g) => (g.skills || []).length);
  if (!groups.length) t.appendChild(el('p', 'anote', tr('Équipe une arme pour simuler ses attaques.')));
  groups.forEach((g) => {
    const blk = el('div', 'bsimgroup');
    const h = el('div', 'bsimgh');
    h.appendChild(el('b', null, g.t));
    if (g.sub) h.appendChild(el('span', null, g.sub));
    blk.appendChild(h);
    const cards = el('div', 'bsimcards');
    g.skills.forEach((sk) => cards.appendChild(spellCard(sk)));
    blk.appendChild(cards);
    t.appendChild(blk);
  });
  out.appendChild(t);
  const d = s.defense;
  const c = el('div', 'spanel bsimdef');
  c.appendChild(el('div', 'sptitle', tr('Ce que tu reçois')));
  const line = (label, v, sub) => {
    const r = el('div', 'attr sec');
    r.appendChild(el('span', 'an', label));
    const b = el('b', 'av', v);
    if (sub) b.appendChild(el('small', null, ' (' + sub + ')'));
    r.appendChild(b);
    c.appendChild(r);
  };
  line(tr('Coup reçu'), d.hit);
  line(tr('Physique'), d.phys, tr('armure −{n}', { n: d.physMit }));
  line(tr('Magique'), d.magic, tr('réduction −{n}', { n: d.magicMit }));
  line(tr('Dégâts reçus (ferveur)'), d.taken);
  line(tr('Points de vie'), d.hp);
  line(tr('Coups physiques encaissés'), d.hitsPhys);
  out.appendChild(c);
  wrap.appendChild(out);
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
    card.appendChild(el('p', 'anote', tr('Pas de dégâts ni de soin chiffrés (effet de script ou de rune).')));
  }
  (sk.lines || []).forEach((l) => {
    const row = el('div', 'spline k-' + l.k);
    const left = el('div', 'spk');
    left.appendChild(el('b', null, l.kind));
    if (l.aff) left.appendChild(el('small', null, l.aff));
    row.appendChild(left);
    const v = el('div', 'spv');
    v.appendChild(el('b', null, l.normal));
    v.appendChild(el('span', 'cc', tr('{n} CC', { n: l.crit })));
    row.appendChild(v);
    card.appendChild(row);
  });
  const foot = el('ul', 'spfoot');
  const item = (t) => foot.appendChild(el('li', null, t));
  if (sk.cd) item(tr('Recharge {cd}', { cd: sk.cd }));
  if (sk.range) item(tr('Portée {range}', { range: sk.range }));
  // the averages: one when they are all the same, else one per line
  const lines = sk.lines || [];
  const avgs = [...new Set(lines.map((l) => l.avg))];
  if (avgs.length === 1) item(tr('Moyenne {avg}', { avg: avgs[0] }));
  else lines.forEach((l) => item(tr('Moyenne {avg} ({kind})', { avg: l.avg, kind: l.kind.toLowerCase() })));
  [...new Set(lines.map((l) => l.mit).filter(Boolean))]
    .forEach((m) => item(tr('Réduction cible −{n}', { n: m })));
  card.appendChild(foot);
  return card;
}

/* The runes: for each skill on the bar, its three, one to pick. */
function runesSection(list) {
  const box = el('div', 'bruneswrap');
  box.appendChild(el('div', 'sub2', tr('Runes')));
  box.appendChild(el('p', 'note', tr('Une rune par compétence de classe, réglée pour le personnage : '
    + 'elle vaut que la compétence soit dans la barre ou non. Clic pour la poser, re-clic pour l’enlever.')));
  const grid = el('div', 'brunegrid');
  const row = (g) => {
    const sk = el('div', 'bruneskill');
    sk.appendChild(skillIcon({ id: g.skill, name: g.name }, 'big'));
    sk.appendChild(el('b', null, g.name));
    if (g.bar) sk.appendChild(el('span', 'onbar', tr('dans la barre')));
    grid.appendChild(sk);
    const r = el('div', 'brunerow');
    g.runes.forEach((x) => {
      const c = el('button', 'brune' + (x.on ? ' on' : ''));
      c.type = 'button';
      c.appendChild(skillIcon({ id: x.id, name: x.name }));
      const t = el('div', 'brt');
      t.appendChild(el('b', null, x.name));
      t.appendChild(el('span', null, x.desc));
      c.appendChild(t);
      c.addEventListener('click', () => notify('build_rune', { skill: g.skill, rune: x.id }));
      r.appendChild(c);
    });
    grid.appendChild(r);
  };
  list.forEach(row);
  box.appendChild(grid);
  return box;
}

/* The action bar under the hero: 1-2 the weapons' skills (set by the
   weapons), 3-4 the arsenal's and A E R G the class's (a click picks). */
function buildBar(cells) {
  const p = el('div', 'spanel bbar');
  p.appendChild(el('div', 'sptitle', tr('Barre de sorts')));
  const bar = el('div', 'skbar actionbar gamebar');
  cells.forEach((c) => {
    if (c.sep) bar.appendChild(el('span', 'barsep'));
    const cell = barCell(Object.assign({}, c, { key: c.open ? c.key : (c.lock || c.key) }));
    if (c.options) cell.classList.add('pick');
    if (!c.open) {
      cell.classList.add('locked');
      cell.title = tr('Débloqué au {lvl}', { lvl: c.lock || tr('niveau suivant') });
    } else if (c.options && !c.tip) {
      cell.title = (c.name ? c.name + '\n' : '') + tr('Clic : choisir');
    }
    if (c.options) cell.addEventListener('click', () => skillMenu(cell, c));
    bar.appendChild(cell);
  });
  p.appendChild(bar);
  return p;
}

/* The skills a bar slot can take, in a window over the page: a click
   places one in THIS slot (and only there), or empties it. */
const SLOT_TITLES = { arsenal: 'Compétence d’arsenal', class: 'Compétence de classe' };   // through tr() when shown

function skillMenu(_anchor, c) {
  const close = () => { const x = $('#skillmodal'); if (x) x.remove(); };
  close();
  const back = el('div', 'modalback');
  back.id = 'skillmodal';
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  const box = el('div', 'modal skmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', close);
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, tr('{what} — case {key}', { what: tr(SLOT_TITLES[c.group] || 'Compétence'), key: c.key })));
  box.appendChild(head);
  const body = el('div', 'skbody');
  const choose = (id) => {
    close();
    notify('build_skill', { group: c.group, index: c.index, value: id });
  };
  if (!(c.options || []).length) body.appendChild(el('p', 'note', tr('Rien à placer ici pour l’instant.')));
  const grid = el('div', 'skgrid');
  (c.options || []).forEach((s) => {
    const card = el('button', 'skcard' + (s.id === c.id ? ' on' : ''));
    card.type = 'button';
    card.appendChild(skillIcon(s, 'big'));
    card.appendChild(el('span', null, s.name));
    attachTip(card, s.tip, s.id);
    card.addEventListener('click', () => choose(s.id));
    grid.appendChild(card);
  });
  body.appendChild(grid);
  const foot = el('div', 'skfoot');
  if (c.id) {
    const clear = el('button', 'rowbtn', tr('Vider la case'));
    clear.type = 'button';
    clear.addEventListener('click', () => choose(''));
    foot.appendChild(clear);
  }
  const cancel = el('button', 'rowbtn', tr('Annuler'));
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
  p.appendChild(el('div', 'sptitle', tr('Passifs')));
  if (!list.length) p.appendChild(el('p', 'anote', tr('Aucun passif.')));
  list.forEach((s) => {
    const row = el('div', 'bprow');
    row.appendChild(skillIcon(s));
    row.appendChild(el('span', null, s.name));
    attachTip(row, s.tip, s.id);
    p.appendChild(row);
  });
  return p;
}

/* Two builds of one class side by side: pick each, then their attributes
   in two columns; where they differ, the higher value is green with its
   lead, the lower red. */
function buildCompare(c, box) {
  const back = el('button', 'btn bback', tr('‹  Revenir aux builds'));
  back.type = 'button';
  back.addEventListener('click', () => notify('build_cmp_close', {}));
  box.appendChild(back);
  const page = el('div', 'bcmp');
  page.appendChild(el('div', 'section', tr('Comparer deux builds')));

  const head = el('div', 'bcmphead');
  const side = (s, key, picks) => {
    const card = el('div', 'bcmpcard ' + key);
    const top = el('div', 'bcmpwho');
    top.appendChild(classEl(s.cls, s.ck, 'big'));
    const t = el('div');
    t.appendChild(el('b', null, s.name));
    t.appendChild(el('span', null, tr('{cls} · niveau {lvl}', { cls: s.cls, lvl: s.lvl })));
    top.appendChild(t);
    card.appendChild(top);
    const sel = select(picks, s.file, (v) => notify('build_cmp_set', { side: key, file: v }));
    sel.classList.add('bcmpsel');
    card.appendChild(sel);
    return card;
  };
  head.appendChild(side(c.a, 'a', c.pickA));
  head.appendChild(el('div', 'bcmpvs', 'VS'));
  head.appendChild(side(c.b, 'b', c.pickB));
  page.appendChild(head);

  (c.groups || []).forEach((g) => {
    const blk = el('div', 'spanel bcmpblk');
    blk.appendChild(el('div', 'sptitle', g.t));
    g.rows.forEach((r) => {
      const row = el('div', 'bcmprow' + (r.better ? ' diff' : ''));
      const cell = (key) => {
        const v = el('div', 'bcmpv ' + key + (r.better === key ? ' up' : r.better ? ' down' : ''));
        v.appendChild(el('b', null, r[key]));
        if (r.better === key) v.appendChild(el('small', null, r.delta));
        return v;
      };
      row.appendChild(cell('a'));
      const lab = el('div', 'bcmpl');
      const art = 'stat_' + (r.k === 'Intellect' ? 'Intelligence' : r.k);   // the sheet's art name
      if (SHEET_ART[art]) lab.appendChild(artImg(art));
      lab.appendChild(el('span', null, r.t));
      row.appendChild(lab);
      row.appendChild(cell('b'));
      blk.appendChild(row);
    });
    page.appendChild(blk);
  });

  // the spells: each source, each spell once, lines face to face
  if ((c.spells || []).length) {
    page.appendChild(el('div', 'section', tr('Sorts')));
    const ctl = el('div', 'spanel bcmpctl');
    ctl.appendChild(el('span', 'bl', tr('Réduction de la cible')));
    ctl.appendChild(slider(0, 80, Math.round(c.armor || 0),
      (v) => notify('build_cmp_armor', { value: v }), '', ' %'));
    page.appendChild(ctl);
    if (c.target) page.appendChild(el('p', 'note', c.target));
    c.spells.forEach((g) => {
      const blk = el('div', 'spanel bcmpblk');
      const h = el('div', 'sptitle bcmpgt');
      h.appendChild(el('span', 'a', g.subA || '—'));
      h.appendChild(el('b', null, g.t));
      h.appendChild(el('span', 'b', g.subB || '—'));
      blk.appendChild(h);
      g.rows.forEach((r) => {
        const row = el('div', 'bcmprow sp' + (r.better.some((x) => x.n || x.c) ? ' diff' : ''));
        const side = (key) => {
          const cell = el('div', 'bcmpsp ' + key);
          const s = r[key];
          if (!s) { cell.appendChild(el('span', 'none', tr('absent de ce build'))); return cell; }
          s.forEach((ln, i) => {
            if (!ln) { cell.appendChild(el('div', 'bcmpln gap', '—')); return; }
            const b = r.better[i] || {};
            const tone = (w) => (w === key ? ' up' : w ? ' down' : '');
            const l = el('div', 'bcmpln');
            l.appendChild(el('span', 'k', ln.kind + (ln.aff ? ' · ' + ln.aff : '')));
            const cc = el('span', 'cc' + tone(b.c));
            cc.appendChild(el('b', null, ln.crit));
            cc.appendChild(el('small', null, tr('CC')));
            l.appendChild(cc);
            l.appendChild(el('b', 'nv' + tone(b.n), ln.normal));
            cell.appendChild(l);
          });
          if (!s.length) cell.appendChild(el('span', 'none', tr('pas de dégâts chiffrés')));
          return cell;
        };
        row.appendChild(side('a'));
        const mid = el('div', 'bcmpl sp');
        mid.appendChild(skillIcon({ id: r.id, name: r.name }));
        mid.appendChild(el('span', null, r.name));
        row.appendChild(mid);
        row.appendChild(side('b'));
        blk.appendChild(row);
      });
      page.appendChild(blk);
    });
  }
  box.appendChild(page);
  return box;
}

/* ---- the guided build --------------------------------------------------
   Step by step: class, level, weapons (and the off hand when the weapon
   takes one), arsenal, the two attributes then the three stats to favour,
   what the build is for (its infusions), a summary; the meter composes
   the build (builds.guided_build). */
const GUIDE = { step: 0, opts: null, cls: null, lvl: null, main: null, off: null, ars: null,
  atbs: [null, null], stats: [null, null, null], role: null, goal: 'max', name: '' };
const GUIDE_STEPS = ['Classe', 'Niveau', 'Armes', 'Arsenal', 'Attributs', 'Statistiques', 'Objectif', 'Résumé'];  // through tr() when shown

/* The goals, worded for the main attribute's role (builds.GUIDE_GOALS):
   the infusions worn, 2 / 4 / 6 pieces reaching their tiers. */
const GUIDE_ROLES = [{ v: 'DPS', t: 'Dégâts' }, { v: 'Support', t: 'Soins' }, { v: 'Tank', t: 'Tank' }];
const guideRoles = () => GUIDE_ROLES.map((r) => ({ v: r.v, t: tr(r.t) }));

/* The role: the player's pick, else the main attribute's (builds.ROLE_OF). */
function guideRole() {
  if (GUIDE.role) return GUIDE.role;
  const a = GUIDE.atbs[0];
  return a === 'Vitality' ? 'Tank' : a === 'Faith' ? 'Support' : 'DPS';
}

function guideGoals() {
  const role = { DPS: 'dps', Support: 'heal', Tank: 'tank' }[guideRole()];
  const main = { dps: 'Dégâts maximum', heal: 'Soins maximum', tank: 'Survie maximum' }[role];
  const mix = { dps: 'Dégâts, avec un peu de survie', heal: 'Soins, avec un peu de survie',
    tank: 'Survie, avec un peu de dégâts' }[role];
  const kind = tr({ dps: 'Dégâts', heal: 'Soutien', tank: 'Tank' }[role]);
  const other = tr(role === 'tank' ? 'Dégâts' : 'Tank');
  return [
    { v: 'max', t: tr(main),
      d: tr('6 pièces d’une imprégnation {kind} (paliers 2, 4 et 6), 2 d’une autre {kind} (palier 2)', { kind }) },
    { v: 'mix', t: tr(mix),
      d: tr('6 pièces d’une imprégnation {kind}, 2 d’une imprégnation {other} (palier 2)', { kind, other }) },
    { v: 'surv', t: tr(role === 'tank' ? 'Autant de dégâts que de survie' : 'Survie d’abord'),
      d: tr('4 pièces d’une imprégnation {kind}, 4 d’une imprégnation {other} (paliers 2 et 4)', { kind, other }) }];
}

function openGuide() {
  if (!window.pywebview) return;
  window.pywebview.api.call('build_guide_options', {}).then((o) => {
    if (!o) return;
    Object.assign(GUIDE, { step: 0, opts: o, cls: null, lvl: o.maxLvl, main: null, off: null,
      ars: null, atbs: [null, null], stats: [null, null, null], role: null, goal: 'max', name: '' });
    drawGuide();
  });
}

function guideWeapon(id) {
  return ((GUIDE.opts.weapons[GUIDE.cls] || []).concat(GUIDE.opts.offhands[GUIDE.cls] || []))
    .find((w) => w.v === id) || null;
}

function guideReady(step) {
  if (step === 0) return !!GUIDE.cls;
  if (step === 2) return !!GUIDE.main;
  if (step === 3) return !!GUIDE.ars;
  if (step === 4) return !!GUIDE.atbs[0];
  if (step === 5) return !!GUIDE.stats[0];
  if (step === 7) return !!GUIDE.name.trim();
  return true;
}

function drawGuide() {
  let back = $('#guidemodal');
  if (!back) {
    back = el('div', 'modalback');
    back.id = 'guidemodal';
    back.addEventListener('mousedown', (e) => { if (e.target === back) back.remove(); });
    document.body.appendChild(back);
  }
  back.textContent = '';
  const box = el('div', 'spanel bguide');
  const steps = el('div', 'bgsteps');
  GUIDE_STEPS.forEach((t, i) => {
    const s = el('span', 'bgstep' + (i === GUIDE.step ? ' on' : i < GUIDE.step ? ' done' : ''));
    s.appendChild(el('i', null, String(i + 1)));
    s.appendChild(el('b', null, tr(t)));
    steps.appendChild(s);
  });
  box.appendChild(steps);
  const body = el('div', 'bgbody fadein');
  const o = GUIDE.opts;
  const chips = (list, cur, set, exclude) => {
    const row = el('div', 'bgchips');
    list.forEach((x) => {
      if (exclude && exclude.includes(x.v)) return;
      const c = el('button', 'bgchip' + (cur === x.v ? ' on' : ''), x.t);
      c.type = 'button';
      c.addEventListener('click', () => { set(cur === x.v ? null : x.v); drawGuide(); });
      row.appendChild(c);
    });
    return row;
  };
  const weapons = (list, cur, set, exclude) => {
    const grid = el('div', 'bgweapons');
    list.forEach((w) => {
      if (exclude && exclude === w.v) return;
      const c = el('button', 'bgweapon r-' + (w.rk || 'common') + (cur === w.v ? ' on' : ''));
      c.type = 'button';
      const ic = el('span', 'gi');
      if (w.img) { const im = el('img'); im.src = w.img; im.alt = ''; ic.appendChild(im); }
      c.appendChild(ic);
      const t = el('div');
      t.appendChild(el('b', null, w.t));
      t.appendChild(el('span', null, w.type + (w.hands ? ' · ' + w.hands : '')));
      c.appendChild(t);
      c.addEventListener('click', () => { set(w.v); drawGuide(); });
      grid.appendChild(c);
    });
    return grid;
  };

  if (GUIDE.step === 0) {
    body.appendChild(el('h3', null, tr('Quelle classe ?')));
    const row = el('div', 'bgclasses');
    o.classes.forEach((c) => {
      const b = el('button', 'bgclass' + (GUIDE.cls === c.v ? ' on' : ''));
      b.type = 'button';
      b.appendChild(classEl(c.t, c.ck, 'big'));
      b.appendChild(el('b', null, c.t));
      b.addEventListener('click', () => {
        if (GUIDE.cls !== c.v) Object.assign(GUIDE, { main: null, off: null, ars: null });
        GUIDE.cls = c.v; drawGuide();
      });
      row.appendChild(b);
    });
    body.appendChild(row);
  } else if (GUIDE.step === 1) {
    body.appendChild(el('h3', null, tr('Quel niveau ?')));
    body.appendChild(slider(1, o.maxLvl, GUIDE.lvl, (v) => { GUIDE.lvl = v; }, tr('Niveau ')));
  } else if (GUIDE.step === 2) {
    body.appendChild(el('h3', null, tr('Quelle arme principale ?')));
    body.appendChild(weapons(o.weapons[GUIDE.cls] || [], GUIDE.main, (v) => {
      GUIDE.main = v; GUIDE.off = null; if (GUIDE.ars === v) GUIDE.ars = null;
    }));
    const w = guideWeapon(GUIDE.main);
    if (w && w.shield && (o.offhands[GUIDE.cls] || []).length) {
      body.appendChild(el('h3', null, tr('Et dans l’autre main ?')));
      body.appendChild(weapons(o.offhands[GUIDE.cls], GUIDE.off, (v) => { GUIDE.off = GUIDE.off === v ? null : v; }));
      body.appendChild(el('p', 'note', tr('Optionnel : un clic sur la pièce choisie la retire.')));
    }
  } else if (GUIDE.step === 3) {
    body.appendChild(el('h3', null, tr('Quelle arme d’arsenal ?')));
    body.appendChild(el('p', 'note', tr('La seconde arme, ses compétences en plus (pas la même que l’arme principale).')));
    body.appendChild(weapons(o.weapons[GUIDE.cls] || [], GUIDE.ars, (v) => { GUIDE.ars = v; }, GUIDE.main));
  } else if (GUIDE.step === 4) {
    body.appendChild(el('h3', null, tr('Attribut principal')));
    body.appendChild(chips(o.atbs, GUIDE.atbs[0], (v) => {
      GUIDE.atbs[0] = v; if (GUIDE.atbs[1] === v) GUIDE.atbs[1] = null;
    }));
    body.appendChild(el('h3', null, tr('Attribut secondaire')));
    body.appendChild(chips(o.atbs, GUIDE.atbs[1], (v) => { GUIDE.atbs[1] = v; }, [GUIDE.atbs[0]]));
  } else if (GUIDE.step === 5) {
    ['Statistique principale', 'Deuxième statistique', 'Troisième statistique'].forEach((t, i) => {
      body.appendChild(el('h3', null, tr(t)));
      const others = GUIDE.stats.filter((x, j) => j !== i && x);
      body.appendChild(chips(o.stats, GUIDE.stats[i], (v) => { GUIDE.stats[i] = v; }, others));
    });
  } else if (GUIDE.step === 6) {
    body.appendChild(el('h3', null, tr('Quel rôle ?')));
    body.appendChild(chips(guideRoles(), guideRole(), (v) => { GUIDE.role = v || guideRole(); }));
    body.appendChild(el('h3', null, tr('Que recherches-tu ?')));
    const list = el('div', 'bggoals');
    guideGoals().forEach((g) => {
      const c = el('button', 'bggoal' + (GUIDE.goal === g.v ? ' on' : ''));
      c.type = 'button';
      c.appendChild(el('b', null, g.t));
      c.appendChild(el('span', null, g.d));
      c.addEventListener('click', () => { GUIDE.goal = g.v; drawGuide(); });
      list.appendChild(c);
    });
    body.appendChild(list);
    body.appendChild(el('p', 'note', tr('Les pièces imprégnables sont épiques, prises prismatiques : '
      + 'n’importe quelle imprégnation y garde son bonus, quelle que soit la faction. Chaque pièce '
      + 'compte pour les paliers, l’imprégnation retenue est celle qui donne le plus de dégâts (de '
      + 'soins et de dégâts pour le rôle Soins), celle de survie la plus résistante.')));
  } else {
    body.appendChild(el('h3', null, tr('Comment s’appelle ton build ?')));
    const nm = el('input', 'bname bgname');
    nm.type = 'text';
    nm.maxLength = 60;
    nm.placeholder = tr('Nom du build');
    nm.value = GUIDE.name;
    nm.addEventListener('input', () => {
      GUIDE.name = nm.value;
      const go = $('#guidemodal .bgnext');
      if (go) go.disabled = !nm.value.trim();
    });
    nm.addEventListener('keydown', (e) => {
      const go = $('#guidemodal .bgnext');
      if (e.key === 'Enter' && go && !go.disabled) go.click();
    });
    body.appendChild(nm);
    setTimeout(() => nm.focus(), 0);
    body.appendChild(el('h3', null, tr('Ton build')));
    const name = (list, v) => ((list || []).find((x) => x.v === v) || {}).t || '—';
    const lines = [
      [tr('Classe'), name(o.classes, GUIDE.cls)], [tr('Niveau'), String(GUIDE.lvl)],
      [tr('Arme principale'), (guideWeapon(GUIDE.main) || {}).t || '—'],
      [tr('Autre main'), GUIDE.off ? (guideWeapon(GUIDE.off) || {}).t : '—'],
      [tr('Arsenal'), (guideWeapon(GUIDE.ars) || {}).t || '—'],
      [tr('Attributs'), GUIDE.atbs.filter(Boolean).map((a) => name(o.atbs, a)).join(tr(' puis '))],
      [tr('Statistiques'), GUIDE.stats.filter(Boolean).map((a) => name(o.stats, a)).join(tr(', puis '))],
      [tr('Rôle'), name(guideRoles(), guideRole())],
      [tr('Objectif'), name(guideGoals(), GUIDE.goal)]];
    const tbl = el('div', 'bgsum');
    lines.forEach(([k, v]) => {
      tbl.appendChild(el('span', null, k));
      tbl.appendChild(el('b', null, v));
    });
    body.appendChild(tbl);
    body.appendChild(el('p', 'note', tr('Les armes sont prises en légendaire, améliorées au maximum. Pour chaque autre '
      + 'emplacement, chaque pièce possible est essayée, puis chaque augmentation : on garde celle qui donne '
      + 'le plus de dégâts aux compétences du build (magiques si tu privilégies la perforation magique, '
      + 'physiques pour la perforation d’armure, et les soins en plus pour le rôle Soins), '
      + 'tes statistiques comptant dans ton ordre. Le bonus d’imprégnation de chaque pièce vise ta première '
      + 'statistique qu’elle n’a pas déjà. Les talents et les runes restent à choisir, tu pourras tout ajuster ensuite.')));
  }
  box.appendChild(body);

  const nav = el('div', 'bgnav');
  const cancel = el('button', 'rowbtn', tr('Annuler'));
  cancel.type = 'button';
  cancel.addEventListener('click', () => back.remove());
  nav.appendChild(cancel);
  nav.appendChild(el('span', 'sp'));
  if (GUIDE.step > 0) {
    const prev = el('button', 'rowbtn', tr('‹ Précédent'));
    prev.type = 'button';
    prev.addEventListener('click', () => { GUIDE.step--; drawGuide(); });
    nav.appendChild(prev);
  }
  const last = GUIDE.step === GUIDE_STEPS.length - 1;
  const next = el('button', 'btn bgnext', tr(last ? 'Composer le build' : 'Suivant ›'));
  next.type = 'button';
  next.disabled = !guideReady(GUIDE.step);
  next.addEventListener('click', () => {
    if (!last) { GUIDE.step++; drawGuide(); return; }
    BUILD_VIEW = 'stuff';
    notify('build_guided', { cls: GUIDE.cls, lvl: GUIDE.lvl, main: GUIDE.main, off: GUIDE.off,
      ars: GUIDE.ars, atbs: GUIDE.atbs, stats: GUIDE.stats, role: guideRole(), goal: GUIDE.goal, name: GUIDE.name.trim() });
    back.remove();
  });
  nav.appendChild(next);
  box.appendChild(nav);
  back.appendChild(box);
}

/* Paste a build's share code (from another player) to add it. */
function importCodeDialog() {
  if ($('#importmodal')) return;
  const back = el('div', 'modalback');
  back.id = 'importmodal';
  const box = el('div', 'spanel bimport');
  box.appendChild(el('div', 'sptitle', tr('Importer un build')));
  box.appendChild(el('p', 'note', tr('Colle le code qu’un joueur t’a donné (il commence par FFB1:).')));
  const ta = el('textarea');
  ta.rows = 4;
  ta.placeholder = 'FFB1:…';
  box.appendChild(ta);
  const row = el('div', 'bbtns');
  const close = () => back.remove();
  const ok = el('button', 'btn', tr('Importer'));
  ok.type = 'button';
  ok.addEventListener('click', () => {
    if (!ta.value.trim()) return;
    BUILD_VIEW = 'stuff';
    notify('build_import', { code: ta.value });
    close();
  });
  const no = el('button', 'rowbtn', tr('Annuler'));
  no.type = 'button';
  no.addEventListener('click', close);
  row.appendChild(ok);
  row.appendChild(no);
  box.appendChild(row);
  back.appendChild(box);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ok.click(); }
  });
  document.body.appendChild(back);
  setTimeout(() => ta.focus(), 0);
}

/* The builds the player is picking to delete (a Set of files), or null. */
let BUILD_DEL = null;

function redrawBuildList(box, n) {
  const page = buildList(n, el('div', 'buildpage'));
  box.replaceWith(page);
  NODES.forEach((v) => { if (v.el === box) v.el = page; });   // core.js keeps the page's nodes
}

/* A small modal: a title, a body, its buttons ([text, class, action]). */
function buildModal(id, title, fill, buttons) {
  if ($('#' + id)) return null;
  const back = el('div', 'modalback');
  back.id = id;
  const box = el('div', 'spanel bdialog');
  box.appendChild(el('div', 'sptitle', title));
  fill(box);
  const close = () => { back.remove(); document.removeEventListener('keydown', esc); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  if (buttons.length) {
    const row = el('div', 'bbtns');
    buttons.forEach(([t, cls, act]) => {
      const b = el('button', cls, t);
      b.type = 'button';
      b.addEventListener('click', () => { close(); if (act) act(); });
      row.appendChild(b);
    });
    box.appendChild(row);
  }
  back.appendChild(box);
  back.addEventListener('mousedown', (e) => { if (e.target === back) close(); });
  document.addEventListener('keydown', esc);
  document.body.appendChild(back);
  return close;
}

/* "+ Nouveau build": by hand, step by step with the guide, or the
   character in game copied (once the game has said who it is). */
function newBuildDialog(n) {
  let close = null;
  const pick = (title, text, act, off) => {
    const c = el('button', 'bnewpick' + (off ? ' off' : ''));
    c.type = 'button';
    c.appendChild(el('b', null, title));
    c.appendChild(el('span', null, text));
    if (off) c.disabled = true;
    else c.addEventListener('click', () => { if (close) close(); act(); });
    return c;
  };
  const me = (n && n.me) || null;
  close = buildModal('newbuildmodal', tr('Nouveau build'), (box) => {
    const row = el('div', 'bnewpicks');
    row.appendChild(pick(tr('Manuellement'), tr('Un build vide : tu choisis toi-même chaque pièce, '
      + 'les talents et les compétences.'), () => { BUILD_VIEW = 'stuff'; notify('build_new', {}); }));
    row.appendChild(pick(tr('Avec assistance'), tr('Quelques questions (classe, armes, attributs, '
      + 'statistiques, objectif) et le build est composé pour toi.'), openGuide));
    row.appendChild(pick(tr('Depuis mon personnage'),
      !me ? tr('Lance le jeu : ton personnage doit être identifié.')
        : me.wait ? tr('Lecture de {name}…', { name: me.n })
          : me.when ? tr('Copie l’équipement, les talents, les compétences et les runes de {name} '
              + '(le jeu est fermé : tel que lu le {when}).', { name: me.n, when: me.when })
            : tr('Copie l’équipement, les talents, les compétences et les runes de {name}.', { name: me.n }),
      () => { BUILD_VIEW = 'stuff'; notify('build_from_me', {}); }, !me || me.wait));
    box.appendChild(row);
  }, [[tr('Annuler'), 'rowbtn', null]]);
}

/* The builds picked for deletion, listed, and a last confirmation. */
function deleteBuildsDialog(list, box, n) {
  if (!list.length) return;
  const many = list.length > 1;
  buildModal('delbuildsmodal', many ? tr('Supprimer {n} builds ?', { n: list.length }) : tr('Supprimer ce build ?'), (b) => {
    const ul = el('ul', 'bdellist');
    list.forEach((x) => {
      const li = el('li');
      if (CLASS_ICONS[x.ck]) li.appendChild(classEl('', x.ck));
      li.appendChild(el('b', null, x.name));
      li.appendChild(el('span', null, tr('{cls} · niveau {lvl}',
        { cls: CLASS_NAMES[x.ck] ? tr(CLASS_NAMES[x.ck]) : x.cls || '', lvl: x.lvl || '?' })));
      ul.appendChild(li);
    });
    b.appendChild(ul);
    b.appendChild(el('p', 'note', tr(many ? 'Es-tu vraiment sûr ? Ces builds seront supprimés définitivement.'
      : 'Es-tu vraiment sûr ? Ce build sera supprimé définitivement.')));
  }, [[tr('Annuler'), 'rowbtn', null],
      [tr('Supprimer définitivement'), 'btn bdelok', () => {
        const files = list.map((x) => x.file);
        BUILD_DEL = null;
        notify('build_delete_many', { files });
        redrawBuildList(box, n);
      }]]);
}

/* The builds, as cards: a click opens one. */
function buildList(n, box) {
  const head = el('div', 'blisthead');
  head.appendChild(el('div', 'section', tr('Mes builds')));
  // importing, a new build (blue), deleting (red); comparing at the right
  const btns = el('div', 'bheadbtns');
  const imp = el('button', 'btn bimp', tr('Importer un code'));
  imp.type = 'button';
  imp.addEventListener('click', importCodeDialog);
  btns.appendChild(imp);
  const nb = el('button', 'btn bnew', tr('+ Nouveau build'));
  nb.type = 'button';
  nb.addEventListener('click', () => newBuildDialog(n));
  btns.appendChild(nb);
  // deleting: a first click to pick the builds, a second to confirm them
  const files = new Set((n.list || []).map((b) => b.file));
  if (BUILD_DEL) BUILD_DEL.forEach((f) => { if (!files.has(f)) BUILD_DEL.delete(f); });
  if ((n.list || []).length) {
    const del = el('button', 'btn bdelbtn' + (BUILD_DEL ? ' armed' : ''),
      BUILD_DEL ? (BUILD_DEL.size ? tr('Confirmer la suppression ({n})', { n: BUILD_DEL.size })
        : tr('Confirmer la suppression')) : tr('Supprimer un build'));
    del.type = 'button';
    if (BUILD_DEL && !BUILD_DEL.size) del.disabled = true;
    del.addEventListener('click', () => {
      if (!BUILD_DEL) { BUILD_DEL = new Set(); redrawBuildList(box, n); return; }
      deleteBuildsDialog(n.list.filter((b) => BUILD_DEL.has(b.file)), box, n);
    });
    btns.appendChild(del);
    if (BUILD_DEL) {
      const no = el('button', 'btn bdelno', tr('Annuler'));
      no.type = 'button';
      no.addEventListener('click', () => { BUILD_DEL = null; redrawBuildList(box, n); });
      btns.appendChild(no);
    }
  }
  const cmp = el('button', 'btn bcmpbtn', tr('Comparer'));
  cmp.type = 'button';
  cmp.title = tr('Compare deux builds de la même classe, côte à côte.');
  cmp.addEventListener('click', () => notify('build_cmp_open', {}));
  btns.appendChild(cmp);
  head.appendChild(btns);
  box.appendChild(head);
  if (BUILD_DEL) {
    box.appendChild(el('p', 'note bdelnote', BUILD_DEL.size
      ? tr('Clique sur d’autres builds pour les ajouter, ou sur un build choisi pour le retirer.')
      : tr('Clique sur les builds à supprimer.')));
  }
  if (!(n.list || []).length) {
    box.appendChild(el('p', 'note', tr('Aucun build pour l’instant. Crée-en un, copie ton '
      + 'personnage, ou pars d’un joueur analysé dans Inspecter (« Créer un build »).')));
    return box;
  }
  // one column per class, its icon and name on top, its art faint behind
  const cols = el('div', 'bcols');
  const keys = ['warrior', 'rogue', 'mage', 'priest'];
  n.list.forEach((b) => { if (!keys.includes(b.ck)) keys.push(b.ck); });
  keys.forEach((ck) => {
    const mine = n.list.filter((b) => b.ck === ck);
    const col = el('div', 'bcol');
    if (CLASS_ICONS[ck]) {
      const bg = el('img', 'bcolbg');
      bg.src = CLASS_ICONS[ck];
      bg.alt = '';
      col.appendChild(bg);
    }
    const h = el('div', 'bcolh');
    if (CLASS_ICONS[ck]) h.appendChild(classEl('', ck));
    h.appendChild(el('b', null, CLASS_NAMES[ck] ? tr(CLASS_NAMES[ck]) : (mine[0] || {}).cls || tr('Autres')));
    h.appendChild(el('span', 'n', String(mine.length)));
    col.appendChild(h);
    mine.forEach((b) => {
      const picked = BUILD_DEL && BUILD_DEL.has(b.file);
      const c = el('button', 'bcard' + (BUILD_DEL ? ' bpick' : '') + (picked ? ' bdel' : ''));
      c.type = 'button';
      const t = el('div', 'bct');
      t.appendChild(el('b', null, b.name));
      t.appendChild(el('span', null, tr('Niveau {n}', { n: b.lvl || '?' })));
      c.appendChild(t);
      if (BUILD_DEL) c.appendChild(el('i', 'bdelmark', picked ? '✓' : ''));
      c.addEventListener('click', () => {
        if (BUILD_DEL) {
          if (BUILD_DEL.has(b.file)) BUILD_DEL.delete(b.file); else BUILD_DEL.add(b.file);
          redrawBuildList(box, n);
          return;
        }
        BUILD_VIEW = 'stuff';
        notify('build_open', { file: b.file });
      });
      col.appendChild(c);
    });
    if (!mine.length) col.appendChild(el('p', 'note bcolnone', tr('Aucun build')));
    cols.appendChild(col);
  });
  box.appendChild(cols);
  return box;
}

/* Name, class, level, and the build's own buttons. */
/* The settings tab: the build's name, class and level, a line each, then
   copying or deleting it. */
function buildSettings(o) {
  const head = el('div', 'spanel bsettings');
  const row = (label, ctl) => {
    const r = el('label', 'bsrow');
    r.appendChild(el('span', 'bsl', label));
    r.appendChild(ctl);
    head.appendChild(r);
  };
  const name = el('input', 'bname');
  name.type = 'text';
  name.value = o.name;
  name.maxLength = 60;
  name.addEventListener('change', () => notify('build_rename', { value: name.value }));
  row(tr('Nom'), name);

  const cls = el('select', 'bcls');
  (o.classes || []).forEach((c) => {
    const op = el('option', null, c.t);
    op.value = c.v;
    cls.appendChild(op);
  });
  cls.value = o.cls;
  cls.addEventListener('change', () => notify('build_class', { value: cls.value }));
  row(tr('Classe'), cls);

  const lv = el('div', 'blvl');
  const out = el('b', null, tr('Niveau {n}', { n: o.lvl }));
  const r = el('input');
  r.type = 'range';
  r.min = 1; r.max = o.maxLvl; r.value = o.lvl;
  r.addEventListener('input', () => { out.textContent = tr('Niveau {n}', { n: r.value }); });
  r.addEventListener('change', () => notify('build_level', { value: Number(r.value) }));
  lv.appendChild(out);
  lv.appendChild(r);
  row(tr('Niveau'), lv);

  const btns = el('div', 'bbtns');
  const share = el('button', 'rowbtn', tr('Copier le code'));
  share.type = 'button';
  share.title = tr('Copie un code à coller à un autre joueur (Discord…) : il l’importe depuis sa liste de builds.');
  share.addEventListener('click', () => notify('build_share', {}));
  btns.appendChild(share);
  const pic = el('button', 'rowbtn', tr(o.imaging ? 'Création de l’image…' : 'Image à partager'));
  pic.type = 'button';
  pic.title = tr('Une image du build (héros en 3D, équipement, sorts, imprégnations) copiée dans le '
    + 'presse-papiers et enregistrée dans Images › Farever Book.');
  pic.disabled = !!o.imaging;
  pic.addEventListener('click', () => notify('build_image', {}));
  btns.appendChild(pic);
  const dup = el('button', 'rowbtn', tr('Dupliquer'));
  dup.type = 'button';
  dup.addEventListener('click', () => notify('build_dup', {}));
  btns.appendChild(dup);
  const del = el('button', 'rowbtn' + (o.confirmDelete ? ' armed' : ''),
    tr(o.confirmDelete ? 'Confirmer la suppression' : 'Supprimer'));
  del.type = 'button';
  del.addEventListener('click', () => notify('build_delete', {}));
  btns.appendChild(del);
  if (o.confirmDelete) {
    const no = el('button', 'rowbtn', tr('Annuler'));
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
let BUILD_JUMP = false;         // a slot or a piece was just clicked: bring it in view

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
  x.title = tr('Revenir au personnage');
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
  combo.appendChild(el('span', null, p ? p.name : tr('Choisir une pièce…')));
  combo.appendChild(el('i', 'chev', '▾'));
  combo.addEventListener('click', () => { BUILD_DROP = !BUILD_DROP; renderDrop(); combo.classList.toggle('open', BUILD_DROP); });
  pick.appendChild(combo);
  if (p) {
    const rm = el('button', 'bsquare', '×');
    rm.type = 'button';
    rm.title = tr('Retirer la pièce');
    rm.addEventListener('click', () => notify('build_unequip', {}));
    pick.appendChild(rm);
  }
  box.appendChild(pick);

  // the drop-down: search, filters, the pieces
  const drop = el('div', 'bdrop');
  const q = el('input', 'bsearch');
  q.type = 'search';
  q.placeholder = tr('Rechercher…');
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
    const fs = select([{ v: '', t: tr('Toutes les familles') }]
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
    count.textContent = tr('{n} / {all} pièces', { n: shown.length, all: (ed.options || []).length });
    if (!shown.length) list.appendChild(el('div', 'empty', ed.options.length
      ? tr('Aucune pièce ne correspond.') : tr('Aucune pièce possible ici pour l’instant.')));
    shown.forEach((it) => {
      const row = el('div', 'bitem r-' + (it.rk || 'common') + (p && p.id === it.id ? ' on' : ''));
      const ii = el('span', 'gi');
      if (it.img) { const im = el('img'); im.src = it.img; im.alt = ''; ii.appendChild(im); }
      row.appendChild(ii);
      const t = el('div', 'bt');
      t.appendChild(el('b', 'nm', it.name));
      t.appendChild(el('span', null, it.type));
      row.appendChild(t);
      row.addEventListener('click', () => {
        BUILD_DROP = false;
        BUILD_JUMP = true;
        notify('build_pick', { id: it.id });
      });
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
    body.appendChild(el('p', 'note', tr('Choisis une pièce dans la liste.')));
  } else {
    // augments and infusion: lines of the card, waiting for a choice;
    // a click opens the choices right there
    const g = p.g || {};
    const choices = [];
    (p.augs || []).forEach((a) => choices.push({
      key: 'aug:' + a.kind, t: a.t, v: a.v, options: a.options, none: tr('Aucun'),
    }));
    if (p.infusable) {
      choices.push({ key: 'inf', t: tr('Imprégnation'), v: p.inf, options: p.infOptions, none: tr('Aucune') });
      choices.push({ key: 'istat', t: tr('Bonus d’imprégnation'), v: p.istat, options: p.statOptions,
        none: tr('Aucun'), small: true,
        fx: g.inf && g.inf.bonus
          ? (g.inf.on ? '+' + (g.inf.val || '')
            : tr('{text} — inactif, faction différente', { text: '+' + (g.inf.val || '') }))
          : (g.plan ? tr('{plan} — inactif sans imprégnation', { plan: g.plan }) : '') });
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
        onWhere: () => { BUILD_WHERE = true; renderWhere(p); },
        lines,
      }));
      body.appendChild(pv);
    } else {
      lines.forEach((x) => body.appendChild(x));
    }
    if (!p.infusable && ['Head', 'Shoulders', 'Chest', 'Back', 'Hands', 'Waist', 'Legs', 'Feet'].includes(ed.slot)) {
      body.appendChild(el('p', 'note', tr('Imprégnation : sur une armure de faction, à partir de la rareté {rar}.',
        { rar: (ed.infusionMin || tr('Épique')).toLowerCase() })));
    }
  }
  box.appendChild(body);
  if (BUILD_WHERE) renderWhere(p);
  // keep where the list and the settings were scrolled, across re-renders
  list.addEventListener('scroll', () => { BUILD_SCROLL.list = list.scrollTop; });
  body.addEventListener('scroll', () => { BUILD_SCROLL.right = body.scrollTop; });
  requestAnimationFrame(() => {
    list.scrollTop = BUILD_SCROLL.list;
    body.scrollTop = BUILD_SCROLL.right;
    // after a click on a slot or a piece: the list to choose from, or the
    // piece's stats
    if (BUILD_JUMP) {
      BUILD_JUMP = false;
      (p ? body : box).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });
  return box;
}

/* "Comment l'obtenir": the piece's sources at its rarity, in a window. It
   follows the piece while open (a new rarity, another piece). */
let BUILD_WHERE = false;
const WHERE_MAPS = new Set();   // the rows whose map is unfolded
function renderWhere(p) {
  let back = $('#wheremodal');
  if (!BUILD_WHERE || !p || !p.where) {
    if (back) back.remove();
    BUILD_WHERE = false;
    WHERE_MAPS.clear();
    return;
  }
  // the page redraws often (the clock): only a new content redraws this
  const sig = JSON.stringify([p.name, p.where]);
  if (back && back.dataset.sig === sig) return;
  if (back && back.dataset.name !== p.name) WHERE_MAPS.clear();
  if (!back) {
    back = el('div', 'modalback');
    back.id = 'wheremodal';
    back.addEventListener('mousedown', (e) => { if (e.target === back) { BUILD_WHERE = false; renderWhere(null); } });
    document.body.appendChild(back);
  }
  back.textContent = '';
  back.dataset.sig = sig;
  back.dataset.name = p.name;
  const w = p.where;
  const box = el('div', 'modal wheremodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', () => { BUILD_WHERE = false; renderWhere(null); });
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, p.name));
  head.appendChild(el('div', 'whrar', tr('Comment l’obtenir en {rar}', { rar: w.rar.toLowerCase() })));
  box.appendChild(head);
  const body = el('div', 'whbody');
  let n = 0;
  const row = (r) => {
    const key = n++;
    const d = el('div', 'whrow');
    d.appendChild(el('span', 'bwtag', r.k));
    const t = el('div', 'wht');
    const top = el('div', 'whtop');
    // the cache's picture, or the dungeon boss's portrait
    // the cache's picture, the dungeon boss's portrait, or the monster's
    const pic = r.img || (r.boss && (window.__PORTRAITS__ || {})[r.boss])
      || (r.unit && (window.__BEST__ || {})[r.unit]);
    if (pic) { const im = el('img'); im.src = pic; im.alt = ''; top.appendChild(im); }
    top.appendChild(el('span', null, r.t));
    t.appendChild(top);
    if (r.sub) r.sub.split('\n').forEach((s) => t.appendChild(el('span', 'whsub', s)));
    // a recipe: each ingredient, its icon and how many
    if ((r.parts || []).length) {
      const ps = el('div', 'whparts');
      r.parts.forEach((x) => {
        const c = el('span', 'whpart');
        if (x.img) { const im = el('img'); im.src = x.img; im.alt = ''; c.appendChild(im); }
        c.appendChild(el('span', null, x.n + ' × ' + x.t));
        ps.appendChild(c);
      });
      t.appendChild(ps);
    }
    // a merchant: where it stands, on a map unfolded on demand
    if ((r.pins || []).length && w.meta) {
      const mb = el('button', 'whmapbtn', WHERE_MAPS.has(key) ? tr('Masquer la carte') : tr('Voir sur la carte'));
      mb.type = 'button';
      mb.addEventListener('click', () => {
        if (WHERE_MAPS.has(key)) WHERE_MAPS.delete(key); else WHERE_MAPS.add(key);
        back.dataset.sig = '';
        renderWhere(p);
      });
      t.appendChild(mb);
      if (WHERE_MAPS.has(key)) {
        const map = huntMiniMap({ meta: w.meta,
          insts: r.pins.map((q) => ({ t: q.t, cls: r.pinCls || 'merchant', doors: [q] })) });
        map.classList.add('whmap');
        t.appendChild(map);
        requestAnimationFrame(() => { mapIcons(); map.focusZone(null, 700); });
      }
    }
    d.appendChild(t);
    d.appendChild(el('span', 'whr', r.r));
    return d;
  };
  if (w.rows.length) {
    w.rows.forEach((r) => body.appendChild(row(r)));
  } else if (w.other.length) {
    body.appendChild(el('p', 'note warn', tr('Aucune source connue ne donne cette pièce en {rar}. Elle s’obtient en {base} :',
      { rar: w.rar.toLowerCase(), base: w.base.toLowerCase() })));
    w.other.forEach((r) => body.appendChild(row(r)));
  } else {
    body.appendChild(el('p', 'note', tr('Il semble que cet élément ne soit pas encore disponible en jeu.')));
  }
  box.appendChild(body);
  back.appendChild(box);
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
    t.appendChild(el('b', 'wait', tr('En attente de sélection')));
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
