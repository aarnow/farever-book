/* The achievements page. */

/* Collection and Succès: the content out of reach in the game as it is (no
   way to get it in the game's data) shown or left out of lists and counts. */
function naToggle(n) {
  // a filter of its own, beside the list's: everything, or what can be had
  const seg = el('div', 'seg naseg');
  seg.title = tr('Disponible : sans ce que les données du jeu ne permettent pas d’obtenir aujourd’hui (aucune source connue), retiré des listes et des totaux');
  [[false, tr('Tout le contenu')],
   [true, tr('Disponible') + (n.na ? ' (−' + fmtN(n.na) + ')' : '')]].forEach(([v, t]) => {
    const b = el('button', !!n.hideNa === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { if (!!n.hideNa !== v) notify('hide_unavailable', { value: v }); });
    seg.appendChild(b);
  });
  return seg;
}

/* ---- achievements ------------------------------------------------------- */
const ACH = { cat: '', filter: 'todo', q: '' };
let ACH_NODE = null;

function buildAch(n) {
  ACH_NODE = n;
  const box = el('div', 'ach');
  renderAch(box, n);
  return box;
}

function rerenderAch() {
  const box = document.querySelector('.ach');
  if (box && ACH_NODE) renderAch(box, ACH_NODE);
}

function renderAch(box, n) {
  const keepQ = document.activeElement && document.activeElement.classList.contains('achq');
  const caret = keepQ ? document.activeElement.selectionStart : null;
  box.textContent = '';
  // the header, one card like a report's: who and the totals on top, the
  // categories as its tabs (readable over the page's picture)
  const head = el('div', 'bhead rhead achhead');
  box.appendChild(head);
  const top = el('div', 'achtop');
  const who = el('div', 'bwho');
  const ic = tabIcon('Achievements');
  if (ic) {
    const b = el('span', 'rhic');
    b.appendChild(ic);
    who.appendChild(b);
  }
  const wt = el('div', 'bwt');
  wt.appendChild(el('b', 'bwn', tr('Succès')));
  const ws = el('div', 'bws');
  if (n.sync) ws.appendChild(el('span', null, n.sync));
  wt.appendChild(ws);
  who.appendChild(wt);
  // who, and the option under it, beside the totals
  const left = el('div', 'achleft');
  left.appendChild(who);
  top.appendChild(left);
  const pct = n.n ? Math.round(n.got / n.n * 100) : 0;
  const facts = el('div', 'achfacts');
  const pic = (src, cls) => {
    if (!src) return null;
    const im = el('img', cls || null);
    im.src = src;
    im.alt = '';
    return im;
  };
  [[tr('Points'), fmtN(n.pts || 0) + ' / ' + fmtN(n.ptsAll || 0), pic((window.__COLL__ || {}).achcat_Character)],
   [tr('Succès obtenus'), (n.got || 0) + ' / ' + (n.n || 0), pic((window.__SHEET__ || {}).ach_badge, 'badge')],
   [tr('Progression'), pctTxt(pct), ring(pct)]].forEach(([t, v, icon]) => {
    const f = el('div', 'achfact');
    const ib = el('span', 'achfic');
    if (icon) ib.appendChild(icon);
    f.appendChild(ib);
    const tx = el('div', 'achtx');
    tx.appendChild(el('div', 't', t));
    tx.appendChild(el('div', 'v', v));
    f.appendChild(tx);
    facts.appendChild(f);
  });
  top.appendChild(facts);
  head.appendChild(top);

  const cats = el('div', 'btabs achtabs');
  [{ v: '', t: tr('Toutes'), got: n.got, n: n.n }].concat(n.cats || []).forEach((c) => {
    const b = el('button', 'btab' + (ACH.cat === c.v ? ' on' : ''));
    b.type = 'button';
    const src = c.img && (window.__COLL__ || {})[c.img];
    if (src) {
      const im = document.createElement('img');
      im.src = src;
      im.alt = '';
      b.appendChild(im);
    }
    const t = el('span', 'ct');
    t.appendChild(el('b', null, c.t));
    t.appendChild(el('small', null, c.got + ' / ' + c.n + (c.pts != null ? ' · ' + tr('{n} pts', { n: c.pts }) : '')));
    b.appendChild(t);
    b.addEventListener('click', () => { ACH.cat = c.v; rerenderAch(); });
    cats.appendChild(b);
  });
  head.appendChild(cats);

  const tools = el('div', 'colltools');
  const q = el('input', 'collq achq');
  q.type = 'text';
  q.placeholder = tr('Rechercher un succès');
  q.value = ACH.q;
  q.addEventListener('input', () => { ACH.q = q.value; rerenderAch(); });
  tools.appendChild(q);
  const seg = el('div', 'seg');
  [['todo', tr('En cours')], ['near', tr('Presque finis')], ['done', tr('Terminés')], ['all', tr('Tous')]].forEach(([v, t]) => {
    const b = el('button', ACH.filter === v ? 'on' : '', t);
    b.type = 'button';
    b.addEventListener('click', () => { ACH.filter = v; rerenderAch(); });
    seg.appendChild(b);
  });
  tools.appendChild(seg);
  tools.appendChild(naToggle(n));
  box.appendChild(tools);

  const needle = ACH.q.trim().toLowerCase();
  let shown = (n.items || []).filter((it) => (!ACH.cat || it.c === ACH.cat)
    && (ACH.filter === 'all' || (ACH.filter === 'done') === it.done)
    && (ACH.filter !== 'near' || (it.pct != null && it.pct >= 0.5))
    && (!needle || (it.name + ' ' + it.desc).toLowerCase().includes(needle)));
  if (ACH.filter === 'near') shown = shown.slice().sort((a, b) => b.pct - a.pct);
  box.appendChild(el('div', 'collcount',
    tr(shown.length === 1 ? '1 succès' : '{n} succès', { n: shown.length })));

  const list = el('div', 'achlist');
  shown.forEach((it) => {
    // styled like the game's guild-mission cards
    const card = el('div', 'achcard cat-' + it.c + (it.done ? ' done' : ''));
    const band = el('div', 'aband');
    const crest = el('span', 'crest');
    const src = (window.__COLL__ || {})['achcat_' + it.c];
    if (src) {
      const im = document.createElement('img');
      im.src = src;
      im.alt = '';
      crest.appendChild(im);
    }
    band.appendChild(crest);
    band.appendChild(el('b', 'nm', it.name));
    card.appendChild(band);
    const body = el('div', 'abody');
    if (it.subT) body.appendChild(el('span', 'sub', it.subT));
    if (it.desc) body.appendChild(el('p', 'ad', it.desc));
    if (it.need != null && !it.done) {
      const pr = el('div', 'aprog');
      const bar = el('div', 'abar');
      const fill = el('i');
      fill.style.width = Math.round((it.pct || 0) * 100) + '%';
      bar.appendChild(fill);
      pr.appendChild(bar);
      pr.appendChild(el('span', null, tr('Progression : {have} / {need}',
        { have: fmtN(Math.floor(it.have)), need: fmtN(it.need) })));
      body.appendChild(pr);
    }
    card.appendChild(body);
    const foot = el('div', 'afoot');
    const pts = el('span', 'well pts');
    pts.appendChild(el('b', null, String(it.pts)));
    pts.appendChild(el('i', 'star', '★'));
    foot.appendChild(pts);
    (it.rewards || []).forEach((r) => {
      const rw = el('span', 'well arew');
      if (r.img) {
        const im = document.createElement('img');
        im.src = r.img;
        im.alt = '';
        rw.appendChild(im);
      }
      rw.appendChild(el('span', null, r.name));
      foot.appendChild(rw);
    });
    foot.appendChild(el('span', 'spacer'));
    if ((it.tiers || []).length > 1) {
      const pips = el('span', 'apips');
      it.tiers.forEach((tier) => pips.appendChild(el('i', tier.ok ? 'on' : '')));
      foot.appendChild(pips);
    }
    if (it.done) foot.appendChild(el('span', 'adone', '✓' + (it.when ? ' ' + it.when : '')));
    card.appendChild(foot);
    list.appendChild(card);
  });
  if (!shown.length) list.appendChild(el('div', 'empty', tr('Rien à afficher.')));
  box.appendChild(list);

  if (keepQ) {
    const qi = box.querySelector('.achq');
    qi.focus();
    try { qi.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
  }
}
