/* The world map: tiles, markers, filters and pan/zoom. The tiles sit in a
   transformed layer; the points in their own, in screen pixels, so they keep
   their size at every zoom. */

const MAP = { s: null, x: 0, y: 0, on: {}, reg: 'all', sel: null, node: null,
              hideFound: false };

function mapPx(n, x, y) {
  const m = n.meta, k = m.tile_px / m.units_per_tile;
  return [(x - m.tx[0] * m.units_per_tile) * k, (y - m.ty[0] * m.units_per_tile) * k];
}

function buildMap(n) {
  MAP.node = n;
  (n.cats || []).forEach((c) => { if (!(c.v in MAP.on)) MAP.on[c.v] = true; });
  const m = n.meta || {};
  const wrap = el('div', 'mapwrap');
  const vp = el('div', 'mapvp');
  const world = el('div', 'mworld');
  const size = m.tile_px || 512;
  (m.tiles || []).forEach((key) => {
    const [tx, ty] = key.split('_').map(Number);
    const im = document.createElement('img');
    im.className = 'mtile';
    im.dataset.key = key;
    im.alt = '';
    im.style.left = (tx - m.tx[0]) * size + 'px';
    im.style.top = (ty - m.ty[0]) * size + 'px';
    // one pixel of overlap: scaled, tiles laid edge to edge show seams
    im.style.width = im.style.height = (size + 1) + 'px';
    if (window.__MAP__[key]) im.src = window.__MAP__[key];
    world.appendChild(im);
  });
  vp.appendChild(world);
  // with the progress read, what is still to get is greyed out
  const marks = el('div', 'mmarks' + (n.known ? ' known' : ''));
  (n.points || []).forEach((p, i) => {
    const d = el('div', 'mk mk-' + p.c + (p.f ? ' found' : ''));
    d.dataset.i = i;
    const [px, py] = mapPx(n, p.x, p.y);
    d.dataset.px = px;
    d.dataset.py = py;
    d.addEventListener('click', (e) => { e.stopPropagation(); MAP.sel = i; mapPopup(); });
    marks.appendChild(d);
  });
  vp.appendChild(marks);
  wrap.appendChild(vp);
  wrap.appendChild(mapPanel(n));
  wrap.appendChild(el('div', 'mpop'));
  mapInteract(vp);
  requestAnimationFrame(() => { if (MAP.s === null) mapFit(); mapApply(); mapPopup(); });
  return wrap;
}

function mapTiles() {
  document.querySelectorAll('.mtile').forEach((im) => {
    const src = window.__MAP__[im.dataset.key];
    if (src && !im.src) im.src = src;
  });
  mapIcons();
}

/* The game's own markers (icon_<category> among the map pictures), as one
   style rule each: they reach the points, the legend and the popup alike. */
function mapIcons() {
  let st = document.getElementById('mapicons');
  if (!st) {
    st = document.createElement('style');
    st.id = 'mapicons';
    document.head.appendChild(st);
  }
  st.textContent = Object.keys(window.__MAP__ || {})
    .filter((k) => k.indexOf('icon_') === 0)
    .map((k) => '.mk.mk-' + k.slice(5) + ',.hdoor.hd-' + k.slice(5)
      + '{background:url(' + window.__MAP__[k]
      + ') center/contain no-repeat;border:0;border-radius:0;clip-path:none}')
    .join(' ');
}

function mapPanel(n) {
  // only the body scrolls, its bar clear of the panel's rounded corners
  const panel = el('div', 'mpanel');
  panel.appendChild(el('div', 'mtitle', tr('Carte de Siagarta')));
  const p = el('div', 'mbody');
  panel.appendChild(p);
  const inReg = (pt) => MAP.reg === 'all' || pt.r === MAP.reg;
  const pts = n.points || [];
  // "found / total" when the progress has been read, the total otherwise
  const tally = (list) => n.known
    ? list.filter((pt) => pt.f).length + ' / ' + list.length : String(list.length);
  if (n.known) {
    const mine = pts.filter(inReg);
    const got = mine.filter((pt) => pt.f).length;
    const pct = mine.length ? Math.round(got / mine.length * 100) : 0;
    const prog = el('div', 'mprog');
    prog.appendChild(el('b', null, got + ' / ' + mine.length));
    prog.appendChild(el('span', null, pctTxt(pct)));
    p.appendChild(prog);
    const bar_ = el('div', 'collbar');
    const fill = el('i');
    fill.style.width = pct + '%';
    bar_.appendChild(fill);
    p.appendChild(bar_);
  }
  p.appendChild(el('div', 'msync', n.sync || ''));
  const regs = el('div', 'mregs');
  [{ v: 'all', t: tr('Tout Siagarta') }].concat(n.regions || []).forEach((r) => {
    const b = el('button', MAP.reg === r.v ? 'on' : '', r.t);
    b.type = 'button';
    b.addEventListener('click', () => { MAP.reg = r.v; mapRefreshPanel(); mapApply(); });
    regs.appendChild(b);
  });
  p.appendChild(regs);
  if (n.known) {
    const hf = el('button', 'mhide' + (MAP.hideFound ? ' on' : ''),
      MAP.hideFound ? tr('✓ Éléments trouvés masqués') : tr('Masquer les éléments trouvés'));
    hf.type = 'button';
    hf.addEventListener('click', () => { MAP.hideFound = !MAP.hideFound; mapRefreshPanel(); mapApply(); });
    p.appendChild(hf);
  }
  const all = el('div', 'mall');
  [[tr('Tout afficher'), true], [tr('Tout masquer'), false]].forEach(([t, v]) => {
    const b = el('button', null, t);
    b.type = 'button';
    b.addEventListener('click', () => {
      (n.cats || []).forEach((c) => { MAP.on[c.v] = v; });
      mapRefreshPanel(); mapApply();
    });
    all.appendChild(b);
  });
  p.appendChild(all);
  let group = null, grid = null;
  (n.cats || []).forEach((c) => {
    if (c.g !== group) {
      group = c.g;
      const shown = pts.filter((pt) => inReg(pt)
        && (n.cats.find((x) => x.v === pt.c) || {}).g === group);
      const h = el('div', 'mgroup');
      h.appendChild(el('b', null, group));
      h.appendChild(el('span', null, tally(shown)));
      p.appendChild(h);
      grid = el('div', 'mcats');
      p.appendChild(grid);
    }
    const cnt = tally(pts.filter((pt) => pt.c === c.v && inReg(pt)));
    const b = el('button', 'mcat' + (MAP.on[c.v] ? ' on' : ''));
    b.type = 'button';
    b.appendChild(el('i', 'mk mk-' + c.v));
    b.appendChild(el('span', 'ml', c.t));
    b.appendChild(el('span', 'mn', cnt));
    b.addEventListener('click', () => { MAP.on[c.v] = !MAP.on[c.v]; mapRefreshPanel(); mapApply(); });
    grid.appendChild(b);
  });
  p.appendChild(el('div', 'mhint', tr('Glisser pour déplacer · molette pour zoomer · clic sur un point pour le détail')));
  return panel;
}

function mapRefreshPanel() {
  const old = document.querySelector('.mpanel');
  if (!old || !MAP.node) return;
  // a click in the list keeps it where it was scrolled
  const top = (old.querySelector('.mbody') || {}).scrollTop || 0;
  const panel = mapPanel(MAP.node);
  old.replaceWith(panel);
  panel.querySelector('.mbody').scrollTop = top;
}

function mapFit() {
  const vp = document.querySelector('.mapvp');
  const n = MAP.node;
  if (!vp || !n || !(n.points || []).length) return;
  const w = vp.clientWidth, h = vp.clientHeight;
  if (!w || !h) return;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  n.points.forEach((p) => {
    const [px, py] = mapPx(n, p.x, p.y);
    x0 = Math.min(x0, px); y0 = Math.min(y0, py);
    x1 = Math.max(x1, px); y1 = Math.max(y1, py);
  });
  const pad = 40;
  MAP.s = Math.min((w - 2 * pad) / (x1 - x0), (h - 2 * pad) / (y1 - y0));
  MAP.x = (w - (x1 - x0) * MAP.s) / 2 - x0 * MAP.s;
  MAP.y = (h - (y1 - y0) * MAP.s) / 2 - y0 * MAP.s;
}

function mapApply() {
  const world = document.querySelector('.mworld');
  if (!world) return;
  // framed on the first call with a real size (a hidden window has none)
  if (MAP.s === null) mapFit();
  if (MAP.s === null) return;
  world.style.transform = `translate(${MAP.x}px, ${MAP.y}px) scale(${MAP.s})`;
  const pts = (MAP.node && MAP.node.points) || [];
  document.querySelectorAll('.mmarks .mk').forEach((d) => {
    const p = pts[d.dataset.i];
    const show = p && MAP.on[p.c] && (MAP.reg === 'all' || p.r === MAP.reg)
      && !(MAP.hideFound && p.f);
    d.style.display = show ? '' : 'none';
    if (!show) return;
    d.style.transform = `translate(${d.dataset.px * MAP.s + MAP.x}px, ${d.dataset.py * MAP.s + MAP.y}px)`;
    d.classList.toggle('sel', +d.dataset.i === MAP.sel);
  });
  mapPopupPlace();
}

function mapZoom(f, cx, cy) {
  if (MAP.s === null) mapFit();
  if (MAP.s === null) return;
  const s = Math.min(3, Math.max(0.08, MAP.s * f));
  f = s / MAP.s;
  MAP.x = cx - (cx - MAP.x) * f;
  MAP.y = cy - (cy - MAP.y) * f;
  MAP.s = s;
  mapApply();
}

function mapInteract(vp) {
  let drag = null;
  vp.addEventListener('pointerdown', (e) => {
    if (e.target.classList.contains('mk')) return;
    drag = { x: e.clientX, y: e.clientY, mx: MAP.x, my: MAP.y, moved: false };
    vp.setPointerCapture(e.pointerId);
    vp.classList.add('drag');
  });
  vp.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    MAP.x = drag.mx + dx;
    MAP.y = drag.my + dy;
    mapApply();
  });
  const end = () => {
    if (drag && !drag.moved) { MAP.sel = null; mapPopup(); }
    drag = null;
    vp.classList.remove('drag');
  };
  vp.addEventListener('pointerup', end);
  vp.addEventListener('pointercancel', end);
  vp.addEventListener('wheel', (e) => {
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    mapZoom(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  vp.addEventListener('dblclick', (e) => {
    const r = vp.getBoundingClientRect();
    mapZoom(2, e.clientX - r.left, e.clientY - r.top);
  });
}

function mapPopup() {
  const pop = document.querySelector('.mpop');
  const n = MAP.node;
  if (!pop || !n) return;
  const p = MAP.sel !== null ? n.points[MAP.sel] : null;
  pop.textContent = '';
  pop.style.display = p ? '' : 'none';
  if (!p) { mapApply(); return; }
  const c = (n.cats || []).find((x) => x.v === p.c) || {};
  const h = el('div', 'ph');
  h.appendChild(el('i', 'mk mk-' + p.c));
  h.appendChild(el('b', null, c.t || p.c));
  pop.appendChild(h);
  const reg = (n.regions || []).find((r) => r.v === p.r);
  pop.appendChild(el('div', 'pz', [p.z, reg && reg.v !== 'other' ? reg.t : ''].filter(Boolean).join(' — ') || tr('Zone inconnue')));
  if (n.known) pop.appendChild(el('div', 'pf' + (p.f ? ' on' : ''), p.f ? tr('✓ Trouvé') : tr('Pas encore trouvé')));
  pop.appendChild(el('div', 'pc', tr('n° {n} · x {x}, y {y}',
    { n: p.n, x: Math.round(p.x), y: Math.round(p.y) })));
  mapApply();
}

function mapPopupPlace() {
  const pop = document.querySelector('.mpop');
  const n = MAP.node;
  if (!pop || !n || MAP.sel === null || pop.style.display === 'none') return;
  const [px, py] = mapPx(n, n.points[MAP.sel].x, n.points[MAP.sel].y);
  pop.style.transform = `translate(${px * MAP.s + MAP.x + 14}px, ${py * MAP.s + MAP.y - 20}px)`;
}

window.addEventListener('resize', () => { if (document.querySelector('.mapvp')) mapApply(); });
