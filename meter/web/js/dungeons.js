/* Dungeons: a run's loot and a dungeon's loot table. */

/* A dungeon run's loot: one block per phase, the reward chest first. */
function buildLoot(groups) {
  const box = el('div', 'loot');
  box.appendChild(el('h4', null, 'Butin'));
  if (!groups.length) {
    box.appendChild(el('div', 'empty', "aucun objet ramassé pendant ce run"));
    return box;
  }
  groups.forEach((g) => {
    box.appendChild(el('div', 'sub', g.t));
    const tbl = el('div', 'tbl lootlist');
    g.items.forEach((it) => {
      const r = el('div', 'lootrow' + (it.rk ? ' r-' + it.rk : ''));
      const nm = el('span', 'nm');
      const ic = el('span', 'ic');
      if (it.img) {
        const im = document.createElement('img');
        im.src = it.img;
        im.alt = '';
        ic.appendChild(im);
      }
      nm.appendChild(ic);
      nm.appendChild(el('span', 'n', it.name));
      r.appendChild(nm);
      r.appendChild(el('span', 'rar', [it.rarity, it.level].filter(Boolean).join(' · ')));
      r.appendChild(el('span', 'num', '×' + it.qty));
      tbl.appendChild(r);
    });
    box.appendChild(tbl);
  });
  return box;
}

/* A dungeon's possible loot: icon, name (rarity colour), type, classes,
   source, chance, quantity, how many the saved runs brought back. */
let DROP_DIFF = 0;          // the difficulty whose table is shown
let DROP_SORT = 0;          // the chance column: 0 as given, 1 up, -1 down

/* The loot tables, one per difficulty (its skull a tab), the chance
   column sortable both ways. Kept here, so switching never waits. */
function buildDropTable(n) {
  const wrap = el('div', 'dropwrap');
  const tables = n.tables || [{ d: 0, t: '', rows: n.rows || [] }];
  if (!tables.some((t) => t.d === DROP_DIFF)) DROP_DIFF = tables[0].d;
  const draw = () => {
    wrap.textContent = '';
    const tabs = el('div', 'droptabs');
    tables.forEach((t) => {
      const b = el('button', 'droptab d' + t.d + (t.d === DROP_DIFF ? ' on' : ''));
      b.type = 'button';
      const art = (window.__SHEET__ || {})['dungeon_diff_' + t.d];
      if (art) { const im = document.createElement('img'); im.src = art; im.alt = ''; b.appendChild(im); }
      b.appendChild(el('span', null, t.t));
      b.addEventListener('click', () => {
        if (DROP_DIFF === t.d) return;
        DROP_DIFF = t.d;
        draw();
        const tbl = wrap.querySelector('.droptable');
        if (tbl) tbl.classList.add('fadein');
      });
      tabs.appendChild(b);
    });
    if (tables.length > 1) wrap.appendChild(tabs);
    const cur = tables.find((t) => t.d === DROP_DIFF) || tables[0];
    wrap.appendChild(dropTable(cur.rows || [], draw, n.lite));
  };
  draw();
  return wrap;
}

/* `lite`: the item, its type, its chance and how many — no classes, source
   nor what we got (the rifts' chests). */
function dropTable(rows, redraw, lite) {
  const box = el('div', 'panel droptable' + (lite ? ' lite' : ''));
  const head = el('div', 'drow dhead');
  (lite ? ['', 'Objet', 'Type', 'Chance', 'Quantité']
    : ['', 'Objet', 'Type', 'Classes', 'Source', 'Chance', 'Quantité', 'Obtenu']).forEach((h) => {
    if (h !== 'Chance') { head.appendChild(el('span', null, h)); return; }
    const b = el('button', 'dsort' + (DROP_SORT ? ' on' : ''),
      'Chance' + (DROP_SORT === 1 ? ' ▲' : DROP_SORT === -1 ? ' ▼' : ' ↕'));
    b.type = 'button';
    b.title = 'Trier par chance (croissant, décroissant, d’origine)';
    b.addEventListener('click', () => { DROP_SORT = DROP_SORT === 0 ? 1 : DROP_SORT === 1 ? -1 : 0; redraw(); });
    head.appendChild(b);
  });
  box.appendChild(head);
  const list = rows.slice();
  if (DROP_SORT) list.sort((a, b) => DROP_SORT * ((a.cv ?? -1) - (b.cv ?? -1)));
  list.forEach((r) => {
    const row = el('div', 'drow' + (r.rk ? ' r-' + r.rk : '') + (r.got ? ' got' : ''));
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
    if (!lite) {
      const cl = el('span', 'apt');
      (r.apt || []).forEach((k) => cl.appendChild(classEl('', k)));
      row.appendChild(cl);
      row.appendChild(el('span', 'dim', r.src));
    }
    row.appendChild(el('span', 'num', r.chance));
    row.appendChild(el('span', 'dim', r.qty));
    if (!lite) row.appendChild(el('span', 'num', r.got ? '×' + r.got : '—'));
    box.appendChild(row);
  });
  if (!list.length) box.appendChild(el('div', 'empty', 'Rien de connu pour cette difficulté.'));
  return box;
}

/* ---- the Collection page ------------------------------------------------ */
/* Category, filter, search and the open card live here, in the page, so
   typing and clicking never wait for the meter; the meter only says what
   exists and what is owned. */

/* ---- the boss sheet (bosssheet.py) --------------------------------------
   The boss's portrait and name, its health by group size, defences, power
   and critical hits, each skill with what it deals, what it summons, the
   statuses it applies and its phases. Pictures from the game: the portrait,
   the difficulty's skull, the skills' icons (a glyph when the game has none,
   by element). */
const BS_GLYPH = {
  phys: 'M6.9 18.5 4 21.4 2.6 20l2.9-2.9-1.4-1.4 1.4-1.4 1.8 1.8L15.7 7.7l-.4-2.9 3.5-2.2 2.6 2.6-2.2 3.5-2.9-.4-8.4 8.4 1.8 1.8-1.4 1.4-1.4-1.4Z',
  magic: 'M8 3s-4 4.6-4 7.3A4 4 0 0 0 8 14.3a4 4 0 0 0 4-4C12 7.6 8 3 8 3Zm8 6s-4 4.6-4 7.3a4 4 0 0 0 8 0C20 13.6 16 9 16 9Z',
  heal: 'M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7V3Z',
  unit: 'M9 2h6v3h2v3h-2v3h2v3h-2v8H9v-8H7v-3h2V8H7V5h2V2Zm2 3v2h2V5h-2Zm0 6v2h2v-2h-2Z',
};

function bsIcon(id, glyph, small) {
  const box = el('span', 'bsic' + (small ? ' sm' : ''));
  if (id) {
    const im = el('img', 'skic');
    im.dataset.id = id;
    im.alt = '';
    const src = (window.__SKILL__ || {})[id];
    if (src) im.src = src;
    box.appendChild(im);
  } else {
    box.classList.add('glyph', glyph);
    box.appendChild(svgIcon(BS_GLYPH[glyph] || BS_GLYPH.phys));
  }
  return box;
}

function bsTile(title, value, sub) {
  const t = el('div', 'bstile');
  t.appendChild(el('div', 't', title));
  if (value != null) t.appendChild(el('div', 'v', value));
  if (sub) sub.split('\n').forEach((line) => t.appendChild(el('div', 's', line)));
  return t;
}

function bsSkill(s, small) {
  const row = el('div', 'bsskill' + (small ? ' sm' : ''));
  row.appendChild(bsIcon(s.icon, s.heal ? 'heal' : s.magic ? 'magic' : 'phys', small));
  const t = el('div', 'bst');
  const nm = el('div', 'bsn', s.name);
  if (s.aff) nm.appendChild(el('span', 'bsaff ' + (s.magic ? 'magic' : 'phys'),
    s.aff + (s.magic && s.aff !== 'Magique' ? ' · magique' : '')));
  t.appendChild(nm);
  if (s.fx) t.appendChild(el('div', 'bsfx', s.fx));
  if ((s.tags || []).length) {
    const tags = el('div', 'bstags');
    s.tags.forEach((x) => tags.appendChild(el('span', null, x)));
    t.appendChild(tags);
  }
  row.appendChild(t);
  if (s.v) {
    const d = el('div', 'bsd' + (s.heal ? ' heal' : ''));
    d.appendChild(el('b', null, s.v));
    if (s.per) d.appendChild(el('small', null, s.per));
    if (s.coef) d.appendChild(el('span', null, s.coef));
    row.appendChild(d);
  }
  return row;
}

function buildBossSheet(n) {
  const box = el('div', 'bsheet');
  const head = el('div', 'bshead');
  const pic = el('div', 'bspor');
  const src = (window.__PORTRAITS__ || {})[n.boss];
  if (src) { const im = el('img'); im.src = src; im.alt = ''; pic.appendChild(im); }
  head.appendChild(pic);
  const ht = el('div', 'bsht');
  const kick = el('div', 'bskick');
  const skull = (window.__SHEET__ || {})['dungeon_diff_' + (n.heroic ? 2 : 0)];
  if (skull) { const im = el('img'); im.src = skull; im.alt = ''; kick.appendChild(im); }
  kick.appendChild(document.createTextNode((n.heroic ? 'Héroïque' : 'Normal') + ' · niveau ' + n.level));
  ht.appendChild(kick);
  ht.appendChild(el('div', 'bsname', n.name));
  head.appendChild(ht);
  box.appendChild(head);

  const stats = el('div', 'bsstats');
  const hp = bsTile('Points de vie');
  (n.hp || []).forEach((r) => {
    const line = el('div', 'bshp');
    line.appendChild(el('span', null, r.n + ' joueur' + (r.n > 1 ? 's' : '')));
    line.appendChild(el('b', null, r.v));
    hp.appendChild(line);
  });
  stats.appendChild(hp);
  stats.appendChild(bsTile('Armure', n.armor, '−' + n.armorPct + ' % de dégâts physiques\nà niveau égal'));
  stats.appendChild(bsTile('Résistance magique', n.magicPct + ' %', '−' + n.magicPct + ' % de dégâts magiques\n(eau, feu, lumière…)'));
  stats.appendChild(bsTile('Puissance', n.power, 'Base de tous ses dégâts'));
  if (n.crit) {
    stats.appendChild(bsTile('Critiques', n.crit.chance + ' %', 'dégâts ×' + n.crit.mult
      + ' à niveau égal\n+' + n.crit.step + ' % de chances et de dégâts par niveau d’avance sur sa cible'));
  }
  box.appendChild(stats);

  box.appendChild(el('div', 'bssec', 'Compétences'));
  (n.skills || []).forEach((s) => box.appendChild(bsSkill(s)));

  (n.summons || []).forEach((u) => {
    box.appendChild(el('div', 'bssec', 'Invocation'));
    const c = el('div', 'bssummon');
    const who = el('div', 'bsw');
    who.appendChild(bsIcon(null, 'unit'));
    const wt = el('div');
    wt.appendChild(el('div', 'bsn', u.name));
    wt.appendChild(el('div', 'bsfx', u.by));
    who.appendChild(wt);
    c.appendChild(who);
    const st = el('div', 'bsmini');
    [['PV', u.hp], ['Armure', u.armor + '  (−' + u.armorPct + ' %)'], ['Rés. magique', u.magic + ' %'],
      ['Puissance', u.power]].forEach(([k, v]) => {
      const d = el('div');
      d.appendChild(el('span', null, k));
      d.appendChild(el('b', null, v));
      st.appendChild(d);
    });
    c.appendChild(st);
    const sk = el('div', 'bsmsk');
    (u.skills || []).forEach((s) => sk.appendChild(bsSkill(s, true)));
    c.appendChild(sk);
    box.appendChild(c);
  });

  const row = el('div', 'bsrow');
  if ((n.statuses || []).length) {
    const col = el('div');
    col.appendChild(el('div', 'bssec', 'Affaiblissement'));
    n.statuses.forEach((s) => {
      const c = el('div', 'bsstatus');
      c.appendChild(bsIcon(s.id, 'magic'));
      const t = el('div');
      t.appendChild(el('div', 'bsn', s.name));
      t.appendChild(el('div', 'bsfx', s.t));
      c.appendChild(t);
      col.appendChild(c);
    });
    row.appendChild(col);
  }
  if ((n.phases || []).length) {
    const col = el('div');
    col.appendChild(el('div', 'bssec', 'Phases (PV du boss)'));
    const tl = el('div', 'bsphases');
    const track = el('div', 'bstrack');
    tl.appendChild(track);
    [{ at: 100, t: 'Début' }].concat(n.phases).forEach((p, i) => {
      const m = el('div', 'bsmark' + (i === 0 ? ' first' : ''));
      m.style.left = (100 - p.at) + '%';
      m.appendChild(el('i'));
      m.appendChild(el('b', null, p.at + ' %'));
      // the skill's first word: the marks can stand close together
      const lab = el('span', null, (p.t || '').split(' ')[0]);
      lab.title = p.t || '';
      m.appendChild(lab);
      tl.appendChild(m);
    });
    const wrap = el('div', 'bstile');
    wrap.appendChild(tl);
    col.appendChild(wrap);
    row.appendChild(col);
  }
  box.appendChild(row);
  box.appendChild(el('div', 'bsfoot', 'Valeurs calculées depuis les données du jeu, dégâts avant l’armure et les résistances du joueur touché.'));
  return box;
}
