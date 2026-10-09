/* The window's frame and title band: tabs as icons, the game state, the
   events and connection windows, moving and resizing the window. */

let EVENTS_OPEN = false;
let EVENTS_SEEN = null;         // how many events there were when last seen

function eventsButton() {
  const b = el('button', 'navicon evbtn' + (EVENTS_OPEN ? ' active' : ''));
  b.type = 'button';
  b.title = tr('Événements');
  b.setAttribute('aria-label', tr('Événements'));
  b.appendChild(svgIcon(JOURNAL_ICON));
  b.addEventListener('click', () => toggleEvents(!EVENTS_OPEN));
  return b;
}

function updateEventsBadge() {
  const b = document.querySelector('#appbtns .evbtn');
  if (!b) return;
  const n = (STATE.events || []).length;
  if (EVENTS_SEEN === null) EVENTS_SEEN = n;      // what was there at start
  if (EVENTS_OPEN) EVENTS_SEEN = n;
  let badge = b.querySelector('.badge');
  const unseen = Math.max(0, n - EVENTS_SEEN);
  if (!unseen) { if (badge) badge.remove(); return; }
  if (!badge) { badge = el('span', 'badge'); b.appendChild(badge); }
  badge.textContent = unseen > 9 ? '9+' : String(unseen);
}

function toggleEvents(open) {
  EVENTS_OPEN = open;
  let m = $('#evmodal');
  if (!open) {
    if (m) m.remove();
  } else if (!m) {
    m = el('div', 'modalback');
    m.id = 'evmodal';
    m.addEventListener('mousedown', (e) => { if (e.target === m) toggleEvents(false); });
    document.body.appendChild(m);
  }
  const b = document.querySelector('#appbtns .evbtn');
  if (b) b.classList.toggle('active', open);
  renderEvents();
  updateEventsBadge();
}

function renderEvents() {
  const m = $('#evmodal');
  if (!m) return;
  const rows = STATE.events || [];
  const sig = JSON.stringify(rows);
  if (m.dataset.sig === sig) return;
  m.dataset.sig = sig;
  m.textContent = '';
  const box = el('div', 'modal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', () => toggleEvents(false));
  box.appendChild(x);
  const p = buildEvents({ rows: rows.map((r) => r.btn ? Object.assign({}, r, {
    btn: Object.assign({}, r.btn, { close: true }) }) : r) });
  box.appendChild(p);
  m.appendChild(box);
}

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (EVENTS_OPEN) toggleEvents(false);
  if (LINK_OPEN) toggleLinkSteps(false);
});

/* ---- the connection window: what the link is doing, step by step ------- */
let LINK_OPEN = false;
const STEP_MARK = { ok: '✓', fail: '✕', run: '', wait: '' };

function toggleLinkSteps(open) {
  LINK_OPEN = open;
  let m = $('#linkmodal');
  if (!open) {
    if (m) m.remove();
    return;
  }
  if (!m) {
    m = el('div', 'modalback');
    m.id = 'linkmodal';
    m.addEventListener('mousedown', (e) => { if (e.target === m) toggleLinkSteps(false); });
    document.body.appendChild(m);
  }
  renderLinkSteps();
}

function renderLinkSteps() {
  const m = $('#linkmodal');
  if (!m) return;
  const steps = STATE.linksteps || [];
  const l = STATE.link || {};
  const sig = JSON.stringify([steps, l]);
  if (m.dataset.sig === sig) return;
  m.dataset.sig = sig;
  m.textContent = '';
  const box = el('div', 'modal linkmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', () => toggleLinkSteps(false));
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, tr('Connexion au jeu')));
  box.appendChild(head);
  const body = el('div', 'stepbody');
  const started = steps.some((st) => st.s !== 'wait');
  if (!started) {
    body.appendChild(el('p', 'note', l.state === 'play' || l.state === 'launching'
      ? tr('Farever n’est pas encore détecté : la connexion commencera dès son lancement.')
      : tr('Aucune connexion en cours.')));
  }
  const list = el('ol', 'steps');
  steps.forEach((st) => {
    const li = el('li', 'step s-' + st.s);
    const mk = el('span', 'stmk', STEP_MARK[st.s] || '');
    li.appendChild(mk);
    const t = el('div', 'st');
    t.appendChild(el('b', null, st.t));
    if (st.d) t.appendChild(el('span', null, st.d));
    li.appendChild(t);
    li.appendChild(el('span', 'secs', st.secs || ''));
    list.appendChild(li);
  });
  body.appendChild(list);
  if (l.state === 'failed' && l.tip) body.appendChild(el('p', 'note warn', l.tip));
  box.appendChild(body);
  m.appendChild(box);
}

const TAB_ICONS = {
  Settings: 'M19.14 12.94c.04-.31.06-.63.06-.94 0-.32-.02-.63-.06-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.04.31-.07.63-.07.94s.02.63.06.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z',
  Help: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H8c0-2.21 1.79-4 4-4s4 1.79 4 4c0 .88-.36 1.68-.93 2.25z',
};

// a list sheet: the events journal
const JOURNAL_ICON = 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-2h2v2zm0-4H7v-2h2v2zm0-4H7V7h2v2zm8 8h-6v-2h6v2zm0-4h-6v-2h6v2zm0-4h-6V7h6v2z';

function svgIcon(d) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', d);
  svg.appendChild(path);
  return svg;
}

/* A tab's glyph (the game's, white, drawn in the tab's colour), or null. */
function tabIcon(key) {
  const src = (window.__TAB_ICONS__ || {})[key];
  if (!src) return null;
  const i = el('i', 'tabic');
  i.style.setProperty('--ic', 'url("' + src + '")');
  return i;
}

/* Tabs are built once and kept, so the active colour can ease in and the
   gold bar (#navink) can slide between them. */
const NAV_BTNS = {};
let NAV_ACTIVE = null;

function renderTabs(tabs, active) {
  const nav = $('#nav');
  const sig = JSON.stringify(tabs);
  if (nav.dataset.sig !== sig) {
    nav.dataset.sig = sig;
    nav.textContent = '';
    const icons = $('#appbtns');
    icons.textContent = '';
    Object.keys(NAV_BTNS).forEach((k) => delete NAV_BTNS[k]);
    Object.keys(NAV_GROUPS).forEach((k) => delete NAV_GROUPS[k]);
    tabs.forEach((tab) => {
      const t = typeof tab === 'string' ? tab : tab.v;
      const label = typeof tab === 'string' ? tab : tab.t;
      let b;
      if (tab.grp) {
        // a tab of a group: an entry of its menu, under the group's tab
        const g = navGroup(nav, tab.grp, tab.grpT || tab.grp);
        b = el('button', 'navitem');
        const ic = tabIcon(t);
        if (ic) b.appendChild(ic);
        b.appendChild(el('span', null, label));
        g.menu.appendChild(b);
      } else if (TAB_ICONS[t]) {
        b = el('button', 'navicon');
        b.title = label;
        b.setAttribute('aria-label', label);
        b.appendChild(svgIcon(TAB_ICONS[t]));
        icons.appendChild(b);
      } else {
        b = el('button');
        const ic = tabIcon(t);
        if (ic) b.appendChild(ic);
        b.appendChild(el('span', null, label));
        nav.appendChild(b);
      }
      b.type = 'button';
      b.addEventListener('click', () => {
        navMenusClose();
        if (t !== NAV_ACTIVE) {
          showTab(t);
          $('#page').classList.add('leaving');
        }
        notify('set_tab', { value: t });
      });
      NAV_BTNS[t] = b;
    });
    nav.appendChild(el('span', null)).id = 'navink';
    icons.appendChild(eventsButton());
    NAV_ACTIVE = null;
  }
  if (active !== NAV_ACTIVE) showTab(active);
  watchNav();
}

/* A group of tabs (the account's): one tab in the band, its menu under
   it, opened by a click, closed by a pick, a click elsewhere or Escape. */
const NAV_GROUPS = {};
function navGroup(nav, key, label) {
  if (NAV_GROUPS[key]) return NAV_GROUPS[key];
  const wrap = el('div', 'navgroup');
  const btn = el('button', 'navgrp');
  btn.type = 'button';
  const ic = tabIcon(key);
  if (ic) btn.appendChild(ic);
  btn.appendChild(el('span', null, label));
  btn.appendChild(el('i', 'caret'));
  btn.setAttribute('aria-haspopup', 'true');
  btn.setAttribute('aria-expanded', 'false');
  const menu = el('div', 'navmenu');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !wrap.classList.contains('open');
    navMenusClose();
    // under its tab, from the band's edge (the band holds it)
    menu.style.left = (wrap.offsetLeft + wrap.offsetWidth / 2) + 'px';
    wrap.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
  });
  wrap.appendChild(btn);
  wrap.appendChild(menu);
  nav.appendChild(wrap);
  NAV_GROUPS[key] = { wrap: wrap, btn: btn, menu: menu };
  return NAV_GROUPS[key];
}
function navMenusClose() {
  Object.values(NAV_GROUPS).forEach((g) => {
    g.wrap.classList.remove('open');
    g.btn.setAttribute('aria-expanded', 'false');
  });
}
document.addEventListener('click', (e) => {
  if (!e.target.closest || !e.target.closest('.navgroup')) navMenusClose();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') navMenusClose(); });

function showTab(t) {
  const first = NAV_ACTIVE === null;
  NAV_ACTIVE = t;
  Object.keys(NAV_BTNS).forEach((k) => NAV_BTNS[k].classList.toggle('active', k === t));
  const ink = $('#navink');
  let b = NAV_BTNS[t];
  // a tab of a group: the group's tab lit, the bar under it
  Object.values(NAV_GROUPS).forEach((g) => g.btn.classList.toggle('active', !!b && g.menu.contains(b)));
  const grp = b && Object.values(NAV_GROUPS).find((g) => g.menu.contains(b));
  if (grp) b = grp.btn;
  if (!ink) return;
  if (!b || b.classList.contains('navicon')) {   // Réglages, Aide: no bar
    ink.style.opacity = '0';
    return;
  }
  // the first placement does not slide in from the left edge
  ink.classList.toggle('still', first || ink.style.opacity === '0');
  // on the band's bottom edge, or right under the tab when the tabs wrap
  const nav = $('#nav');
  const rows = new Set(Object.values(NAV_BTNS).filter((x) => x.parentNode === nav)
    .concat(Object.values(NAV_GROUPS).map((g) => g.wrap))
    .map((x) => x.offsetTop)).size;
  const y = rows > 1 ? b.offsetTop + b.offsetHeight - 3 : nav.clientHeight - 3;
  // (a group's tab too: its wrapper isn't positioned, the band is)
  const left = b.offsetLeft;
  ink.style.width = Math.max(0, b.offsetWidth - 28) + 'px';
  ink.style.transform = 'translate(' + (left + 14) + 'px, ' + y + 'px)';
  ink.style.opacity = '1';
}

/* The bar follows its tab when the band reflows (resize, zoom, wrap). */
let NAV_WATCH = null;
function watchNav() {
  if (NAV_WATCH) return;
  NAV_WATCH = new ResizeObserver(() => {
    const t = NAV_ACTIVE;
    if (!t) return;
    NAV_ACTIVE = null;
    showTab(t);
    $('#navink').classList.add('still');      // a reflow is not a move
  });
  NAV_WATCH.observe($('#nav'));
}

/* The game's state in the title band: Play (launches Farever through
   Steam), launching, connecting, "En jeu" over the server, or retry. */
function renderLink(l, shard) {
  const box = $('#link');
  box.textContent = '';
  box.className = 'link-' + (l.state || 'play');
  if (l.state === 'play' || l.state === 'failed') {
    const b = el('button', 'playbtn' + (l.state === 'failed' ? ' retry' : ''));
    b.type = 'button';
    if (l.state === 'play') {
      const tri = el('i', 'tri');
      b.appendChild(tri);
    }
    b.appendChild(el('span', null, l.state === 'play' ? tr('Jouer') : tr('Réessayer')));
    if (l.tip) b.title = l.tip;
    b.addEventListener('click', () => notify(l.state === 'play' ? 'launch_game' : 'link_retry', {}));
    box.appendChild(b);
    if (l.state === 'failed') {
      // the error, to paste in a message
      const c = el('button', 'copyerr');
      c.type = 'button';
      c.title = tr('Copier l’erreur');
      c.appendChild(svgIcon('M9 9h10v12H9zM5 15V3h10'));
      c.addEventListener('click', () => notify('copy_error', {}));
      box.appendChild(c);
    }
    return;
  }
  const st = el('div', 'gamestate');
  st.title = tr('Voir le détail de la connexion');
  st.addEventListener('click', () => toggleLinkSteps(true));
  const top = el('div', 'gs');
  top.appendChild(el('i', 'dot'));
  top.appendChild(el('b', null, l.t || ''));
  st.appendChild(top);
  if (l.state === 'ingame' && shard) st.appendChild(el('span', 'srv', tr('Serveur {shard}', { shard: shard })));
  else if (l.tip) st.appendChild(el('span', 'srv', l.tip));
  box.appendChild(st);
}

/* The Steam account under the game's state: the one signed in, else the
   last one seen, else none (app.py _account_view). */
function renderAccount(a) {
  const box = $('#account');
  if (!box) return;
  box.textContent = '';
  box.className = 'acct-' + (a.state || 'none');
  if (a.state === 'on' || a.state === 'sync') {
    box.appendChild(document.createTextNode(a.state === 'on'
      ? tr('Connecté en tant que') + ' ' : tr('Compte synchronisé :') + ' '));
    // its name: Réglages › Compte (its characters, the one synced)
    const b = el('button', 'acctlink', a.name || '?');
    b.type = 'button';
    b.title = tr('Voir le compte et ses personnages');
    b.addEventListener('click', () => notify('open_account', {}));
    box.appendChild(b);
  } else {
    box.textContent = tr('Aucun compte Steam n’est synchronisé avec l’application.');
  }
  headerWrap();
}

/* The frameless window's drag, resize and caption buttons, all through the
   host (menu_host.Api.win). */
function winCall(action, arg) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.win) return Promise.resolve(false);
  return api.win(action, arg === undefined ? null : arg);
}

function setMaxState(isMax) {
  document.body.classList.toggle('maxed', !!isMax);
  const b = document.querySelector('#winctl [data-win="max"]');
  if (!b) return;
  b.dataset.i18nTitle = isMax ? 'Restaurer' : 'Agrandir';   // for translateStatic
  b.title = tr(b.dataset.i18nTitle);
  // Windows' own: one square to maximise, two stacked to restore
  b.innerHTML = isMax
    ? '<svg viewBox="0 0 12 12"><rect x="2" y="4" width="6" height="6"/><path d="M4 4V2h6v6H8"/></svg>'
    : '<svg viewBox="0 0 12 12"><rect x="2.5" y="2.5" width="7" height="7"/></svg>';
}

/* At most one rectangle per frame, never before the last one is done:
   keeps up without flooding the bridge. */
function followMouse(e, place) {
  winCall('rect').then((r) => {
    if (!r) return;
    const sx = e.screenX, sy = e.screenY;
    let last = null, busy = false, frame = 0;
    const flush = () => {
      frame = 0;
      if (!last || busy) return;
      const want = last;
      last = null;
      busy = true;
      winCall(want[0], want[1]).finally(() => { busy = false; if (last) flush(); });
    };
    const move = (ev) => {
      last = place(r, ev.screenX - sx, ev.screenY - sy);
      if (!frame) frame = requestAnimationFrame(flush);
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
}

const MIN_W = 720, MIN_H = 480;

function resizePlace(edge) {
  return (r, dx, dy) => {
    let [x, y, w, h] = r;
    if (edge.includes('e')) w = Math.max(MIN_W, r[2] + dx);
    if (edge.includes('s')) h = Math.max(MIN_H, r[3] + dy);
    if (edge.includes('w')) { w = Math.max(MIN_W, r[2] - dx); x = r[0] + r[2] - w; }
    if (edge.includes('n')) { h = Math.max(MIN_H, r[3] - dy); y = r[1] + r[3] - h; }
    return ['setrect', [x, y, w, h]];
  };
}

function initWindowFrame() {
  const top = $('#titlebar');
  top.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.detail > 1) return;
    if (e.target.closest('button')) return;
    if (document.body.classList.contains('maxed')) return;
    e.preventDefault();
    followMouse(e, (r, dx, dy) => ['move', [r[0] + dx, r[1] + dy]]);
  });
  top.addEventListener('dblclick', (e) => {
    if (e.target.closest('button, a, input, select')) return;
    winCall('max').then(setMaxState);
  });
  document.querySelectorAll('#winctl [data-win]').forEach((b) => {
    b.addEventListener('click', () => winCall(b.dataset.win).then(setMaxState));
  });
  document.querySelectorAll('.grip').forEach((g) => {
    g.addEventListener('mousedown', (e) => {
      if (e.button !== 0 || document.body.classList.contains('maxed')) return;
      e.preventDefault();
      followMouse(e, resizePlace(g.dataset.edge));
    });
  });
}

/* The first launch: the welcome screen, alone in the window (no tabs) until
   the game's data has been read (app.py _setup_*). */
function welcomePick() {
  if (!window.pywebview || !window.pywebview.api.pick_folder) return;
  window.pywebview.api.pick_folder().then((p) => {
    if (p) notify('setup_folder', { path: p });
  });
}

/* The progress screen is kept between pushes and updated in place, so its
   animations don't restart. */
let WELCOME_RUN = null;

function updateWelcomeRun(w, n) {
  const pct = n.pct || 0;
  w.pct.textContent = pctTxt(pct);
  w.fill.style.width = Math.max(2, pct) + '%';
  (n.rows || []).forEach((r, i) => {
    const li = w.rows[i];
    if (!li) return;
    li.className = 'w' + r.s;
    li.querySelector('.wmark').textContent = r.s === 'done' ? '✓' : '';
    const has = r.n !== null && r.n !== undefined;
    li.querySelector('b').textContent = has
      ? tr(r.n > 1 ? '{n} images' : '{n} image', { n: fmtN(r.n) }) : '';
  });
}

function buildWelcome(n) {
  if (n.stage !== 'run') WELCOME_RUN = null;
  else if (WELCOME_RUN && WELCOME_RUN.box.isConnected
           && WELCOME_RUN.rows.length === (n.rows || []).length) {
    updateWelcomeRun(WELCOME_RUN, n);
    return WELCOME_RUN.box;
  }
  const box = el('div', 'welcome');
  const card = el('div', 'wcard');
  const btn = (text, cls, fn) => {
    const b = el('button', cls, text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  };

  const repair = n.mode === 'repair';
  if (repair) {
    if (n.stage === 'done') {
      card.appendChild(el('h1', null, tr('Réparation terminée')));
      card.appendChild(el('p', 'wlead', tr('Les données du jeu ont été relues et la connexion au jeu '
        + 'relancée.')));
      card.appendChild(el('p', 'wtext', tr('Si le problème persiste, contactez @Aarnow sur Discord.')));
      card.appendChild(btn(tr('Revenir à l’application'), 'wgo', () => notify('setup_finish', {})));
      box.appendChild(card);
      return box;
    }
    card.appendChild(el('h1', null, n.stage === 'error' ? tr('La réparation n’a pas abouti')
      : tr('Réparation de Farever Book')));
    card.appendChild(el('p', 'wlead', n.stage === 'error'
      ? tr('La nouvelle analyse du jeu s’est arrêtée avant la fin.')
      : tr('Nouvelle analyse du jeu pour résoudre les problèmes rencontrés.')));
  }
  if (n.stage === 'done') {
    card.appendChild(el('h1', null, tr('Tout est prêt !')));
    card.appendChild(el('p', 'wlead', tr('Merci d’utiliser Farever Book. Les images, les icônes et les '
      + 'données du jeu sont en place, tous les modules sont accessibles.')));
    card.appendChild(el('p', 'wgame', tr('Bon jeu sur Farever !')));
    card.appendChild(btn(tr('Commencer'), 'wgo', () => notify('setup_finish', {})));
    box.appendChild(card);
    return box;
  }

  if (!repair) {
    // the languages first: the welcome is read in the one picked
    if ((n.langs || []).length) {
      const lr = el('div', 'wlangs');
      n.langs.forEach((it) => {
        const b = el('button', 'wlang' + (it.id === n.lang ? ' on' : ''));
        b.type = 'button';
        b.title = it.t;
        const f = el('span', 'lflag');
        f.innerHTML = LANG_FLAGS[it.id] || '';
        b.appendChild(f);
        b.appendChild(el('span', null, it.t));
        b.addEventListener('click', () => { if (it.id !== n.lang) notify('set_lang', { id: it.id }); });
        lr.appendChild(b);
      });
      card.appendChild(lr);
    }
    card.appendChild(el('h1', null, tr('Bienvenue sur Farever Book')));
    card.appendChild(el('p', 'wlead', tr('L’outil qui vous accompagne dans vos aventures sur Farever !')));
  }

  if (n.stage === 'run') {
    const ring = el('div', 'wring');
    ring.appendChild(el('i'));
    const pct = el('b');
    ring.appendChild(pct);
    card.appendChild(ring);
    card.appendChild(el('p', 'wtext', tr('Récupération des données du jeu, une trentaine de secondes.')));
    const bar = el('div', 'wbar');
    const fill = el('i');
    bar.appendChild(fill);
    card.appendChild(bar);
    const list = el('ul', 'wrows');
    const rows = (n.rows || []).map((r) => {
      const li = el('li');
      li.appendChild(el('i', 'wmark'));
      li.appendChild(el('span', null, r.t));
      li.appendChild(el('b'));
      list.appendChild(li);
      return li;
    });
    card.appendChild(list);
    box.appendChild(card);
    WELCOME_RUN = { box, pct, fill, rows };
    updateWelcomeRun(WELCOME_RUN, n);
    return box;
  }

  if (!repair) {
    card.appendChild(el('p', 'wtext', tr('Pour accéder aux divers modules, nous avons besoin d’accéder '
      + 'au dossier du jeu Farever afin d’y récupérer :')));
    const ul = el('ul', 'wneeds');
    (n.needs || []).forEach((t) => ul.appendChild(el('li', null, t)));
    card.appendChild(ul);
    card.appendChild(el('p', 'wsafe', tr('Le jeu est seulement lu : rien n’y est modifié, et rien ne '
      + 'quitte votre ordinateur.')));
  }

  if (n.stage === 'error') {
    card.appendChild(el('p', 'werr', tr('La récupération n’a pas abouti. Vérifiez que Farever est à '
      + 'jour, puis réessayez. Si le problème persiste, contactez @Aarnow sur Discord.')));
  }
  if (n.stage === 'locate') {
    card.appendChild(el('p', 'wwarn', tr('Nous n’avons pas trouvé Farever sur cet ordinateur. '
      + 'Indiquez-nous son dossier, celui qui contient Farever.exe.')));
    if (n.err) card.appendChild(el('p', 'werr', n.err));
    card.appendChild(btn(tr('Parcourir…'), 'wgo', welcomePick));
  } else {
    const where = el('div', 'wpath');
    where.appendChild(el('span', null, tr('Dossier du jeu')));
    where.appendChild(el('b', null, n.path || ''));
    const other = btn(tr('Ce n’est pas le bon dossier ?'), 'wlink', welcomePick);
    where.appendChild(other);
    card.appendChild(where);
    if (n.err) card.appendChild(el('p', 'werr', n.err));
    card.appendChild(btn(n.stage === 'error' ? tr('Réessayer') : tr('Autoriser et commencer'), 'wgo',
      () => notify('setup_start', {})));
  }
  if (repair) {
    // a repair can always be left: the app was working before it
    const back = btn(tr('Revenir à l’application'), 'wlink wback', () => notify('setup_finish', {}));
    card.appendChild(back);
  }
  box.appendChild(card);
  return box;
}


/* Réglages: the subjects, a menu down the page's left. */
const SETNAV_ICONS = {
  account: '<svg viewBox="0 0 24 24"><path d="M12 3a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zm0 11c4.4 0 8 2.2 8 5v2H4v-2c0-2.8 '
    + '3.6-5 8-5z"/></svg>',
  meter: '<svg viewBox="0 0 24 24"><path d="M3 20h18v2H3zM5 11h3v8H5zm5.5-5h3v13h-3zM16 14h3v5h-3z"/></svg>',
  overlay: '<svg viewBox="0 0 24 24"><path d="M3 4h18v12H3zm2 2v8h14V6zm3 12h8v2H8z"/>'
    + '<path d="M12 7h5v4h-5z"/></svg>',
  display: '<svg viewBox="0 0 24 24"><path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-5 2 3h-4zm0 20-2-3h4zM2 12l3-2v4zm20 '
    + '0-3 2v-4z"/></svg>',
  config: '<svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v12H3zm2 4v8h14V9z"/></svg>',
};

function buildSetNav(n) {
  const nav = el('nav', 'setnav');
  (n.items || []).forEach((it) => {
    const b = el('button', 'setitem' + (it.id === n.on ? ' on' : ''));
    b.type = 'button';
    const ic = el('span', 'setic');
    ic.innerHTML = SETNAV_ICONS[it.id] || '';
    b.appendChild(ic);
    b.appendChild(el('span', null, it.t));
    if (it.tag) b.appendChild(el('span', 'settag', it.tag));
    b.addEventListener('click', () => {
      if (it.id !== n.on) notify('settings_topic', { id: it.id });
    });
    nav.appendChild(b);
  });
  return nav;
}


/* Réglages › Compte: each Steam account seen on this PC, the one synced
   (a button to sync another, not in game), then its characters: portrait
   (head and shoulders), name, class and level, counted or not. */
function buildAccounts(n) {
  const box = el('div', 'accts');
  (n.items || []).forEach((a) => {
    const card = el('div', 'acct' + (a.sync ? ' sync' : ''));
    const head = el('div', 'accthead');
    const nm = el('div', 'acctname');
    nm.appendChild(el('b', null, a.name));
    if (a.steam) nm.appendChild(el('span', 'accttag on', tr('Connecté à Steam')));
    if (a.sync) nm.appendChild(el('span', 'accttag', tr('Synchronisé')));
    head.appendChild(nm);
    if (!a.sync) {
      const b = el('button', 'btn', tr('Synchroniser ce compte'));
      b.type = 'button';
      if (a.locked) {
        b.disabled = true;
        b.title = tr('En jeu, le compte synchronisé est celui qui joue.');
      }
      b.addEventListener('click', () => notify('acct_sync', { sid: a.sid }));
      head.appendChild(b);
    }
    card.appendChild(head);
    if (!(a.chars || []).length) {
      card.appendChild(el('p', 'note', tr('Aucun personnage vu en jeu pour ce compte.')));
    }
    const grid = el('div', 'chars');
    (a.chars || []).forEach((c) => grid.appendChild(charCard(a, c)));
    card.appendChild(grid);
    box.appendChild(card);
  });
  return box;
}

function charCard(a, c) {
  const card = el('div', 'charcard' + (c.on ? '' : ' off'));
  const pic = el('div', 'charpic');
  const src = c.port && (window.__PORTRAITS__ || {})[c.port];
  const im = el('img');
  im.alt = '';
  if (src) im.src = src;
  else if (c.snap) charSnap(c.snap, im);
  // no portrait yet: its class's crest
  const cls = (window.__ICONS__ || {})[c.cls];
  if (!src && cls) { im.src = cls; im.className = 'cls'; }
  if (im.src || c.snap) pic.appendChild(im);
  card.appendChild(pic);
  const t = el('div', 'chart');
  t.appendChild(el('b', null, c.n));
  t.appendChild(el('small', null, [c.clsT, c.lvl ? tr('niv. {n}', { n: c.lvl }) : '']
    .filter(Boolean).join(' · ')));
  card.appendChild(t);
  const sw = buildControl({ k: 'toggle', id: 'acct_hero', on: c.on, p: { sid: a.sid, key: c.key } });
  sw.title = c.on ? tr('Compté dans la progression du compte') : tr('Pas compté dans la progression du compte');
  card.appendChild(sw);
  // a character deleted in game: removed here too (a second click to confirm)
  const del = el('button', 'chardel');
  del.type = 'button';
  del.title = tr('Retirer ce personnage de l’application');
  del.appendChild(svgIcon('M6 7h12l-1 13H7zm3-3h6l1 2H8z'));
  del.addEventListener('click', () => {
    if (!del.classList.contains('confirm')) {
      del.classList.add('confirm');
      del.title = tr('Cliquer encore pour retirer {name} et sa progression', { name: c.n });
      setTimeout(() => { del.classList.remove('confirm'); }, 4000);
      return;
    }
    notify('acct_forget', { key: c.key });
  });
  card.appendChild(del);
  return card;
}

/* A character's portrait off its model, head and shoulders, taken once and
   kept by the meter with the others. */
const CHAR_SNAPS = { busy: false, todo: [], done: {} };
const CHAR_FRAME = [0.15, 0.21];
function charSnap(snap, im) {
  if (CHAR_SNAPS.done[snap[1]] || typeof m3dPortrait !== 'function') return;
  CHAR_SNAPS.done[snap[1]] = true;
  CHAR_SNAPS.todo.push([snap, im]);
  charSnapNext();
}
async function charSnapNext() {
  if (CHAR_SNAPS.busy || !m3dSupported()) return;
  CHAR_SNAPS.busy = true;
  try {
    while (CHAR_SNAPS.todo.length) {
      const [[model, key], im] = CHAR_SNAPS.todo.shift();
      const url = await m3dPortrait(model, 192, Math.PI / 2, CHAR_FRAME);
      if (!url) continue;
      (window.__PORTRAITS__ = window.__PORTRAITS__ || {})[key] = url;
      im.className = '';
      im.src = url;
      notify('npc_portrait', { key: key, data: url });
    }
  } finally { CHAR_SNAPS.busy = false; }
}

/* Réglages › Affichage: each colour theme as a small window in its colours
   (the header band, the tabs), its name under it. */
function buildThemes(n) {
  const grid = el('div', 'themes');
  (n.items || []).forEach((it) => {
    const c = it.c || {};
    const b = el('button', 'theme' + (it.id === n.on ? ' on' : ''));
    b.type = 'button';
    const win = el('div', 'thwin');
    win.style.background = c.bg;
    win.style.borderColor = c.line;
    const band = el('div', 'thband', 'Farever Book');
    band.style.background = 'linear-gradient(180deg, ' + c.band1 + ', ' + c.band2 + ')';
    band.style.borderColor = c.line;
    win.appendChild(band);
    const tabs = el('div', 'thtabs');
    tabs.style.background = c.panel;
    const on = el('span', 'on', tr('En jeu'));
    on.style.color = c.accent;
    on.style.borderColor = c.accent;
    const off = el('span', null, tr('Failles'));
    off.style.color = c.dim;
    tabs.appendChild(on);
    tabs.appendChild(off);
    win.appendChild(tabs);
    b.appendChild(win);
    b.appendChild(el('span', 'thname', it.id === n.on ? tr('{name}  ·  actif', { name: it.t }) : it.t));
    b.addEventListener('click', () => {
      if (it.id !== n.on) notify('set_theme', { id: it.id });
    });
    grid.appendChild(b);
  });
  return grid;
}

/* Aide › Liens utiles: a card per link, its logo, name and what it is for;
   a click opens it (when it has an address). The logos are Simple Icons'
   (CC0), drawn in each brand's colour (menu.css .lcard.l-<id>). */
/* "Soutenir" under Réglages and Aide: the project's Tipeee page. */
function renderSupport(on) {
  const slot = $('#supportslot');
  if (!slot) return;
  slot.textContent = '';
  if (!on) return;
  const b = el('button', 'supportbtn');
  b.type = 'button';
  b.title = tr('Soutenir le développement de Farever Book sur Tipeee');
  b.appendChild(svgIcon(LINK_ICONS.tipeee));
  b.appendChild(el('span', null, tr('Soutenir')));
  b.addEventListener('click', () => notify('open_link', { id: 'tipeee' }));
  slot.appendChild(b);
}

const LINK_ICONS = {
  tipeee: 'M12 21s-7.5-4.6-9.6-9.2C.9 8.4 2.9 4.5 6.6 4.1c2.1-.2 3.9.9 5.4 2.8 1.5-1.9 3.3-3 5.4-2.8 3.7.4 5.7 4.3 4.2 7.7C19.5 16.4 12 21 12 21z',
  discord: "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z",
  github: "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"
};
function buildLinkCards(n) {
  const grid = el('div', 'lcards');
  (n.items || []).forEach((it) => {
    const c = el(it.open ? 'button' : 'div', 'lcard l-' + it.id + (it.open ? ' open' : ''));
    if (it.open) {
      c.type = 'button';
      c.addEventListener('click', () => notify('open_link', { id: it.id }));
    }
    const ic = el('span', 'lic');
    ic.innerHTML = '<svg viewBox="0 0 24 24"><path d="' + (LINK_ICONS[it.id] || '') + '"/></svg>';
    c.appendChild(ic);
    c.appendChild(el('b', null, it.t));
    c.appendChild(el('span', 'lmeta', it.meta));
    grid.appendChild(c);
  });
  return grid;
}

/* Réglages › Affichage: the languages, each its flag and its own name. */
const LANG_FLAGS = {
  en: '<svg viewBox="0 0 60 30" preserveAspectRatio="xMidYMid slice"><clipPath id="fgb"><path d="M0 0v30h60V0z"/></clipPath>'
    + '<clipPath id="fgt"><path d="M30 15h30v15zv15H0zH0V0zV0h30z"/></clipPath>'
    + '<g clip-path="url(#fgb)"><path d="M0 0v30h60V0z" fill="#012169"/>'
    + '<path d="M0 0l60 30m0-30L0 30" stroke="#fff" stroke-width="6"/>'
    + '<path d="M0 0l60 30m0-30L0 30" clip-path="url(#fgt)" stroke="#C8102E" stroke-width="4"/>'
    + '<path d="M30 0v30M0 15h60" stroke="#fff" stroke-width="10"/>'
    + '<path d="M30 0v30M0 15h60" stroke="#C8102E" stroke-width="6"/></g></svg>',
  fr: '<svg viewBox="0 0 3 2"><path fill="#0055A4" d="M0 0h1v2H0z"/>'
    + '<path fill="#fff" d="M1 0h1v2H1z"/><path fill="#EF4135" d="M2 0h1v2H2z"/></svg>',
};
function buildLangs(n) {
  const box = el('div', 'langs');
  (n.items || []).forEach((it) => {
    const b = el('button', 'lang' + (it.id === n.on ? ' on' : ''));
    b.type = 'button';
    const flag = el('span', 'lflag');
    flag.innerHTML = LANG_FLAGS[it.id] || '';
    b.appendChild(flag);
    b.appendChild(el('span', 'lname', it.t));
    b.addEventListener('click', () => {
      if (it.id !== n.on) notify('set_lang', { id: it.id });
    });
    box.appendChild(b);
  });
  return box;
}

/* A newer version found: a band under the header, whose "Mettre à jour"
   opens the offer again (put off or not), until it is installed. */
function renderUpdateBar(v) {
  const bar = $('#updbar');
  if (!bar) return;
  bar.textContent = '';
  if (!v) return;
  const ic = svgIcon('M21 12a9 9 0 1 1-3-6.7M21 4v5h-5');
  ic.classList.add('ub-ic');
  bar.appendChild(ic);
  bar.appendChild(el('span', 'ub-t', tr('Farever Book {v} est disponible', { v: v })));
  bar.appendChild(el('span', 'ub-s', tr('Vos builds et votre historique sont conservés.')));
  const b = el('button', 'playbtn upbtn');
  b.type = 'button';
  b.title = tr('Farever Book {v} est disponible', { v: v });
  b.appendChild(svgIcon('M12 4v11M7 10l5 5 5-5M5 20h14'));
  b.appendChild(el('span', null, tr('Mettre à jour')));
  b.addEventListener('click', () => notify('update_offer', {}));
  bar.appendChild(b);
}

/* The rift clock's window: where the next rift opens, on the world map
   (the Codex's mini map), and the hours after. */
function renderRiftMap(r) {
  let back = $('#riftmapmodal');
  if (!r) { if (back) back.remove(); return; }
  const fresh = !back;
  if (fresh) {
    back = el('div', 'modalback');
    back.id = 'riftmapmodal';
    back.addEventListener('mousedown', (e) => { if (e.target === back) notify('rift_map_close', {}); });
    document.body.appendChild(back);
  }
  back.textContent = '';
  const box = el('div', 'modal riftmodal');
  const x = el('button', 'hclose', '×');
  x.type = 'button';
  x.title = tr('Fermer');
  x.addEventListener('click', () => notify('rift_map_close', {}));
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, r.title));
  head.appendChild(el('div', 'rmwhere', r.where));
  box.appendChild(head);
  // both spots, each its own entrance: framing on the next dims the other
  const map = huntMiniMap({ meta: r.meta,
    insts: (r.spots || []).map((p) => ({ t: p.t, kind: 'Faille', doors: [p] })) });
  box.appendChild(map);
  const list = el('div', 'rmnext');
  list.appendChild(el('div', 'rmlabel', tr('Les suivantes')));
  (r.next || []).forEach((n) => {
    const row = el('div', 'rmrow');
    row.appendChild(el('b', null, n.h));
    row.appendChild(el('span', null, n.t));
    list.appendChild(row);
  });
  box.appendChild(list);
  back.appendChild(box);
  mapIcons();
  // framed once laid out: on the spot, with room around it
  requestAnimationFrame(() => map.focusZone(r.where, 1100));
}

/* The update dialog (updater.py): offer, download progress, install; also
   the manual check's answer. */
function renderUpdate(u) {
  let back = $('#updatemodal');
  if (!u) { if (back) back.remove(); return; }
  if (!back) {
    back = el('div', 'modalback');
    back.id = 'updatemodal';
    document.body.appendChild(back);
  }
  back.textContent = '';
  const box = el('div', 'spanel bdialog upbox');
  const btn = (t, cls, id) => {
    const b = el('button', cls, t);
    b.type = 'button';
    b.addEventListener('click', () => notify(id, {}));
    return b;
  };
  const row = el('div', 'bbtns');
  if (u.stage === 'offer') {
    box.appendChild(el('div', 'sptitle', tr('Nouvelle version disponible')));
    box.appendChild(el('p', 'uplead', tr('Farever Book {v} est disponible (vous avez la {mine}).', { v: u.v, mine: u.mine })));
    if (u.notes) {
      box.appendChild(el('div', 'upsub', tr('Nouveautés')));
      box.appendChild(el('div', 'upnotes', u.notes));
    }
    box.appendChild(el('p', 'note', u.mb
      ? tr('La mise à jour télécharge l’installeur ({mb} Mo) et ferme Farever Book. '
        + 'Seule une fenêtre de progression s’affiche, puis l’application redémarre d’elle-même. '
        + 'Vos builds et votre historique sont conservés.', { mb: u.mb })
      : tr('La mise à jour télécharge l’installeur et ferme Farever Book. '
        + 'Seule une fenêtre de progression s’affiche, puis l’application redémarre d’elle-même. '
        + 'Vos builds et votre historique sont conservés.')));
    row.appendChild(btn(tr('Plus tard'), 'rowbtn', 'update_later'));
    row.appendChild(btn(tr('Mettre à jour'), 'btn go', 'update_install'));
  } else if (u.stage === 'download' || u.stage === 'installing') {
    box.appendChild(el('div', 'sptitle', tr('Mise à jour vers la {v}', { v: u.v })));
    const pct = u.stage === 'installing' ? 100 : (u.pct || 0);
    const bar = el('div', 'wbar');
    const fill = el('i');
    fill.style.width = Math.max(2, pct) + '%';
    bar.appendChild(fill);
    box.appendChild(bar);
    box.appendChild(el('p', 'note', u.stage === 'installing'
      ? tr('Ouverture de l’installeur, Farever Book va se fermer…')
      : tr('Téléchargement de l’installeur : {pct} %', { pct: pct })));
  } else if (u.stage === 'checking') {
    box.appendChild(el('div', 'sptitle', tr('Recherche d’une mise à jour…')));
  } else if (u.stage === 'uptodate') {
    box.appendChild(el('div', 'sptitle', tr('Farever Book est à jour')));
    box.appendChild(el('p', 'note', tr('Vous avez déjà la dernière version.')));
    row.appendChild(btn(tr('Fermer'), 'btn go', 'update_close'));
  } else {
    box.appendChild(el('div', 'sptitle', tr('Mise à jour impossible')));
    box.appendChild(el('p', 'werr', u.t || tr('Une erreur est survenue.')));
    if (u.page) row.appendChild(btn(tr('Page des versions'), 'rowbtn', 'update_page'));
    row.appendChild(btn(tr('Fermer'), 'btn go', 'update_close'));
  }
  if (row.children.length) box.appendChild(row);
  back.appendChild(box);
}
