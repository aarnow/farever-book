/* Rift reports: the report page and a player's card. */

/* ---- rift report -------------------------------------------------------- */
/* The player whose report card is open, and which of their phases. */
const REPORT_SEL = { name: null, phase: 0, detail: null };

/* A ranking, a bar per player as in the game's meters, filled in the
   class colour relative to the best. */
function rankTable(rows, rateLabel) {
  const box = el('div', 'tbl rktbl');
  const h = el('div', 'rk h');
  h.appendChild(el('span', 'rkic'));
  const hb = el('div', 'rkbar');
  [tr('Joueur'), rateLabel, tr('Total'), tr('Part')].forEach((t, i) => hb.appendChild(el('span', i ? 'num' : '', t)));
  h.appendChild(hb);
  box.appendChild(h);
  rows.forEach((r) => {
    const row = el('div', 'rk click rkfill' + (r.zero ? ' zero' : r.rank <= 3 ? ' top' : '')
      + (r.ck ? ' c-' + r.ck : ''));
    row.title = tr('Détail de {name}', { name: r.name });
    row.addEventListener('click', () => { REPORT_SEL.name = r.name; renderPlayerCard(); });
    const ic = el('span', 'rkic');
    if (r.cls || r.ck) ic.appendChild(classEl(r.cls, r.ck, 'rkicon'));
    row.appendChild(ic);
    const bar = el('div', 'rkbar');
    row.style.setProperty('--fill', (r.zero ? 0 : Math.max(0, Math.min(1, r.f || 0)) * 100) + '%');
    bar.appendChild(el('span', 'nm', r.rank + '. ' + r.name));
    bar.appendChild(el('span', 'num', r.rate));
    bar.appendChild(el('span', 'num', r.total));
    bar.appendChild(el('span', 'num', r.pct));
    row.appendChild(bar);
    box.appendChild(row);
  });
  return box;
}

/* Damage by type: a ring chart with its legend. */
function typePie(types) {
  const box = el('div', 'typepie');
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 120 120');
  svg.setAttribute('class', 'pie');
  const r = 46, len = 2 * Math.PI * r;
  const sum = types.reduce((a, x) => a + (x.s || 0), 0) || 1;
  let at = 0;
  const arcs = [];
  types.forEach((x, i) => {
    const part = (x.s || 0) / sum;
    if (part <= 0) return;
    const arc = document.createElementNS(ns, 'circle');
    arc.dataset.i = i;
    arcs.push(arc);
    arc.setAttribute('cx', 60);
    arc.setAttribute('cy', 60);
    arc.setAttribute('r', r);
    arc.setAttribute('fill', 'none');
    arc.setAttribute('stroke', x.c);
    arc.setAttribute('stroke-width', 22);
    // a hair of gap between arcs, but never more than the arc itself
    const gap = Math.min(1.2, part * len / 3);
    arc.setAttribute('stroke-dasharray', (part * len - gap) + ' ' + (len - part * len + gap));
    arc.setAttribute('stroke-dashoffset', -at * len);
    arc.setAttribute('transform', 'rotate(-90 60 60)');
    const tip = document.createElementNS(ns, 'title');
    tip.textContent = x.t + ' — ' + x.pct;
    arc.appendChild(tip);
    svg.appendChild(arc);
    at += part;
  });
  box.appendChild(svg);
  const legend = el('div', 'pielegend');
  // a type pointed at in the legend: the others fade in the ring
  const focus = (i) => arcs.forEach((a) => a.classList.toggle('dim', i != null && a.dataset.i !== String(i)));
  types.forEach((x, i) => {
    const row = el('div', 'pl');
    row.addEventListener('mouseenter', () => focus(i));
    row.addEventListener('mouseleave', () => focus(null));
    const dot = el('i');
    dot.style.background = x.c;
    row.appendChild(dot);
    row.appendChild(el('span', null, x.t));
    row.appendChild(el('b', null, x.pct));
    legend.appendChild(row);
  });
  box.appendChild(legend);
  return box;
}

function buildReport(n) {
  REPORT_SEL.detail = n.detail || null;
  const p = el('div', 'panel report');
  const t = el('div', 'section rtitle', n.title);
  t.appendChild(el('span', 'rwhen', n.when));
  if (n.sub) t.appendChild(el('span', 'rsub', n.sub));
  p.appendChild(t);
  const cols = el('div', 'phases');
  (n.phases || []).forEach((ph) => {
    const c = el('div', 'phase');
    const top = el('div', 'phtop');
    top.appendChild(el('h4', null, ph.label));
    const facts = el('div', 'facts');
    [[tr('Durée'), ph.dur], ['DPS', ph.dps.replace(' DPS', '')],
     ['HPS', ph.hps.replace(' HPS', '')]].forEach(([k, v]) => {
      const f = el('div', 'fact');
      f.appendChild(el('span', null, k));
      f.appendChild(el('b', null, v));
      facts.appendChild(f);
    });
    top.appendChild(facts);
    c.appendChild(top);
    if (ph.empty) c.appendChild(el('div', 'empty', tr("rien n'a été enregistré pour cette phase")));
    if (ph.dmg && ph.dmg.length) {
      c.appendChild(el('div', 'sub rtab', tr('Dégâts')));
      c.appendChild(rankTable(ph.dmg, 'DPS'));
    }
    c.appendChild(el('div', 'sub rtab', tr('Soins')));
    if (ph.heal && ph.heal.length) c.appendChild(rankTable(ph.heal, 'HPS'));
    else c.appendChild(el('div', 'empty', tr('aucun soin enregistré')));
    if (ph.types && ph.types.length) {
      c.appendChild(el('div', 'sub rtab', tr('Dégâts par type')));
      c.appendChild(typePie(ph.types));
    }
    cols.appendChild(c);
  });
  // a dungeon run: its loot as a second column
  if (n.loot) cols.appendChild(buildLoot(n.loot));
  p.appendChild(cols);
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

  // same bars as the report's rankings, one per skill
  const table = (title, rows, cols) => {
    card.appendChild(el('div', 'sub2 rtab', title));
    if (!rows.length) { card.appendChild(el('p', 'none', tr('rien d’enregistré'))); return; }
    const t = el('div', 'tbl rktbl pctbl');
    const grid = 'minmax(110px, 1fr) repeat(' + cols.length + ', 62px)';
    const hd = el('div', 'rk h');
    hd.appendChild(el('span', 'rkic'));
    const hb = el('div', 'rkbar');
    hb.style.gridTemplateColumns = grid;
    [tr('Compétence')].concat(cols.map((c) => c[0])).forEach((c, i) => hb.appendChild(el('span', i ? 'num' : '', c)));
    hd.appendChild(hb);
    t.appendChild(hd);
    const top = Math.max(...rows.map((r) => r.f || 0)) || 1;
    rows.forEach((r) => {
      const row = el('div', 'rk rkfill' + (d.ck ? ' c-' + d.ck : ''));
      row.style.setProperty('--fill', (Math.max(0, Math.min(1, (r.f || 0) / top)) * 100) + '%');
      if (r.c) row.style.setProperty('--cc', r.c);
      const ic = el('span', 'rkic');
      const src = (r.ids || []).map((id) => (window.__SKILL__ || {})[id]).find(Boolean);
      if (src) {
        const im = el('img', 'rkicon skic');
        im.src = src;
        im.alt = '';
        ic.appendChild(im);
      } else {
        ic.appendChild(el('span', 'rkicon'));   // the square, empty
      }
      row.appendChild(ic);
      const bar = el('div', 'rkbar');
      bar.style.gridTemplateColumns = grid;
      bar.appendChild(el('span', 'nm', r.n));
      cols.forEach((c) => bar.appendChild(el('span', 'num', r[c[1]])));
      row.appendChild(bar);
      t.appendChild(row);
    });
    card.appendChild(t);
  };
  table(tr('Sources de dégâts'), ph.skills || [],
        [[tr('Part'), 'pct'], [tr('Dégâts'), 't'], [tr('Coups'), 'hits'], [tr('Crit.'), 'crit'], [tr('Moy.'), 'avg']]);
  if ((ph.heals || []).length) {
    table(tr('Sources de soins'), ph.heals, [[tr('Part'), 'pct'], [tr('Soins'), 't'], [tr('Nombre'), 'hits']]);
  }
  if ((ph.elements || []).length) {
    card.appendChild(el('div', 'sub2 rtab', tr('Dégâts par type')));
    // here each type's f is already its share of the player's damage
    card.appendChild(typePie(ph.elements.map((x2) => Object.assign({}, x2, { s: x2.f }))));
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
/* The card opens the report; its corner box selects it for deletion.
   Dungeon runs reuse it with their own `open` action (and no tick box). */
function buildRiftCards(n) {
  const box = el('div', 'riftdays');
  (n.groups || []).forEach((g) => {
    box.appendChild(el('div', 'sub riftday', g.t));
    const grid = el('div', 'riftcards');
    g.cards.forEach((c) => {
      const card = el('div', 'riftcard' + (c.on ? ' on' : ''));
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      const open = () => notify(c.open || 'open_rift', { file: c.file });
      card.addEventListener('click', open);
      card.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
      if (!c.open) {
        const tick = el('button', 'rtick' + (c.on ? ' on' : ''), c.on ? '✓' : '');
        tick.type = 'button';
        tick.title = c.on ? tr('Ne plus sélectionner') : tr('Sélectionner (pour supprimer)');
        tick.addEventListener('click', (e) => {
          e.stopPropagation();
          notify('rift_tick', { file: c.file });
        });
        card.appendChild(tick);
      }
      const tm = el('div', 'rtime', c.time);
      if (c.star) {
        const st = el('span', 'rstar', '★');
        st.title = tr('Record de cette difficulté');
        tm.appendChild(st);
      }
      card.appendChild(tm);
      if (c.diff) {
        const tag = el('div', 'rdiff d' + c.diff.d + ' r-' + (c.result || '').toLowerCase());
        const art = (window.__SHEET__ || {})['dungeon_diff_' + c.diff.d];
        if (art) {
          const im = document.createElement('img');
          im.src = art;
          im.alt = '';
          tag.appendChild(im);
        }
        tag.appendChild(el('span', 'dt', c.diff.t));
        if (c.result) tag.appendChild(el('span', 'res', tr(c.result)));
        card.appendChild(tag);
      }
      const facts = el('div', 'rfacts');
      const fact = (label, value) => {
        const f = el('div', 'rfact');
        f.appendChild(el('b', null, value));
        f.appendChild(el('span', null, label));
        facts.appendChild(f);
      };
      if (c.facts) {
        c.facts.forEach(([label, value]) => fact(label, value));
      } else {
        fact(tr('durée'), c.dur);
        fact(c.players > 1 ? tr('joueurs') : tr('joueur'), String(c.players));
        fact(c.gates === 1 ? tr('portail') : tr('portails'), c.gates == null ? '—' : String(c.gates));
      }
      card.appendChild(facts);
      if (c.group) {
        const g = el('div', 'rgroup', c.group);
        g.title = c.group;
        card.appendChild(g);
      }
      grid.appendChild(card);
    });
    box.appendChild(grid);
  });
  if (!(n.groups || []).length) box.appendChild(el('div', 'empty', n.empty || ''));
  return box;
}
