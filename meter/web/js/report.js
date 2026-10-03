/* Rift reports: the report page and a player's card. */

/* ---- rift report -------------------------------------------------------- */
const players = (n) => n + (n > 1 ? ' joueurs' : ' joueur');

/* The player whose report card is open, and which of their phases. */
const REPORT_SEL = { name: null, phase: 0, detail: null };

function rankTable(rows, rateLabel) {
  const box = el('div', 'tbl');
  const h = el('div', 'rk h');
  ['', 'Joueur', rateLabel, 'Total', 'Part'].forEach((t, i) =>
    h.appendChild(el('span', i > 1 ? 'num' : '', t)));
  box.appendChild(h);
  rows.forEach((r) => {
    const row = el('div', 'rk click' + (r.zero ? ' zero' : r.rank <= 3 ? ' top' : ''));
    row.title = 'Détail de ' + r.name;
    row.addEventListener('click', () => { REPORT_SEL.name = r.name; renderPlayerCard(); });
    row.appendChild(el('span', null, r.rank));
    const nm = el('span', 'nm', r.name);
    if (r.cls || r.ck) nm.appendChild(classEl(r.cls, r.ck, 'cl'));
    row.appendChild(nm);
    row.appendChild(el('span', 'num', r.rate));
    row.appendChild(el('span', 'num', r.total));
    row.appendChild(el('span', 'num', r.pct));
    box.appendChild(row);
  });
  return box;
}

function buildReport(n) {
  REPORT_SEL.detail = n.detail || null;
  const p = el('div', 'panel report');
  const t = el('div', 'rtitle');
  t.appendChild(el('b', null, n.title));
  t.appendChild(el('span', null, n.when));
  if (n.sub) t.appendChild(el('span', 'rsub', n.sub));
  p.appendChild(t);
  const cols = el('div', 'phases');
  (n.phases || []).forEach((ph) => {
    const c = el('div', 'phase');
    const top = el('div', 'phtop');
    top.appendChild(el('h4', null, ph.label));
    const facts = el('div', 'facts');
    [['Durée', ph.dur], ['DPS', ph.dps.replace(' DPS', '')],
     ['HPS', ph.hps.replace(' HPS', '')]].forEach(([k, v]) => {
      const f = el('div', 'fact');
      f.appendChild(el('span', null, k));
      f.appendChild(el('b', null, v));
      facts.appendChild(f);
    });
    top.appendChild(facts);
    top.appendChild(el('div', 'totals', ph.totals));
    c.appendChild(top);
    if (ph.mvp) {
      const podium = el('div', 'podium');
      const m = el('div', 'mvpbox');
      m.appendChild(el('span', 'lbl', 'MVP dégâts'));
      const mn = el('div', 'mvp', '★ ' + ph.mvp.name + ' ');
      if (ph.mvp.cls || ph.mvp.ck) mn.appendChild(classEl(ph.mvp.cls, ph.mvp.ck, 'big'));
      m.appendChild(mn);
      m.appendChild(el('div', 'v', ph.mvp.v));
      podium.appendChild(m);
      if (ph.healer) {
        const h = el('div', 'mvpbox heal');
        h.appendChild(el('span', 'lbl', 'MVP soins'));
        const hn = el('div', 'healer', '✚ ' + ph.healer.name + ' ');
        if (ph.healer.cls || ph.healer.ck) hn.appendChild(classEl(ph.healer.cls, ph.healer.ck, 'big'));
        h.appendChild(hn);
        h.appendChild(el('div', 'v', ph.healer.v));
        podium.appendChild(h);
      }
      c.appendChild(podium);
    } else {
      c.appendChild(el('div', 'empty', "rien n'a été enregistré pour cette phase"));
    }
    if (ph.dmg && ph.dmg.length) {
      c.appendChild(el('div', 'sub', 'Dégâts — ' + players(ph.dmg.length)));
      c.appendChild(rankTable(ph.dmg, 'DPS'));
    }
    c.appendChild(el('div', 'sub', 'Soins' + (ph.heal && ph.heal.length ? ' — ' + players(ph.heal.length) : '')));
    if (ph.heal && ph.heal.length) c.appendChild(rankTable(ph.heal, 'HPS'));
    else c.appendChild(el('div', 'empty', 'aucun soin enregistré'));
    if (ph.types && ph.types.length) {
      c.appendChild(el('div', 'sub', 'Dégâts par type'));
      const tbox = el('div', 'tbl types');
      c.appendChild(tbox);
      ph.types.forEach((x) => {
        const r = el('div', 'typ');
        const l = el('span', null, x.t);
        l.style.color = x.c;
        r.appendChild(l);
        const b = el('div', 'b');
        b.style.background = x.c;
        b.style.width = (Math.max(0.02, x.f) * 100) + '%';
        r.appendChild(b);
        r.appendChild(el('span', 'num', x.pct));
        tbox.appendChild(r);
      });
    }
    cols.appendChild(c);
  });
  p.appendChild(cols);
  if (n.loot) p.appendChild(buildLoot(n.loot));
  return p;
}

/* A player's card in a report: their damage and healing, skill by skill. */
function renderPlayerCard() {
  document.querySelectorAll('.pcshade').forEach((x) => x.remove());
  const d = REPORT_SEL.detail && REPORT_SEL.detail[REPORT_SEL.name];
  if (!d) return;
  const phases = d.phases || [];
  if (REPORT_SEL.phase >= phases.length) REPORT_SEL.phase = 0;
  const ph = phases[REPORT_SEL.phase];
  const shade = el('div', 'collshade pcshade');
  const close = () => { REPORT_SEL.name = null; shade.remove(); };
  shade.addEventListener('click', (e) => { if (e.target === shade) close(); });
  const card = el('div', 'colldetail pcard');
  const x = el('button', 'x', '×');
  x.type = 'button';
  x.addEventListener('click', close);
  card.appendChild(x);
  const h = el('div', 'pch');
  const nm = el('h3', null, d.name + ' ');
  if (d.cls || d.ck) nm.appendChild(classEl(d.cls, d.ck, 'big'));
  h.appendChild(nm);
  if (phases.length > 1) {
    const seg = el('div', 'seg');
    phases.forEach((p, i) => {
      const b = el('button', i === REPORT_SEL.phase ? 'on' : '', p.label);
      b.type = 'button';
      b.addEventListener('click', () => { REPORT_SEL.phase = i; renderPlayerCard(); });
      seg.appendChild(b);
    });
    h.appendChild(seg);
  }
  card.appendChild(h);
  const facts = el('div', 'facts');
  (ph.facts || []).forEach(([k, v]) => {
    const f = el('div', 'fact');
    f.appendChild(el('span', null, k));
    f.appendChild(el('b', null, v));
    facts.appendChild(f);
  });
  card.appendChild(facts);

  const table = (title, rows, cols) => {
    card.appendChild(el('div', 'sub2', title));
    if (!rows.length) { card.appendChild(el('p', 'none', 'rien d’enregistré')); return; }
    const t = el('div', 'tbl pctbl');
    const hd = el('div', 'pr h');
    ['Compétence', ''].concat(cols.map((c) => c[0])).forEach((c, i) =>
      hd.appendChild(el('span', i > 1 ? 'num' : '', c)));
    t.appendChild(hd);
    rows.forEach((r) => {
      const row = el('div', 'pr');
      row.appendChild(el('span', 'nm', r.n));
      const b = el('div', 'bar dmg');
      const i = el('i', 'all');
      i.style.width = (Math.max(0.01, r.f) * 100) + '%';
      b.appendChild(i);
      row.appendChild(b);
      cols.forEach((c) => row.appendChild(el('span', 'num', r[c[1]])));
      t.appendChild(row);
    });
    card.appendChild(t);
  };
  table('Sources de dégâts', ph.skills || [],
        [['Part', 'pct'], ['Dégâts', 't'], ['Coups', 'hits'], ['Crit.', 'crit'], ['Moy.', 'avg']]);
  if ((ph.heals || []).length) {
    table('Sources de soins', ph.heals, [['Part', 'pct'], ['Soins', 't'], ['Nombre', 'hits']]);
  }
  if ((ph.elements || []).length) {
    card.appendChild(el('div', 'sub2', 'Dégâts par type'));
    const tb = el('div', 'tbl types');
    ph.elements.forEach((x2) => {
      const r = el('div', 'typ');
      const l = el('span', null, x2.t);
      l.style.color = x2.c;
      r.appendChild(l);
      const b = el('div', 'b');
      b.style.background = x2.c;
      b.style.width = (Math.max(0.02, x2.f) * 100) + '%';
      r.appendChild(b);
      r.appendChild(el('span', 'num', x2.pct));
      tb.appendChild(r);
    });
    card.appendChild(tb);
  }
  shade.appendChild(card);
  document.body.appendChild(shade);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && REPORT_SEL.name) {
    REPORT_SEL.name = null;
    document.querySelectorAll('.pcshade').forEach((x) => x.remove());
  }
});

/* ---- the rifts done: a small card each, under the day ------------------- */
/* The whole card opens the rift's report; its corner box ticks it for
   deletion without opening it. */
function buildRiftCards(n) {
  const box = el('div', 'riftdays');
  (n.groups || []).forEach((g) => {
    box.appendChild(el('div', 'sub riftday', g.t));
    const grid = el('div', 'riftcards');
    g.cards.forEach((c) => {
      const card = el('div', 'riftcard' + (c.on ? ' on' : ''));
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      const open = () => notify('open_rift', { file: c.file });
      card.addEventListener('click', open);
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
      const tick = el('button', 'rtick' + (c.on ? ' on' : ''), c.on ? '✓' : '');
      tick.type = 'button';
      tick.title = c.on ? 'Ne plus sélectionner' : 'Sélectionner (pour supprimer)';
      tick.addEventListener('click', (e) => {
        e.stopPropagation();
        notify('rift_tick', { file: c.file });
      });
      card.appendChild(tick);
      card.appendChild(el('div', 'rtime', c.time));
      const facts = el('div', 'rfacts');
      const fact = (label, value) => {
        const f = el('div', 'rfact');
        f.appendChild(el('b', null, value));
        f.appendChild(el('span', null, label));
        facts.appendChild(f);
      };
      fact('durée', c.dur);
      fact(c.players > 1 ? 'joueurs' : 'joueur', String(c.players));
      fact(c.gates === 1 ? 'portail' : 'portails', c.gates == null ? '—' : String(c.gates));
      card.appendChild(facts);
      grid.appendChild(card);
    });
    box.appendChild(grid);
  });
  if (!(n.groups || []).length) box.appendChild(el('div', 'empty', n.empty || ''));
  return box;
}
