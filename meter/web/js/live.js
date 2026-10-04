/* The live page: meter, player detail, loot luck, statistics, events list. */

/* ---- live nodes --------------------------------------------------------- */
function buildMeter(n) {
  const p = el('div', 'panel meter' + (n.heal ? ' heal' : ''));
  p.appendChild(el('h3', null, n.title));
  if (!n.rows || !n.rows.length) {
    p.appendChild(el('div', 'empty', n.empty));
    return p;
  }
  const head = el('div', 'mline mhead');
  ['#', 'Joueur', 'Cl.', 'Dégâts', 'DPS', '%']
    .concat(n.heal ? ['Soins', 'Excès'] : [])
    .forEach((h, i) => head.appendChild(el('span', i > 2 ? 'num' : '', h)));
  p.appendChild(head);
  n.rows.forEach((r) => {
    const row = el('div', 'mrow' + (r.me ? ' me' : '') + (r.focus ? ' focus' : ''));
    const line = el('div', 'mline');
    line.appendChild(el('span', 'rank', r.rank));
    line.appendChild(el('span', 'who', r.name));
    const c = el('span', 'cls');
    c.appendChild(classEl(r.cls, r.ck));
    line.appendChild(c);
    line.appendChild(el('span', 'num', r.dmg));
    line.appendChild(el('span', 'num', r.dps));
    line.appendChild(el('span', 'num', r.pct));
    if (n.heal) {
      line.appendChild(el('span', 'num', r.heal));
      line.appendChild(el('span', 'num', r.over));
    }
    row.appendChild(line);
    const bars = el('div', 'bars');
    bars.appendChild(bar('d', r.df));
    if (n.heal) bars.appendChild(bar('h', r.hf, r.hsf));
    row.appendChild(bars);
    row.addEventListener('click', () => notify('focus_player', { name: r.name }));
    p.appendChild(row);
  });
  return p;
}

function skillList(title, rows, kind) {
  const box = el('div');
  box.appendChild(el('h3', null, title));
  if (!rows || !rows.length) {
    box.appendChild(el('div', 'empty', '—'));
    return box;
  }
  rows.forEach((s) => {
    const sk = el('div', 'sk');
    const l = el('div', 'l');
    l.appendChild(el('span', null, s.t));
    l.appendChild(el('span', 'num', s.v));
    l.appendChild(el('span', 'num', s.pct));
    l.appendChild(el('span', 'num n', s.n + '×'));
    sk.appendChild(l);
    sk.appendChild(bar(kind, s.f, s.sf));
    box.appendChild(sk);
  });
  return box;
}

function buildDetail(n) {
  const p = el('div', 'panel detail');
  if (!n.name) {
    p.appendChild(el('h3', null, 'Détail'));
    p.appendChild(el('div', 'empty', n.empty));
    return p;
  }
  const name = el('div', 'dname', n.name);
  if (n.cls || n.ck) name.appendChild(classEl(n.cls, n.ck, 'cls'));
  p.appendChild(name);
  const stats = el('div', 'stats');
  (n.stats || []).forEach(([c, x]) => {
    const s = el('div', 'stat');
    s.appendChild(el('div', 'c', c));
    s.appendChild(el('div', 'x', x));
    stats.appendChild(s);
  });
  p.appendChild(stats);
  const cols = el('div', 'skills' + (n.heal ? '' : ' one'));
  cols.appendChild(skillList('Dégâts par sort', n.dmg, 'd'));
  if (n.heal) cols.appendChild(skillList('Soins par sort', n.heal, 'h'));
  p.appendChild(cols);
  if (n.elements && n.elements.length) {
    const e = el('div', 'elems');
    n.elements.forEach((x) => {
      const s = el('span');
      const dot = el('i');
      dot.style.background = x.c;
      s.appendChild(dot);
      s.appendChild(document.createTextNode(x.t + ' ' + x.pct));
      e.appendChild(s);
    });
    p.appendChild(e);
  }
  return p;
}

/* The local hero's loot luck counters (read by the hook every minute). */
function buildLuck(n) {
  const box = el('div', 'luckbox');
  box.appendChild(el('div', 'sub2', 'Chance de butin'));
  if (!n.rows) {
    box.appendChild(el('p', 'note', n.empty || ''));
    return box;
  }
  box.appendChild(el('p', 'note', 'Les compteurs de chance du jeu. Le bonus est lié à '
    + 'l’offrande correspondante du Puits des âmes.'));
  const lk = el('div', 'lucklist');
  n.rows.forEach((l) => {
    const row = el('div', 'luckrow' + (l.on ? ' on' : ''));
    const t = el('div', 'lt');
    t.appendChild(el('b', null, l.t));
    t.appendChild(el('span', null, l.grows
      ? 'Compteur ' + l.n + ' · +' + l.inc + ' par cran · '
        + (l.full ? 'plafond atteint' : l.steps + ' cran' + (l.steps > 1 ? 's' : '') + ' avant le plafond')
      : 'Bonus fixe'));
    row.appendChild(t);
    const v = el('div', 'lv');
    v.appendChild(el('b', null, '+' + l.bonus));
    v.appendChild(el('span', null, 'sur ' + l.cap + ' max'));
    row.appendChild(v);
    row.appendChild(el('span', 'lst' + (l.on ? ' on' : ''), l.on
      ? 'Offrande active' + (l.left != null ? ' · ' + l.left + ' min' : '')
      : 'Pas d’offrande'));
    lk.appendChild(row);
  });
  box.appendChild(lk);
  return box;
}

/* The local hero's statistics (Progress.counters). */
function buildStatCards(n) {
  const box = el('div', 'statbox');
  box.appendChild(el('div', 'sub2', 'Statistiques'));
  const st = el('div', 'cards statcards');
  (n.items || []).forEach((x) => {
    const c = el('div', 'card');
    c.appendChild(el('div', 't', x.t));
    c.appendChild(el('div', 'v', fmtN(x.v)));
    st.appendChild(c);
  });
  box.appendChild(st);
  return box;
}

/* ---- the events window ---------------------------------------------------
   Opened from the title band's journal button; follows every state push
   while open. Unseen events are counted on the button. */
function buildEvents(n) {
  const p = el('div', 'panel events');
  const head = el('div', 'phead');
  const count = n.rows && n.rows.length ? ' (' + n.rows.length + ')' : '';
  head.appendChild(el('h3', null, 'Événements' + count));
  if (count) {
    const clr = el('button', 'rowbtn', 'Effacer');
    clr.addEventListener('click', () => notify('clear_events', {}));
    head.appendChild(clr);
  }
  p.appendChild(head);
  if (!n.rows || !n.rows.length) {
    p.appendChild(el('div', 'empty',
      'Les kills de boss, records et fins de faille apparaîtront ici.'));
    return p;
  }
  /* Newest first, in a box of its own that scrolls: the page never grows
     with the feed. */
  const list = el('div', 'evlist');
  p.appendChild(list);
  n.rows.forEach((r) => {
    const row = el('div', 'ev' + (r.tone ? ' ' + r.tone : ''));
    row.appendChild(el('span', 'when', r.when));
    row.appendChild(el('span', 'txt', r.t));
    if (r.btn) {
      const b = el('button', 'rowbtn', r.btn.t);
      b.addEventListener('click', () => {
        notify(r.btn.id, r.btn.p || {});
        if (r.btn.close) toggleEvents(false);
      });
      row.appendChild(b);
    }
    list.appendChild(row);
  });
  return p;
}


/* The tab with the game off: what it shows once Farever runs. */
function buildLiveIntro(n) {
  const box = el('div', 'lintro');
  const head = el('div', 'lihead');
  head.appendChild(el('h1', null, 'En jeu'));
  head.appendChild(el('p', null, 'Ce module fonctionne en direct, pendant que vous jouez à Farever.'));
  box.appendChild(head);
  const grid = el('div', 'ligrid');
  const card = (cls, icon, title, text, extra) => {
    const c = el('div', 'licard ' + cls);
    const ic = el('span', 'liic');
    ic.innerHTML = icon;
    c.appendChild(ic);
    c.appendChild(el('h3', null, title));
    c.appendChild(el('p', null, text));
    if (extra) c.appendChild(extra);
    grid.appendChild(c);
  };
  const tags = (list) => {
    const t = el('div', 'litags');
    list.forEach((x) => t.appendChild(el('span', null, x)));
    return t;
  };
  card('fight',
    '<svg viewBox="0 0 24 24"><path d="M6.9 18.5 4 21.4 2.6 20l2.9-2.9-1.4-1.4 1.4-1.4 1.8 1.8L15.7 7.7l-.4-2.9 '
    + '3.5-2.2 2.6 2.6-2.2 3.5-2.9-.4-8.4 8.4 1.8 1.8-1.4 1.4-1.4-1.4Z"/></svg>',
    'Vos performances en combat',
    'Les dégâts et les soins, en DPS et en HPS, pour vous-même et pour votre groupe, combat par combat. '
    + 'Un clic sur un joueur détaille ses sorts, ses coups critiques, ses éléments et ses soins en excès.',
    tags(['Dégâts et DPS', 'Soins et HPS', 'Votre groupe', 'Détail par joueur']));
  card('luck',
    '<svg viewBox="0 0 24 24"><path d="M12 2 14.6 8.6 21.6 9.2 16.3 13.8 17.9 20.7 12 17 6.1 20.7 7.7 13.8 2.4 9.2 '
    + '9.4 8.6Z"/></svg>',
    'Vos chances de butin',
    'Les chances de butin de votre personnage. Elles augmentent à chaque échec, jusqu’à leur maximum, '
    + 'sur plusieurs sujets :',
    tags(n.luck || []));
  box.appendChild(grid);
  const foot = el('div', 'lifoot');
  foot.appendChild(el('span', null, 'Lancez Farever : tout apparaît ici dès que le jeu est détecté.'));
  box.appendChild(foot);
  return box;
}
