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
    wrap.appendChild(dropTable(cur.rows || [], draw));
  };
  draw();
  return wrap;
}

function dropTable(rows, redraw) {
  const box = el('div', 'panel droptable');
  const head = el('div', 'drow dhead');
  ['', 'Objet', 'Type', 'Classes', 'Source', 'Chance', 'Quantité', 'Obtenu'].forEach((h) => {
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
    const cl = el('span', 'apt');
    (r.apt || []).forEach((k) => cl.appendChild(classEl('', k)));
    row.appendChild(cl);
    row.appendChild(el('span', 'dim', r.src));
    row.appendChild(el('span', 'num', r.chance));
    row.appendChild(el('span', 'dim', r.qty));
    row.appendChild(el('span', 'num', r.got ? '×' + r.got : '—'));
    box.appendChild(row);
  });
  if (!list.length) box.appendChild(el('div', 'empty', 'Rien de connu pour cette difficulté.'));
  return box;
}

/* ---- the Collection page ------------------------------------------------ */
/* Category, filter, search and the open card live here, in the page, so
   typing and clicking never wait for the meter; the meter only says what
   exists and what is owned. */
