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
function buildDropTable(n) {
  const box = el('div', 'panel droptable');
  const head = el('div', 'drow dhead');
  ['', 'Objet', 'Type', 'Classes', 'Source', 'Chance', 'Quantité', 'Obtenu']
    .forEach((h) => head.appendChild(el('span', null, h)));
  box.appendChild(head);
  (n.rows || []).forEach((r) => {
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
  return box;
}

/* ---- the Collection page ------------------------------------------------ */
/* Category, filter, search and the open card live here, in the page, so
   typing and clicking never wait for the meter; the meter only says what
   exists and what is owned. */
