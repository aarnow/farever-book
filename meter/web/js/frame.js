/* The window's frame and title band: tabs as icons, the game state, the
   events and connection windows, moving and resizing the window. */

let EVENTS_OPEN = false;
let EVENTS_SEEN = null;         // how many events there were when last seen

function eventsButton() {
  const b = el('button', 'navicon evbtn' + (EVENTS_OPEN ? ' active' : ''));
  b.type = 'button';
  b.title = 'Événements';
  b.setAttribute('aria-label', 'Événements');
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
  x.title = 'Fermer';
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
  x.title = 'Fermer';
  x.addEventListener('click', () => toggleLinkSteps(false));
  box.appendChild(x);
  const head = el('div', 'phead');
  head.appendChild(el('h3', null, 'Connexion au jeu'));
  box.appendChild(head);
  const body = el('div', 'stepbody');
  const started = steps.some((st) => st.s !== 'wait');
  if (!started) {
    body.appendChild(el('p', 'note', l.state === 'play' || l.state === 'launching'
      ? 'Farever n’est pas encore détecté : la connexion commencera dès son lancement.'
      : 'Aucune connexion en cours.'));
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

/* The tabs are built once and kept: only which one is active changes, so
   its colour can ease in and one gold bar (#navink) can slide under it.
   A click shows the new tab at once; the page follows (core.js). */
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
    tabs.forEach((tab) => {
      const t = typeof tab === 'string' ? tab : tab.v;
      const label = typeof tab === 'string' ? tab : tab.t;
      let b;
      if (TAB_ICONS[t]) {
        b = el('button', 'navicon');
        b.title = label;
        b.setAttribute('aria-label', label);
        b.appendChild(svgIcon(TAB_ICONS[t]));
        icons.appendChild(b);
      } else {
        b = el('button', null, label);
        nav.appendChild(b);
      }
      b.type = 'button';
      b.addEventListener('click', () => {
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

function showTab(t) {
  const first = NAV_ACTIVE === null;
  NAV_ACTIVE = t;
  Object.keys(NAV_BTNS).forEach((k) => NAV_BTNS[k].classList.toggle('active', k === t));
  const ink = $('#navink');
  const b = NAV_BTNS[t];
  if (!ink) return;
  if (!b || b.classList.contains('navicon')) {   // Réglages, Aide: no bar
    ink.style.opacity = '0';
    return;
  }
  // the first placement does not slide in from the left edge
  ink.classList.toggle('still', first || ink.style.opacity === '0');
  // under its tab: on the band's bottom edge when the tabs fit on one line,
  // right under the tab when they wrap onto two
  const nav = $('#nav');
  const rows = new Set(Object.values(NAV_BTNS).filter((x) => x.parentNode === nav)
    .map((x) => x.offsetTop)).size;
  const y = rows > 1 ? b.offsetTop + b.offsetHeight - 3 : nav.clientHeight - 3;
  ink.style.width = Math.max(0, b.offsetWidth - 28) + 'px';
  ink.style.transform = 'translate(' + (b.offsetLeft + 14) + 'px, ' + y + 'px)';
  ink.style.opacity = '1';
}

/* The bar follows its tab when the band reflows: window resized, the
   interface zoomed, the tabs wrapping onto two lines. */
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
    b.appendChild(el('span', null, l.state === 'play' ? 'Jouer' : 'Réessayer'));
    if (l.tip) b.title = l.tip;
    b.addEventListener('click', () => notify(l.state === 'play' ? 'launch_game' : 'link_retry', {}));
    box.appendChild(b);
    return;
  }
  const st = el('div', 'gamestate');
  st.title = 'Voir le détail de la connexion';
  st.addEventListener('click', () => toggleLinkSteps(true));
  const top = el('div', 'gs');
  top.appendChild(el('i', 'dot'));
  top.appendChild(el('b', null, l.t || ''));
  st.appendChild(top);
  if (l.state === 'ingame' && shard) st.appendChild(el('span', 'srv', 'Serveur ' + shard));
  else if (l.tip) st.appendChild(el('span', 'srv', l.tip));
  box.appendChild(st);
}

/* The window's own frame: the title bar drags the window (a double click
   maximises), the edges resize it, and the three buttons do what the native
   caption's did. All through the host (menu_host.Api.win). */
function winCall(action, arg) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.win) return Promise.resolve(false);
  return api.win(action, arg === undefined ? null : arg);
}

function setMaxState(isMax) {
  document.body.classList.toggle('maxed', !!isMax);
  const b = document.querySelector('#winctl [data-win="max"]');
  if (b) b.title = isMax ? 'Restaurer' : 'Agrandir';
}

/* Follow the mouse while a button is held, sending the host at most one
   new rectangle per frame (and never a new one before the last is done):
   the window keeps up without flooding the bridge. */
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

/* ---- the state push ----------------------------------------------------- */
/* Called by the host with a JSON *string*: the state carries player names,
   and interpolating those into a script expression would break the page the
   first time somebody had a quote in their name. */


/* The first launch: the welcome screen, alone in the window (no tabs) until
   the game's data has been read (app.py _setup_*). It asks before reading
   the game's folder, shows the progress, then lets the player in. */
function welcomePick() {
  if (!window.pywebview || !window.pywebview.api.pick_folder) return;
  window.pywebview.api.pick_folder().then((p) => {
    if (p) notify('setup_folder', { path: p });
  });
}

/* The progress screen, kept between state pushes: its ring turns and its
   bar slides on, the numbers changed in place rather than redrawn. */
let WELCOME_RUN = null;

function updateWelcomeRun(w, n) {
  const pct = n.pct || 0;
  w.pct.textContent = pct + ' %';
  w.fill.style.width = Math.max(2, pct) + '%';
  (n.rows || []).forEach((r, i) => {
    const li = w.rows[i];
    if (!li) return;
    li.className = 'w' + r.s;
    li.querySelector('.wmark').textContent = r.s === 'done' ? '✓' : '';
    const has = r.n !== null && r.n !== undefined;
    li.querySelector('b').textContent = has
      ? r.n.toLocaleString('fr-FR') + ' image' + (r.n > 1 ? 's' : '') : '';
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
  const crest = el('span', 'emblem wcrest');
  crest.appendChild(el('i'));
  card.appendChild(crest);
  const btn = (text, cls, fn) => {
    const b = el('button', cls, text);
    b.type = 'button';
    b.addEventListener('click', fn);
    return b;
  };

  const repair = n.mode === 'repair';
  if (repair) {
    if (n.stage === 'done') {
      card.appendChild(el('h1', null, 'Réparation terminée'));
      card.appendChild(el('p', 'wlead', 'Les données du jeu ont été relues et la connexion au jeu '
        + 'relancée.'));
      card.appendChild(el('p', 'wtext', 'Si le problème persiste, contactez @Aarnow sur Discord.'));
      card.appendChild(btn('Revenir à l’application', 'wgo', () => notify('setup_finish', {})));
      box.appendChild(card);
      return box;
    }
    card.appendChild(el('h1', null, n.stage === 'error' ? 'La réparation n’a pas abouti'
      : 'Réparation de Farever France'));
    card.appendChild(el('p', 'wlead', n.stage === 'error'
      ? 'La nouvelle analyse du jeu s’est arrêtée avant la fin.'
      : 'Nouvelle analyse du jeu pour résoudre les problèmes rencontrés.'));
  }
  if (n.stage === 'done') {
    card.appendChild(el('h1', null, 'Tout est prêt !'));
    card.appendChild(el('p', 'wlead', 'Merci d’utiliser Farever France. Les images, les icônes et les '
      + 'données du jeu sont en place, tous les modules sont accessibles.'));
    card.appendChild(el('p', 'wgame', 'Bon jeu sur Farever !'));
    card.appendChild(btn('Commencer', 'wgo', () => notify('setup_finish', {})));
    box.appendChild(card);
    return box;
  }

  if (!repair) {
    card.appendChild(el('h1', null, 'Bienvenue sur Farever France'));
    card.appendChild(el('p', 'wlead', 'L’outil qui vous accompagne dans vos aventures sur Farever !'));
  }

  if (n.stage === 'run') {
    // a ring that always turns, the percentage in it: something is moving
    const ring = el('div', 'wring');
    ring.appendChild(el('i'));
    const pct = el('b');
    ring.appendChild(pct);
    card.appendChild(ring);
    card.appendChild(el('p', 'wtext', 'Récupération des données du jeu, une trentaine de secondes.'));
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
    card.appendChild(el('p', 'wtext', 'Pour accéder aux divers modules, nous avons besoin d’accéder '
      + 'au dossier du jeu Farever afin d’y récupérer :'));
    const ul = el('ul', 'wneeds');
    (n.needs || []).forEach((t) => ul.appendChild(el('li', null, t)));
    card.appendChild(ul);
    card.appendChild(el('p', 'wsafe', 'Le jeu est seulement lu : rien n’y est modifié, et rien ne '
      + 'quitte votre ordinateur.'));
  }

  if (n.stage === 'error') {
    card.appendChild(el('p', 'werr', 'La récupération n’a pas abouti. Vérifiez que Farever est à '
      + 'jour, puis réessayez. Si le problème persiste, contactez @Aarnow sur Discord.'));
  }
  if (n.stage === 'locate') {
    card.appendChild(el('p', 'wwarn', 'Nous n’avons pas trouvé Farever sur cet ordinateur. '
      + 'Indiquez-nous son dossier, celui qui contient Farever.exe.'));
    if (n.err) card.appendChild(el('p', 'werr', n.err));
    card.appendChild(btn('Parcourir…', 'wgo', welcomePick));
  } else {
    const where = el('div', 'wpath');
    where.appendChild(el('span', null, 'Dossier du jeu'));
    where.appendChild(el('b', null, n.path || ''));
    const other = btn('Ce n’est pas le bon dossier ?', 'wlink', welcomePick);
    where.appendChild(other);
    card.appendChild(where);
    if (n.err) card.appendChild(el('p', 'werr', n.err));
    card.appendChild(btn(n.stage === 'error' ? 'Réessayer' : 'Autoriser et commencer', 'wgo',
      () => notify('setup_start', {})));
  }
  if (repair) {
    // a repair can always be left: the app was working before it
    const back = btn('Revenir à l’application', 'wlink wback', () => notify('setup_finish', {}));
    card.appendChild(back);
  }
  box.appendChild(card);
  return box;
}


/* Réglages: the subjects, a menu down the page's left. */
const SETNAV_ICONS = {
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
    b.addEventListener('click', () => {
      if (it.id !== n.on) notify('settings_topic', { id: it.id });
    });
    nav.appendChild(b);
  });
  return nav;
}


/* A new version (updater.py): offered, downloaded with its progress, then
   its installer opened and the app closed. Also the manual check's answer. */
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
    box.appendChild(el('div', 'sptitle', 'Nouvelle version disponible'));
    box.appendChild(el('p', 'uplead', 'Farever France ' + u.v + ' est disponible (vous avez la ' + u.mine + ').'));
    if (u.notes) {
      box.appendChild(el('div', 'upsub', 'Nouveautés'));
      box.appendChild(el('div', 'upnotes', u.notes));
    }
    box.appendChild(el('p', 'note', 'La mise à jour télécharge l’installeur' + (u.mb ? ' (' + u.mb + ' Mo)' : '')
      + ', l’ouvre et ferme Farever France. Suivez l’installeur : il relance l’application à la fin. '
      + 'Vos builds et votre historique sont conservés.'));
    row.appendChild(btn('Plus tard', 'rowbtn', 'update_later'));
    row.appendChild(btn('Mettre à jour', 'btn go', 'update_install'));
  } else if (u.stage === 'download' || u.stage === 'installing') {
    box.appendChild(el('div', 'sptitle', 'Mise à jour vers la ' + u.v));
    const pct = u.stage === 'installing' ? 100 : (u.pct || 0);
    const bar = el('div', 'wbar');
    const fill = el('i');
    fill.style.width = Math.max(2, pct) + '%';
    bar.appendChild(fill);
    box.appendChild(bar);
    box.appendChild(el('p', 'note', u.stage === 'installing'
      ? 'Ouverture de l’installeur, Farever France va se fermer…'
      : 'Téléchargement de l’installeur : ' + pct + ' %'));
  } else if (u.stage === 'checking') {
    box.appendChild(el('div', 'sptitle', 'Recherche d’une mise à jour…'));
  } else if (u.stage === 'uptodate') {
    box.appendChild(el('div', 'sptitle', 'Farever France est à jour'));
    box.appendChild(el('p', 'note', 'Vous avez déjà la dernière version.'));
    row.appendChild(btn('Fermer', 'btn go', 'update_close'));
  } else {
    box.appendChild(el('div', 'sptitle', 'Mise à jour impossible'));
    box.appendChild(el('p', 'werr', u.t || 'Une erreur est survenue.'));
    if (u.page) row.appendChild(btn('Page des versions', 'rowbtn', 'update_page'));
    row.appendChild(btn('Fermer', 'btn go', 'update_close'));
  }
  if (row.children.length) box.appendChild(row);
  back.appendChild(box);
}
