/* The achievements page. */

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
  box.appendChild(el('div', 'section', tr('Succès')));
  box.appendChild(el('p', 'note', n.sync || ''));
  const stats = el('div', 'cards achstats');
  const pct = n.n ? Math.round(n.got / n.n * 100) : 0;
  const pic = (src, cls) => {
    if (!src) return null;
    const im = el('img', 'achstat' + (cls ? ' ' + cls : ''));
    im.src = src;
    im.alt = '';
    return im;
  };
  const ringBox = () => {
    const r = el('div', 'achstat achring');
    r.appendChild(ring(pct));
    return r;
  };
  [[tr('Points'), fmtN(n.pts || 0) + ' / ' + fmtN(n.ptsAll || 0), pic((window.__COLL__ || {}).achcat_Character)],
   [tr('Succès obtenus'), (n.got || 0) + ' / ' + (n.n || 0), pic((window.__SHEET__ || {}).ach_badge, 'badge')],
   [tr('Progression'), pctTxt(pct), ringBox()]].forEach(([t, v, icon]) => {
    const c = el('div', 'card');
    if (icon) c.appendChild(icon);
    const tx = el('div', 'achtx');
    tx.appendChild(el('div', 't', t));
    tx.appendChild(el('div', 'v', v));
    c.appendChild(tx);
    stats.appendChild(c);
  });
  box.appendChild(stats);

  const cats = el('div', 'achcats');
  [{ v: '', t: tr('Toutes'), got: n.got, n: n.n }].concat(n.cats || []).forEach((c) => {
    const b = el('button', 'achcat' + (ACH.cat === c.v ? ' on' : ''));
    b.type = 'button';
    if (c.img) {
      const src = (window.__COLL__ || {})[c.img];
      if (src) {
        const im = document.createElement('img');
        im.src = src;
        im.alt = '';
        b.appendChild(im);
      }
    }
    const t = el('span', 'ct');
    t.appendChild(el('b', null, c.t));
    t.appendChild(el('span', null, c.got + ' / ' + c.n + (c.pts != null ? ' · ' + tr('{n} pts', { n: c.pts }) : '')));
    b.appendChild(t);
    b.addEventListener('click', () => { ACH.cat = c.v; rerenderAch(); });
    cats.appendChild(b);
  });
  box.appendChild(cats);

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
