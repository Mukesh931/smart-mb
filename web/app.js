/* ============================================================================
   Smart-MB | Maharashtra PWD Electrical
   Centralized CSR Database & Site Verification System - front-end
   Single-file vanilla SPA: hash router, live API, offline demo fallback.
   ========================================================================== */
'use strict';

const API_BASE = '';
const S = {
  token: localStorage.getItem('smartmb_token') || '',
  outbox: (function () { try { return JSON.parse(localStorage.getItem('smartmb_outbox') || '[]'); } catch (e) { return []; } })(),
  user: JSON.parse(localStorage.getItem('smartmb_user') || 'null'),
  route: { name: 'dashboard', params: {} },
  demo: false,
  csr: { fy: '', region: '', q: '', category: '', chapter: '', page: 0, limit: 40 },
  proj: { id: null, tab: 'overview', room: null, data: null },
  verify: { docId: null, onlyPending: false, byFloor: false, section: 'rooms', queuedIds: new Set() },
  outbox: [],
  schedulePreview: null,
  importPreview: null,
  adminTab: 'master',
};

/* ============================================================ offline outbox
   Site engineers work in places with one bar of signal.  Every write that records
   something on site (verify a quantity, capture a measurement) goes through
   queueable(): the POST is made, and if the phone is offline the action is kept in
   localStorage, applied to the screen immediately, and replayed automatically when
   the network returns.  Measurement POSTs carry a client_ref, so a replay can never
   record the same reading twice.  The queue survives closing the app.
   ========================================================================== */
const OUTBOX_KEY = 'smartmb_outbox';
const isOnline = () => !(typeof navigator !== 'undefined' && navigator.onLine === false);

function outboxLoad() {
  try { return JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]'); } catch (e) { return []; }
}
function outboxSave() {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(S.outbox)); } catch (e) { /* full/blocked */ }
  paintOutboxBadge();
}
function outboxAdd(item) {
  item.id = item.id || ('q' + Date.now() + Math.random().toString(16).slice(2, 8));
  S.outbox.push(item);
  outboxSave();
  return item;
}
function outboxDrop(match) {
  const before = S.outbox.length;
  S.outbox = S.outbox.filter((o) => !(o.kind === match.kind && String(o.cellId || '') === String(match.cellId || '')
    && (match.clientRef ? o.clientRef === match.clientRef : true)));
  if (S.outbox.length !== before) outboxSave();
}
function outboxClearFor(prefix) { S.outbox = S.outbox.filter((o) => !o.path.startsWith(prefix)); outboxSave(); }

function paintOutboxBadge() {
  const n = S.outbox.length;
  const bar = document.getElementById('outbox-bar');
  const off = !isOnline();
  if (!bar) return;
  if (!n && !off) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
  bar.style.display = '';
  bar.innerHTML = `<span class="dot ${off ? 'bad' : 'warn'}"></span>
    <span>${off ? 'No network at site — ' : ''}${n ? `<b>${n}</b> site record${n === 1 ? '' : 's'} saved on this phone, not yet sent.` : 'Offline — readings will be saved on this phone.'}</span>
    <button class="btn sm" data-act="outbox-sync" ${off ? 'disabled' : ''}>Sync now</button>`;
}

/* Route a write through the outbox.  Returns the API response, or a synthetic
   {queued:true} response when the action was stored on the phone instead. */
async function queueable(path, opts) {
  try {
    return await api(path, opts);
  } catch (err) {
    if (err.status || !S.demo) {
      if (err.status) throw err;        // the server answered and refused - show that to the engineer
      const kind = path.includes('/api/measurements') ? 'measurement'
        : path.includes('verify-bulk') ? 'bulk' : 'cell';
      const cellId = (path.match(/\/api\/schedule-cells\/(\d+)\/verify/) || [])[1];
      const item = outboxAdd({ kind, cellId: cellId ? Number(cellId) : null, path, method: opts.method || 'POST',
        body: opts.body, clientRef: (opts.body || {}).client_ref || null, ts: Date.now() });
      const action = (opts.body || {}).action;
      const qty = (opts.body || {}).actual_qty;
      return { ok: true, queued: true, offline: true, queue_id: item.id, cell_id: item.cellId,
               status: action === 'keep' ? 'kept' : action === 'pending' ? 'pending' : (qty != null ? 'changed' : 'kept'),
               qty: qty != null ? qty : (opts.body || {}).schedule_qty, verified: 0, failed: [],
               measurement_id: null, measured_qty: (opts.body || {}).measured_qty, item_total_qty: null };
    }
    throw err;
  }
}

let flushing = false;
async function outboxFlush(manual) {
  if (flushing) return { sent: 0, left: S.outbox.length };
  if (!S.outbox.length) { if (manual) toast('Everything is already synced', 'ok'); return { sent: 0, left: 0 }; }
  flushing = true;
  let sent = 0, rejected = 0;
  while (S.outbox.length) {
    const item = S.outbox[0];
    try {
      await api(item.path, { method: item.method || 'POST', body: item.body });
      S.outbox.shift(); sent++;
    } catch (err) {
      if (err.status) { S.outbox.shift(); rejected++; }   // server said no - retrying forever would block the queue
      else break;                                          // still offline
    }
  }
  flushing = false;
  if (!S.outbox.length) S.verify.queuedIds.clear();
  outboxSave();
  if (sent) toast(`${sent} site record${sent === 1 ? '' : 's'} synced to the server`, 'ok');
  if (rejected) toast(`${rejected} queued record(s) were rejected by the server — please re-check them`, 'bad', 6000);
  if (sent || rejected) {
    if (typeof render === 'function' && (S.token || S.demo)) render();
  }
  return { sent, left: S.outbox.length };
}

async function syncNow() {
  if (!isOnline()) { toast('Still offline — the records stay safely on this phone', 'warn'); return; }
  const btn = document.querySelector('[data-act="outbox-sync"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>'; }
  await outboxFlush(true);
  paintOutboxBadge();
}

/* ------------------------------------------------------------------ utils */
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function inr(n, dp = 2) {
  if (n == null || n === '' || isNaN(n)) return '—';
  const neg = n < 0; n = Math.abs(Number(n));
  const fixed = dp ? n.toFixed(dp) : String(Math.round(n));
  let [w, f] = fixed.split('.');
  if (w.length > 3) {
    const t = w.slice(-3); let h = w.slice(0, -3); const parts = [];
    while (h.length > 2) { parts.unshift(h.slice(-2)); h = h.slice(0, -2); }
    if (h) parts.unshift(h);
    w = parts.join(',') + ',' + t;
  }
  return (neg ? '-' : '') + w + (f ? '.' + f : '');
}
const n2 = (v, dp = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toFixed(dp).replace(/\.?0+$/, (m) => m.includes('.') ? '' : m) || '0';
function smartNum(v) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}
const dt = (s) => { if (!s) return '—'; const d = new Date(s); return isNaN(d) ? s : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }); };
const dtt = (s) => { if (!s) return '—'; const d = new Date(s); return isNaN(d) ? s : d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); };
const today = () => new Date().toISOString().slice(0, 10);
const debounce = (fn, ms = 320) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const ICON = {
  dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M3 13h8V3H3v10Zm10 8h8V11h-8v10ZM3 21h8v-5H3v5Zm10-13h8V3h-8v5Z"/></svg>',
  book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M4 4h11a3 3 0 0 1 3 3v13H7a3 3 0 0 1-3-3V4Zm14 3h2v13a3 3 0 0 0-3-3h-8"/><path d="M8 8h6M8 12h6"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 3l8 3v6c0 5-3.4 8.3-8 9-4.6-.7-8-4-8-9V6l8-3Z"/><path d="M9 12l2 2 4-4"/></svg>',
  ruler: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="m3 17 4-4 3 3 3-3 3 3 5-5M4 20h16"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"><path d="M12 5v14M5 12h14"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.4-3.4"/></svg>',
  x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  cam: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M4 8h3l1.5-2h7L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.2"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 17V5m0 0L7 10m5-5 5 5M5 19h14"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 5v12m0 0 5-5m-5 5-5-5M5 19h14"/></svg>',
  out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M10 5H6v14h4M14 8l4 4-4 4M18 12H10"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 4l9 16H3L12 4Z"/><path d="M12 10v4m0 3h.01"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="m5 13 4 4L19 7"/></svg>',
  sparkle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="M18 16l.9 2.1L21 19l-2.1.9L18 22l-.9-2.1L15 19l2.1-.9L18 16Z"/></svg>',
  chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M4 20V4m0 16h16M8 16V9m4 7V6m4 10v-5"/></svg>',
};

/* ------------------------------------------------------------ api / demo */
const DEMO = (() => { try { return JSON.parse(document.getElementById('demo-snapshot').textContent); } catch (e) { return null; } })();

function demoLookup(path, method) {
  if (!DEMO || method !== 'GET') return null;
  const p = path.split('?')[0].replace(/^\/api/, '');
  const map = [
    [/^\/dashboard$/, () => DEMO.dashboard],
    [/^\/csr\/versions$/, () => ({ versions: DEMO.versions })],
    [/^\/csr\/facets$/, () => DEMO.facets],
    [/^\/csr\/items$/, () => DEMO.csr_items],
    [/^\/csr\/suggest$/, () => ({ results: DEMO.suggest })],
    [/^\/projects$/, () => ({ projects: DEMO.projects })],
    [/^\/projects\/(\d+)$/, (m) => (Number(m[1]) === DEMO.project.project.id ? DEMO.project : null)],
    [/^\/projects\/(\d+)\/items$/, () => ({ items: DEMO.items })],
    [/^\/projects\/(\d+)\/checklist$/, () => DEMO.checklist],
    [/^\/projects\/(\d+)\/verify$/, () => DEMO.verify],
    [/^\/projects\/(\d+)\/reconciliation$/, () => DEMO.reconciliation],
    [/^\/projects\/(\d+)\/reconciliation\.xlsx$/, () => null],
    [/^\/projects\/(\d+)\/schedules$/, () => ({ docs: (DEMO.verify && DEMO.verify.docs) || [] })],
    [/^\/schedule-docs\/(\d+)$/, () => DEMO.schedule_doc],
    [/^\/projects\/(\d+)\/deviations$/, () => DEMO.deviations],
    [/^\/projects\/(\d+)\/measurements$/, () => ({ measurements: DEMO.measurements, count: DEMO.measurements.length, total_amount: 0 })],
    [/^\/admin\/overview$/, () => DEMO.admin],
    [/^\/admin\/parse-jobs$/, () => ({ jobs: [] })],
    [/^\/auth\/me$/, () => ({ user: DEMO.user })],
  ];
  for (const [re, fn] of map) { const m = p.match(re); if (m) { const r = fn(m); if (r) return r; } }
  return null;
}

async function api(path, { method = 'GET', body, form, raw } = {}) {
  const url = API_BASE + path;
  const opt = { method, headers: {} };
  if (S.token) opt.headers['Authorization'] = 'Bearer ' + S.token;
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  if (form) opt.body = form;
  try {
    const res = await fetch(url, opt);
    if (raw) return res;
    const text = await res.text();
    let data; try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { detail: text }; }
    if (!res.ok) throw Object.assign(new Error(data.detail || ('HTTP ' + res.status)), { status: res.status, data });
    return data;
  } catch (err) {
    if (err.status) throw err;
    const dm = demoLookup(path, method);
    if (dm && S.demo) return dm;
    if (DEMO && method === 'GET' && !S.demo) {
      const dm2 = demoLookup(path, method);
      if (dm2) return dm2;
    }
    throw Object.assign(new Error('Network unavailable. Start the server to use live data.'), { offline: true });
  }
}

function enterDemo() {
  S.demo = true; S.token = ''; S.user = DEMO.user;
  const p = DEMO.project.project;
  S.proj.id = p.id;
  S.csr.fy = DEMO.versions[0].fy; S.csr.region = DEMO.versions[0].region;
  toast('Offline demo mode — read-only sample data. Start the server for the full app.', 'warn', 6000);
  location.hash = '#/dashboard';
}

async function download(path, filename) {
  try {
    const res = await fetch(API_BASE + path, { headers: S.token ? { Authorization: 'Bearer ' + S.token } : {} });
    if (!res.ok) {
      let msg = 'HTTP ' + res.status;
      try { msg = (await res.json()).detail || msg; } catch (e) { }
      if (res.status === 401) msg = 'Session expired — sign in again to download reports.';
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 4000);
    toast('Download started: ' + filename, 'ok');
  } catch (e) { toast('Download failed — ' + e.message, 'bad'); }
}

/* ------------------------------------------------------------ ui helpers */
function toast(msg, kind = '', ms = 3600) {
  const wrap = document.getElementById('toasts');
  while (wrap.children.length >= 3) wrap.firstElementChild.remove();   // never a wall of notices
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `${kind === 'bad' ? ICON.warn : kind === 'ok' ? ICON.check : kind === 'warn' ? ICON.warn : ICON.ruler}<div>${esc(msg)}</div>`;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 320); }, ms);
}
function closeSheet() { const s = document.querySelector('.sheet-bg'); if (s) s.remove(); }
function sheet({ title, subtitle, body, footer, wide = false, onOpen, body_id, sticky = false }) {
  closeSheet();
  const el = document.createElement('div');
  el.className = 'sheet-bg';
  el.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" ${wide ? 'style="max-width:920px"' : ''}>
    <div class="grabber mob-only"></div>
    <div class="hd"><div style="flex:1;min-width:0"><h3>${esc(title)}</h3>
      ${subtitle ? `<div class="small muted">${esc(subtitle)}</div>` : ''}</div>
      <button class="btn sm" data-act="close-sheet">${ICON.x}</button></div>
    <div class="bd"${body_id ? ` id="${body_id}"` : ''}>${body}</div>
    ${footer ? `<div class="ft${sticky ? ' sticky' : ''}">${footer}</div>` : ''}</div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => {
    if (e.target === el || e.target.closest('[data-act="close-sheet"]')) closeSheet();
  });
  if (onOpen) onOpen(el);
  return el;
}
const loading = (label = 'Loading…') => `<div class="stack"><div class="card"><div class="bd"><div class="row"><div class="spin" style="border-color:#0b5c8a;border-top-color:transparent"></div><span class="muted">${esc(label)}</span></div></div></div>
  <div class="card"><div class="bd stack"><div class="skeleton" style="width:70%"></div><div class="skeleton" style="width:88%"></div><div class="skeleton" style="width:54%"></div></div></div></div>`;

function badgeFor(status) {
  const m = {
    matched: ['ok', 'Matched to CSR'], excess: ['bad', 'Excess'], saving: ['ok', 'Saving'],
    pending: ['warn', 'Not measured'], unknown_item: ['ns', 'Unknown / Non-schedule'],
    critical: ['bad', 'Critical deviation'], review: ['warn', 'Review ±10%'], info: ['info', 'Minor deviation'],
    ok: ['ok', 'OK'], active: ['ok', 'Active'], completed: ['info', 'Completed'],
  };
  const [k, label] = m[status] || ['', status];
  return `<span class="badge ${k}">${esc(label)}</span>`;
}

/* ------------------------------------------------------------------ router */
function parseHash() {
  const h = (location.hash || '#/dashboard').replace(/^#\/?/, '');
  const seg = h.split('/').filter(Boolean);
  if (!seg.length) return { name: 'dashboard', params: {} };
  if (seg[0] === 'project' && seg[1]) return { name: 'project', params: { id: Number(seg[1]) } };
  if (seg[0] === 'csr') return { name: 'csr', params: { q: seg[1] ? decodeURIComponent(seg[1]) : '' } };
  return { name: seg[0], params: {} };
}

async function render() {
  setTimeout(paintOutboxBadge, 0);
  if (!S.token && !S.demo) { renderLogin(); return; }
  S.route = parseHash();
  const app = document.getElementById('app');
  app.innerHTML = shell();
  const view = document.getElementById('view');
  view.innerHTML = loading();
  try {
    if (S.route.name === 'dashboard') await viewDashboard(view);
    else if (S.route.name === 'csr') await viewCSR(view);
    else if (S.route.name === 'projects') await viewProjects(view);
    else if (S.route.name === 'project') await viewProject(view, S.route.params.id);
    else if (S.route.name === 'admin') await viewAdmin(view);
    else view.innerHTML = `<div class="empty">Unknown screen</div>`;
  } catch (err) {
    if (err.status === 401) { S.token = ''; S.user = null; localStorage.removeItem('smartmb_token'); renderLogin(); return; }
    view.innerHTML = `<div class="notice bad">${ICON.warn}<div><b>Could not load this screen.</b><br>${esc(err.message)}</div></div>`;
  }
  markNav();
}

/* The bundle a phone holds can be days old (and a field phone caches hard).  The server
   publishes a build hash in /api/health; when it differs from the one stamped into this
   page, reload instead of running stale screens. */
const MY_BUILD = (document.querySelector('meta[name="smartmb-build"]') || {}).content || '';
let _buildChecked = false;

async function checkBuildAndStorage() {
  if (_buildChecked || S.demo || !MY_BUILD) return;
  _buildChecked = true;
  try {
    const h = await fetch('/api/health', { cache: 'no-store' }).then((r) => r.json());
    if (h.build && h.build !== MY_BUILD) {
      // a newer build is live: reload when nothing is at risk, otherwise offer a button
      const busy = (S.outbox || []).length || document.querySelector('.sheet-bg') || document.querySelector('textarea:focus');
      if (!busy && sessionStorage.getItem('smartmb_reloaded') !== h.build) {
        sessionStorage.setItem('smartmb_reloaded', h.build);
        toast('New version found — reloading', 'ok');
        setTimeout(() => location.reload(), 600);
      } else {
        const bar = document.getElementById('outbox-bar');
        if (bar) {
          bar.style.display = '';
          bar.classList.add('update');
          bar.innerHTML = '<span class="dot warn"></span><div class="grow"><b>A newer version of Smart-MB is live.</b>'
            + '<div class="small">Save what you are typing, then reload to get it.</div></div>'
            + '<button class="btn sm" data-act="do-reload">Reload</button>';
        }
      }
    }
    S.storage = h.storage || 'persistent';
    S.backupOn = !!(h.backup && h.backup.configured);
    if (S.storage === 'ephemeral' && !S.backupOn) {
      const key = 'smartmb_storage_warn';
      const last = Number(localStorage.getItem(key) || 0);
      if (Date.now() - last > 12 * 3600 * 1000) {
        localStorage.setItem(key, String(Date.now()));
        toast('This server has no permanent disk — a restart can erase works.', 'warn', 7000);
      }
    }
  } catch (e) { /* offline: nothing to check */ }
}

function shell() {
  const u = S.user || { name: 'Guest', role: 'engineer' };
  const isAdmin = u.role === 'admin';
  const navItems = [
    ['#/dashboard', 'dash', 'Dashboard'],
    ['#/csr', 'book', 'Master CSR'],
    ['#/projects', 'folder', 'Projects'],
    ['#/project/' + (S.proj.id || 1), 'ruler', 'Measurement'],
    ...(isAdmin ? [['#/admin', 'shield', 'Admin Control']] : []),
  ];
  return `
  <div class="app">
    <aside class="side">
      <div class="brand">
        <div class="mark">SM</div>
        <div><b>Smart-MB</b><span>PWD Electrical · Maharashtra</span></div>
      </div>
      <nav class="nav">
        <div class="sechead">Workspace</div>
        ${navItems.slice(0, 3).map(([h, i, l]) => `<a href="${h}" data-nav="${h}">${ICON[i]}${l}</a>`).join('')}
        <div class="sechead">Site verification</div>
        ${navItems.slice(3).map(([h, i, l]) => `<a href="${h}" data-nav="${h}">${ICON[i]}${l}</a>`).join('')}
      </nav>
      <div class="who">
        <b>${esc(u.name)}</b><span>${esc(u.designation || u.role)}</span><br>
        <span>${esc(u.division || '')}</span>
        <div class="row" style="margin-top:8px">
          <button class="btn sm" data-act="logout" style="background:transparent;color:#cbd8e6;border-color:rgba(255,255,255,.2)">Sign out</button>
          ${S.demo ? '<span class="badge warn tiny">DEMO</span>' : ''}
        </div>
      </div>
    </aside>
    <main class="main">
      <div id="outbox-bar" class="outbox-bar" style="display:none"></div>
      <header class="topbar">
        <div style="flex:1;min-width:0"><h1 id="ptitle">Smart-MB<small id="psub">Centralized CSR Database & Site Verification System</small></h1></div>
        <span class="badge brand desk-only">CSR ${esc(S.csr.fy || '2024-25')} · ${esc(S.csr.region || (u.region || 'Pune'))}</span>
        <button class="btn sm" data-act="help">?</button>
      </header>
      <div class="wrap with-bottom" id="view"></div>
      <nav class="bottomnav">
        ${[['#/dashboard', 'dash', 'Home'], ['#/csr', 'book', 'CSR'], ['#/projects', 'folder', 'Projects'],
      ['#/project/' + (S.proj.id || 1), 'ruler', 'Measure'], ...(isAdmin ? [['#/admin', 'shield', 'Admin']] : [])]
      .map(([h, i, l]) => `<a href="${h}" data-nav="${h}">${ICON[i]}<span>${l}</span></a>`).join('')}
      </nav>
    </main>
  </div>`;
}
function markNav() {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    const h = a.getAttribute('data-nav'); const cur = '#/' + S.route.name;
    a.classList.toggle('on', h === cur || (S.route.name === 'project' && h.startsWith('#/project')));
  });
}
function setTitle(t, s) { const a = document.getElementById('ptitle'); if (a) a.innerHTML = `${esc(t)}<small>${esc(s || '')}</small>`; }
function logout() {
  S.token = ''; S.user = null; S.demo = false;
  localStorage.removeItem('smartmb_token'); localStorage.removeItem('smartmb_user');
  location.hash = ''; renderLogin();
}

/* ------------------------------------------------------------------- login */
function renderLogin() {
  document.getElementById('app').innerHTML = `
  <div class="login-wrap">
    <div class="login-card">
      <div class="top">
        <div class="mark">SM</div>
        <h2>Smart-MB Platform</h2>
        <p>Centralized CSR Database &amp; Site Verification System<br>Public Works Department (Electrical Wing) · Government of Maharashtra</p>
      </div>
      <div class="bd stack">
        <div id="login-err"></div>
        <div><label class="f">Official email</label><input class="i" id="l-email" type="email" placeholder="je.nashik@pwd.maharashtra.gov.in" autocomplete="username"></div>
        <div><label class="f">Password</label><input class="i" id="l-pass" type="password" placeholder="••••••••" autocomplete="current-password"></div>
        <button class="btn pri block" data-act="login" id="l-btn">Sign in</button>
        <div class="hr"></div>
        <div class="muted small"><b>Demo accounts</b> (click to fill)</div>
        <div class="demo-creds">
          <button class="btn sm" data-act="fill" data-email="admin@pwd.maharashtra.gov.in" data-pass="Admin@123">
            <span class="badge brand">Super Admin</span> admin@pwd.maharashtra.gov.in</button>
          <button class="btn sm" data-act="fill" data-email="je.nashik@pwd.maharashtra.gov.in" data-pass="Engineer@123">
            <span class="badge ok">Site Engineer</span> je.nashik@pwd.maharashtra.gov.in</button>
        </div>
        ${DEMO ? `<button class="btn block" data-act="demo" style="margin-top:4px">${ICON.sparkle} Explore offline demo (sample data)</button>` : ''}
        <div class="hint">Live data is served by the Smart-MB API. The offline demo renders a bundled snapshot and is read-only.</div>
      </div>
    </div>
  </div>`;
}

/* --------------------------------------------------------------- dashboard */
async function viewDashboard(view) {
  setTitle('Dashboard', 'Role-aware overview of works, measurements and CSR usage');
  const [d, csrV] = await Promise.all([api('/api/dashboard'), api('/api/csr/versions').catch(() => ({ versions: [] }))]);
  if (!S.csr.fy && csrV.versions.length) {
    S.csr.fy = csrV.versions[0].fy; S.csr.region = csrV.versions[0].region;
  }
  const t = d.totals;
  const cat = d.by_category || [];
  const maxAmt = Math.max(1, ...cat.map((c) => c.amount));
  view.innerHTML = `
    <div class="grid g4" style="margin-bottom:14px">
      <div class="card kpi"><div class="lbl">Active projects</div><div class="val">${t.projects}</div>
        <div class="foot">${(d.projects || []).filter(p => p.status === 'active').length} in progress</div></div>
      <div class="card kpi"><div class="lbl">Measurement entries</div><div class="val">${t.measurements}</div>
        <div class="foot">jointly recorded at site</div></div>
      <div class="card kpi"><div class="lbl">Measured value</div><div class="val sm">₹ ${inr(t.measured_value)}</div>
        <div class="foot">valued at Master CSR rates</div></div>
      <div class="card kpi"><div class="lbl">Master CSR items</div><div class="val sm">${csrV.versions.filter(v => v.fy === S.csr.fy).map(v => v.item_count).reduce((a, b) => a + b, 0) || '—'}</div>
        <div class="foot">CSR ${esc(S.csr.fy)} · ${csrV.versions.length} FY × region versions</div></div>
    </div>

    <div class="grid g2" style="align-items:start">
      <div class="card">
        <div class="hd"><h3>Works in hand</h3><div style="flex:1"></div>
          <a class="btn sm" href="#/projects">${ICON.folder} All projects</a></div>
        <div class="bd stack">
          ${(d.projects || []).length ? d.projects.map((p) => `
            <div class="item-card" style="cursor:pointer" data-act="open-project" data-id="${p.id}">
              <div class="row between"><div class="pill-code">${esc(p.project_code)}</div>${badgeFor(p.status)}</div>
              <div style="font-weight:650">${esc(p.name)}</div>
              <div class="small muted">${esc(p.division || '')} · TS ${esc(p.ts_no || '—')} · MB ${esc(p.mb_no || '—')}</div>
              <div class="progress"><i style="width:${Math.min(100, p.progress_pct || 0)}%"></i></div>
              <div class="row between small">
                <span class="muted">${p.items} items · est. ₹ ${inr(p.tendered_amount)}</span>
                <span><b>${p.progress_pct}%</b> measured (₹ ${inr(p.measured_amount)})</span></div>
            </div>`).join('') : `<div class="empty">${ICON.folder}<div>No projects yet. Create one from the Projects screen.</div></div>`}
        </div>
      </div>

      <div class="stack">
        <div class="card">
          <div class="hd"><h3>Value distribution by work head</h3></div>
          <div class="bd stack">
            ${cat.length ? cat.slice(0, 7).map((c) => `
              <div class="bar-row">
                <span class="nowrap" title="${esc(c.category)}">${esc((c.category || 'Other').slice(0, 20))}</span>
                <span class="track"><i style="width:${Math.max(3, (c.amount / maxAmt) * 100)}%"></i></span>
                <span class="right mono small">₹ ${inr(c.amount, 0)}</span>
              </div>`).join('') : '<div class="muted small">No measured value yet.</div>'}
          </div>
        </div>
        <div class="card">
          <div class="hd"><h3>Latest joint measurements</h3></div>
          <div class="bd tight scrollx">
            <table class="tbl"><thead><tr><th>Item</th><th>Location</th><th>Item description</th><th class="num">Qty</th><th class="num">Amount</th><th></th></tr></thead>
            <tbody>${(d.recent_measurements || []).map((m) => `
              <tr><td><span class="pill-code">${esc(m.item_code)}</span></td>
                <td class="small">${esc(m.room_name || '—')}</td>
                <td class="small">${esc((m.description || '').slice(0, 74))}…</td>
                <td class="num">${smartNum(m.measured_qty)} ${esc(m.unit)}</td>
                <td class="num">₹ ${inr((m.measured_qty || 0) * (m.rate || 0), 0)}</td>
                <td><a class="btn sm" href="#/project/${m.project_id}">Open</a></td></tr>`).join('')
      || '<tr><td colspan="6" class="empty">No measurements recorded yet.</td></tr>'}</tbody></table>
          </div>
        </div>
      </div>
    </div>`;
}

/* --------------------------------------------------------------- master CSR */
async function viewCSR(view) {
  setTitle('Master CSR Database', 'Admin-controlled Schedule of Rates — every project maps against this source of truth');
  const isAdmin = S.user.role === 'admin';
  const versions = (await api('/api/csr/versions')).versions;
  if (!S.csr.fy && versions.length) { S.csr.fy = versions[0].fy; S.csr.region = versions[0].region; }
  const fys = [...new Set(versions.map((v) => v.fy))];
  const regions = versions.filter((v) => v.fy === S.csr.fy).map((v) => v.region);
  if (!regions.includes(S.csr.region)) S.csr.region = regions[0];
  const qp = new URLSearchParams({ fy: S.csr.fy, region: S.csr.region, limit: S.csr.limit, offset: S.csr.page * S.csr.limit });
  if (S.csr.q) qp.set('q', S.csr.q);
  if (S.csr.category) qp.set('category', S.csr.category);
  if (S.csr.chapter) qp.set('chapter', S.csr.chapter);
  const [list, facets] = await Promise.all([
    api('/api/csr/items?' + qp.toString()), api(`/api/csr/facets?fy=${S.csr.fy}&region=${S.csr.region}`)]);

  view.innerHTML = `
    ${isAdmin ? `<div class="card" style="margin-bottom:14px">
      <div class="hd">${ICON.up}<h3>Master CSR control</h3><div style="flex:1"></div>
        <span class="badge brand">Super Admin</span></div>
      <div class="bd">
        <div class="notice info" style="margin-bottom:12px">${ICON.shield}<div>The Master Database is the permanent, pre-loaded Schedule of Rates. Projects only <b>reference</b> it: descriptions, units and rates are always pulled from here, so a blurry estimate PDF can never corrupt a legal description or a rate.</div></div>
        <div class="grid g4">
          <div><label class="f">Financial year</label><select class="i" id="imp-fy">${fys.map((f) => `<option>${esc(f)}</option>`).join('')}</select></div>
          <div><label class="f">Region</label><select class="i" id="imp-region">${regions.map((r) => `<option>${esc(r)}</option>`).join('')}</select></div>
          <div><label class="f">Import mode</label><select class="i" id="imp-mode"><option value="merge">Merge / update existing codes</option><option value="replace">Replace entire version</option></select></div>
          <div><label class="f">CSR file (.xlsx / .csv)</label><input class="i" type="file" id="imp-file" accept=".xlsx,.xlsm,.csv"></div>
        </div>
        <div class="row" style="margin-top:12px">
          <button class="btn" data-act="csr-preview">${ICON.sparkle} Preview &amp; validate</button>
          <button class="btn pri" data-act="csr-commit" disabled id="imp-commit">${ICON.check} Commit to Master DB</button>
          <button class="btn" data-act="csr-template">${ICON.doc} Download template</button>
          <div style="flex:1"></div>
          <button class="btn" data-act="csr-export">${ICON.down} Export CSR ${esc(S.csr.fy)} · ${esc(S.csr.region)}</button>
        </div>
        <div id="imp-result" style="margin-top:12px"></div>
      </div></div>` : ''}

    <div class="card" style="margin-bottom:14px">
      <div class="bd">
        <div class="row">
          <div style="min-width:190px"><label class="f">Financial year</label>
            <select class="i" id="csr-fy">${fys.map((f) => `<option ${f === S.csr.fy ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select></div>
          <div style="min-width:190px"><label class="f">Region</label>
            <select class="i" id="csr-region">${regions.map((r) => `<option ${r === S.csr.region ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></div>
          <div style="flex:1;min-width:200px"><label class="f">Search code / description / tag</label>
            <input class="i" id="csr-q" placeholder="e.g. 1-3-6, exhaust fan, earthing, LED street light" value="${esc(S.csr.q)}"></div>
          <div style="min-width:150px"><label class="f">Chapter</label>
            <select class="i" id="csr-chapter"><option value="">All chapters</option>
            ${facets.chapters.map((c) => `<option value="${c.value}" ${String(S.csr.chapter) === String(c.value) ? 'selected' : ''}>${c.value} · ${esc(c.name)} (${c.count})</option>`).join('')}</select></div>
        </div>
        <div class="row" style="margin-top:10px">
          <button class="chip ${!S.csr.category ? 'on' : ''}" data-act="csr-cat" data-v="">All heads (${list.total})</button>
          ${facets.categories.map((c) => `<button class="chip ${S.csr.category === c.value ? 'on' : ''}" data-act="csr-cat" data-v="${esc(c.value)}">${esc(c.value)} · ${c.count}</button>`).join('')}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="hd"><h3>${list.total} items · CSR ${esc(S.csr.fy)} (${esc(S.csr.region)} region)</h3><div style="flex:1"></div>
        <span class="badge">page ${S.csr.page + 1} of ${Math.max(1, Math.ceil(list.total / S.csr.limit))}</span></div>
      <div class="bd tight scrollx">
        <table class="tbl">
          <thead><tr><th>Item code</th><th>Description (legal text from Master CSR)</th><th>Unit</th><th class="num">Rate (₹)</th><th>Head / Section</th></tr></thead>
          <tbody>${list.items.map((it) => `
            <tr data-act="csr-item" data-id="${it.id}" style="cursor:pointer">
              <td><span class="pill-code">${esc(it.item_code)}</span>${it.is_new ? ' <span class="badge brand tiny">NEW</span>' : ''}</td>
              <td class="small">${esc(it.short_desc || it.description)}</td>
              <td class="nowrap">${esc(it.unit)}</td>
              <td class="num mono">${inr(it.rate)}</td>
              <td class="small muted">${esc(it.category || '')}<br><span class="tiny">${esc(it.section || '')}</span></td>
            </tr>`).join('') || '<tr><td colspan="5" class="empty">No items match the filters.</td></tr>'}</tbody>
        </table>
      </div>
      <div class="bd row between">
        <button class="btn sm" data-act="csr-page" data-p="${S.csr.page - 1}" ${S.csr.page <= 0 ? 'disabled' : ''}>← Previous</button>
        <span class="muted small">Showing ${list.items.length} of ${list.total}</span>
        <button class="btn sm" data-act="csr-page" data-p="${S.csr.page + 1}" ${(S.csr.page + 1) * S.csr.limit >= list.total ? 'disabled' : ''}>Next →</button>
      </div>
    </div>`;

  const q = document.getElementById('csr-q');
  if (q) q.addEventListener('input', debounce((e) => { S.csr.q = e.target.value; S.csr.page = 0; render(); }, 420));
}

async function csrItemSheet(id) {
  const r = await api(`/api/csr/items?fy=${S.csr.fy}&region=${S.csr.region}&limit=300`);
  const it = (r.items || []).find((x) => x.id === Number(id));
  if (!it) { toast('Item not found in this CSR version', 'bad'); return; }
  const full = await api(`/api/csr/items?fy=${S.csr.fy}&region=${S.csr.region}&q=${encodeURIComponent(it.item_code)}&limit=5`);
  const item = (full.items || []).find((x) => x.item_code === it.item_code) || it;
  sheet({
    title: `CSR item ${item.item_code}`,
    body: `
      <div class="row" style="margin-bottom:10px">
        <span class="badge brand">CSR ${esc(S.csr.fy)}</span>
        <span class="badge">${esc(S.csr.region)} region</span>
        <span class="badge info">${esc(item.category || '')}</span>
        ${item.is_new ? '<span class="badge warn">New item</span>' : ''}
      </div>
      <div class="small" style="line-height:1.55">${esc(item.description)}</div>
      <div class="hr"></div>
      <div class="grid g2">
        <div><div class="lbl muted tiny">Unit</div><b>${esc(item.unit)}</b></div>
        <div><div class="lbl muted tiny">Completed rate</div><b>₹ ${inr(item.rate)}</b></div>
        <div><div class="lbl muted tiny">Material</div>₹ ${inr(item.material_rate)}</div>
        <div><div class="lbl muted tiny">Labour</div>₹ ${inr(item.labour_rate)}</div>
        <div><div class="lbl muted tiny">Chapter</div>${esc(item.chapter || '')} — ${esc(item.section || '')}</div>
        <div><div class="lbl muted tiny">Specification</div>${esc(item.spec_no || '—')}</div>
      </div>
      <div class="hr"></div>
      <label class="f">Metadata tags (drive the “Extra Item” search)</label>
      ${S.user.role === 'admin'
        ? `<div class="row"><input class="i" id="tag-input" value="${esc((item.tags || []).join(', '))}" placeholder="Fan, Earthing, Safety">
            <button class="btn pri sm" data-act="save-tags" data-id="${item.id}">Save tags</button></div>`
        : `<div class="row">${(item.tags || []).map((t) => `<span class="badge">${esc(t)}</span>`).join('') || '<span class="muted small">No tags</span>'}</div>`}
      <div class="notice info" style="margin-top:12px">${ICON.sparkle}<div>Every estimate that references <b>${esc(item.item_code)}</b> will print this exact description, unit and rate on the Form-23 MB, regardless of what the uploaded PDF says.</div></div>`,
  });
}

/* ------------------------------------------------------------ projects list */
async function viewProjects(view) {
  setTitle('Projects & Estimates', 'Create a work, import the Technical Sanction estimate, then verify at site');
  const { projects } = await api('/api/projects');
  view.innerHTML = `
    <div class="row between" style="margin-bottom:12px">
      <div class="muted small">${projects.length} work(s) visible to you · ${S.user.role === 'admin' ? 'all divisions (admin view)' : 'your sub-division'}</div>
      <button class="btn pri" data-act="new-project">${ICON.plus} New project</button>
    </div>
    <div class="grid g-auto">
      ${projects.map((p) => `
        <div class="card item-card" style="cursor:pointer" data-act="open-project" data-id="${p.id}">
          <div class="row between"><span class="pill-code">${esc(p.project_code)}</span>${badgeFor(p.status)}</div>
          <div style="font-weight:650">${esc(p.name)}</div>
          <div class="small muted">${esc(p.division || '')}<br>Engineer: ${esc(p.engineer_name || '—')}</div>
          <div class="progress ${p.progress_pct < 35 ? 'warn' : ''}"><i style="width:${Math.min(100, p.progress_pct)}%"></i></div>
          <div class="row between small">
            <span>${p.items} items</span><span><b>${p.progress_pct}%</b> measured</span></div>
          <div class="row between tiny muted">
            <span>Estimate ₹ ${inr(p.tendered_amount, 0)}</span><span>MB ${esc(p.mb_no || '—')}</span></div>
        </div>`).join('') || `<div class="card"><div class="empty">${ICON.folder}<div>No projects yet — create your first work.</div></div></div>`}
    </div>`;
}

function newProjectSheet() {
  const u = S.user || {};
  sheet({
    title: 'New project',
    wide: true,
    body: `
      <div class="grid g2">
        <div style="grid-column:1/-1"><label class="f">Name of work *</label>
          <input class="i" id="np-name" placeholder="Electrical installation to ... building at ..."></div>
        <div><label class="f">Scheme / head</label><input class="i" id="np-scheme" placeholder="District Annual Plan 2024-25"></div>
        <div><label class="f">Name of agency</label><input class="i" id="np-agency" placeholder="M/s ..."></div>
        <div><label class="f">Division</label><input class="i" id="np-div" value="${esc(u.division || '')}"></div>
        <div><label class="f">Circle</label><input class="i" id="np-circle" value="${esc(u.circle || '')}"></div>
        <div><label class="f">Region <span class="muted tiny">(also picks the CSR version used for mapping)</span></label>
          <select class="i" id="np-region">${['Pune', 'Mumbai', 'Nagpur', 'Nashik', 'Chhatrapati Sambhajinagar', 'Konkan', 'Amravati'].map((r) => `<option ${(u.region || '') === r ? 'selected' : ''}>${r}</option>`).join('')}</select></div>
        <div><label class="f">CSR financial year</label>
          <select class="i" id="np-fy"><option>2025-26</option><option selected>2024-25</option><option>2023-24</option></select></div>
        <div><label class="f">Estimate No.</label><input class="i" id="np-est" placeholder="EST/ELE/NSK/2024-25/017"></div>
        <div><label class="f">Technical Sanction No.</label><input class="i" id="np-ts" placeholder="TS/ELE/NSK/2024-25/041"></div>
        <div><label class="f">TS date</label><input class="i" id="np-tsdate" type="date"></div>
        <div><label class="f">TS amount (₹)</label><input class="i" id="np-amt" type="number" placeholder="auto-filled from import"></div>
        <div><label class="f">MB No.</label><input class="i" id="np-mb" value="MB-01"></div>
        <div><label class="f">Agreement No.</label><input class="i" id="np-agr" placeholder="AG/ELE/..."></div>
        <div style="grid-column:1/-1"><label class="f">Rooms / locations for joint measurement (one per line — “Floor | Room”)</label>
          <textarea class="i" id="np-rooms" placeholder="Ground Floor | Head Master Cabin
Ground Floor | Office Room 1
First Floor | Class Room 1
Terrace / External | Corridor & External Area"></textarea></div>
      </div>`,
    footer: `<button class="btn" data-act="close-sheet">Cancel</button><button class="btn pri" data-act="create-project">Create project</button>`,
  });

  document.querySelector('[data-act="create-project"]').addEventListener('click', async () => {
    const val = (id) => (document.getElementById(id) || {}).value || '';
    const name = val('np-name').trim();
    if (name.length < 6) { toast('Please enter a proper name of work', 'bad'); return; }
    const rooms = val('np-rooms').split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
      const [a, b] = l.split('|').map((x) => (x || '').trim());
      return b ? { floor: a, name: b } : { floor: '', name: a };
    });
    const payload = {
      name, scheme: val('np-scheme'), agency: val('np-agency'), division: val('np-div'), circle: val('np-circle'),
      region: val('np-region'), csr_fy: val('np-fy'), csr_region: val('np-region'),
      estimate_no: val('np-est'), ts_no: val('np-ts'), ts_date: val('np-tsdate'),
      ts_amount: Number(val('np-amt') || 0), mb_no: val('np-mb'), agreement_no: val('np-agr'), rooms,
    };
    try {
      const r = await api('/api/projects', { method: 'POST', body: payload });
      closeSheet(); toast('Project created — now import the estimate', 'ok');
      location.hash = `#/project/${r.project_id}`;
      S.proj.tab = 'import';
      render();
    } catch (e) { toast(e.message, 'bad'); }
  });
}

/* ------------------------------------------------------------ project view */
async function viewProject(view, id) {
  if (!id) { view.innerHTML = `<div class="empty">No project selected</div>`; return; }
  S.proj.id = id;
  let p, checklist, verify;
  try {
    [p, checklist, verify] = await Promise.all([
    api(`/api/projects/${id}`), api(`/api/projects/${id}/checklist${S.proj.room ? '?room_id=' + S.proj.room : ''}`),
      api(`/api/projects/${id}/verify`).catch(() => ({ totals: { pending: 0, cells: 0 }, docs: [], rooms: [] }))]);
  } catch (e) {
    setTitle('Work not found', 'This record is no longer on the server');
    view.innerHTML = `
      <div class="card"><div class="hd">${ICON.warn}<h3>Work #${esc(String(id))} is not on this server</h3></div>
        <div class="bd stack">
          <div class="notice warn">${ICON.warn}<div>Either it was never created here, or the server was restarted on
            hosting without a permanent disk and the database started empty. Works, measurements and photos created
            before a restart are gone.</div></div>
          <div class="row wrap-gap">
            <button class="btn pri" data-act="goto-projects">Show the works that exist now</button>
            <button class="btn" data-act="bk-hint">How do I stop losing data?</button>
          </div>
        </div></div>`;
    return;
  }
  S.proj.verifyData = verify;
  S.proj.data = p;
  const pr = p.project;
  setTitle(pr.name, `${pr.project_code} · ${pr.division || ''}`);
  const tab = S.proj.tab;
  view.innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <div class="bd">
        <div class="row between" style="align-items:flex-start">
          <div style="min-width:260px;flex:1">
            <div class="row"><span class="pill-code">${esc(pr.project_code)}</span>${badgeFor(pr.status)}
              <span class="badge brand">CSR ${esc(pr.csr_fy)} · ${esc(pr.csr_region)}</span>
              ${p.non_schedule_items ? `<span class="badge ns">${p.non_schedule_items} non-schedule</span>` : ''}</div>
            <div class="small muted" style="margin-top:6px">
              TS: ${esc(pr.ts_no || '—')} dt. ${dt(pr.ts_date)} · Estimate: ${esc(pr.estimate_no || '—')} ·
              MB ${esc(pr.mb_no || '—')} · Agreement ${esc(pr.agreement_no || '—')}</div>
          </div>
          <div class="row">
            <button class="btn" data-act="gen" data-fmt="xlsx">${ICON.doc} Excel MB</button>
            <button class="btn pri" data-act="gen" data-fmt="pdf">${ICON.doc} Form-23 MB (PDF)</button>
          </div>
        </div>
        <div class="grid g4" style="margin-top:14px">
          <div class="kpi" style="padding:8px 0"><div class="lbl">Checklist items</div><div class="val sm">${p.items}</div>
            <div class="foot">${p.items_measured} measured · ${p.items_pending} pending</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Tendered (estimate)</div><div class="val sm">₹ ${inr(p.tendered_amount, 0)}</div>
            <div class="foot">${Array.isArray(p.rooms) ? p.rooms.length : (p.rooms || 0)} rooms · ${Array.isArray(p.photos) ? p.photos.length : (p.photos || 0)} site photos</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Measured value</div><div class="val sm">₹ ${inr(p.measured_amount, 0)}</div>
            <div class="foot">${p.measurement_rows} measurement rows</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Net deviation</div>
            <div class="val sm" style="color:${p.net_deviation >= 0 ? 'var(--bad)' : 'var(--ok)'}">${p.net_deviation >= 0 ? '+' : '−'} ₹ ${inr(Math.abs(p.net_deviation), 0)}</div>
            <div class="foot">${p.critical_deviations} item(s) beyond ±10%</div></div>
        </div>
        <div class="progress" style="margin-top:12px"><i style="width:${Math.min(100, p.progress_pct)}%"></i></div>
      </div>
      <div class="bd tight" style="padding:10px 12px">
        <div class="tabs" id="ptabs">
          ${[['overview', 'Overview'], ['import', 'Smart Import'], ['verify', `Site Verify${(verify.totals && verify.totals.pending) ? ' (' + verify.totals.pending + ')' : ''}`],
      ['checklist', `Checklist (${checklist.totals.done}/${checklist.totals.items})`],
      ['measurements', 'Measurements'], ['deviations', `Deviations (${p.critical_deviations})`], ['reports', 'Form-23 & Reports']]
      .map(([k, l]) => `<button data-act="ptab" data-t="${k}" class="${tab === k ? 'on' : ''}">${l}</button>`).join('')}
        </div>
      </div>
    </div>
    <div id="tabbody"></div>`;
  const body = document.getElementById('tabbody');
  if (!body) return;
  body.innerHTML = loading();
  if (tab === 'overview') return tabOverview(body, p, checklist);
  if (tab === 'import') return tabImport(body, p);
  if (tab === 'verify') return tabVerify(body, p, verify);
  if (tab === 'checklist') return tabChecklist(body, p, checklist);
  if (tab === 'measurements') return tabMeasurements(body, p);
  if (tab === 'deviations') return tabDeviations(body, p);
  if (tab === 'reports') return tabReports(body, p);
}

async function tabOverview(body, p, checklist) {
  const pr = p.project;
  const dev = await api(`/api/projects/${p.project.id}/deviations`);
  const critical = dev.rows.filter((r) => r.severity === 'critical' || r.severity === 'review').slice(0, 6);
  let sched = { docs: [] };
  try { sched = await api(`/api/projects/${pr.id}/schedules`); } catch (e) { sched = { docs: [] }; }
  if (!(sched.docs || []).length && S.demo && typeof DEMO !== 'undefined' && DEMO.verify && DEMO.verify.docs) {
    sched = { docs: DEMO.verify.docs };            // the offline demo snapshot ships one document
  }
  const doc0 = (sched.docs || [])[0];
  body.innerHTML = `
    <div class="card sched-step" style="margin-bottom:14px">
      <div class="hd">${ICON.doc}<h3>Step 2 · Descriptive schedule (rooms, floors &amp; location-wise quantities)</h3>
        <div style="flex:1"></div>
        ${doc0
    ? `<span class="badge ok">${esc(doc0.filename)} · ${doc0.cells || 0} quantities</span>`
    : '<span class="badge warn">not uploaded yet</span>'}</div>
      <div class="bd row between wrap-gap">
        <div class="small muted" style="flex:1;min-width:220px">
          ${doc0
    ? `This project verifies against <b>${esc(doc0.filename)}</b> — ${doc0.locations || 0} room(s), ${doc0.cells || 0} quantities. Site Verify walks you through them room by room.`
    : 'Upload the <b>descriptive schedule</b> (PDF or Excel) or a <b>scan</b> of it. Every room, floor and location-wise quantity becomes a verification row, so at site you only change what differs.'}
        </div>
        <div class="row wrap-gap">
          <button class="btn pri big" data-act="goto-schedule">${ICON.up} ${doc0 ? 'Upload a different schedule' : 'Upload descriptive schedule'}</button>
          ${doc0 ? '<button class="btn big" data-act="goto-verify">Start joint verification</button>' : ''}
        </div>
      </div>
    </div>
    <div class="grid g2" style="align-items:start">
      <div class="card">
        <div class="hd"><h3>Work particulars</h3></div>
        <div class="bd">
          <div class="stat-line"><span>Name of work</span><b style="text-align:right">${esc(pr.name)}</b></div>
          <div class="stat-line"><span>Scheme / head</span><span>${esc(pr.scheme || '—')}</span></div>
          <div class="stat-line"><span>Agency</span><span>${esc(pr.agency || '—')}</span></div>
          <div class="stat-line"><span>Division / Circle</span><span>${esc(pr.division || '—')} / ${esc(pr.circle || '—')}</span></div>
          <div class="stat-line"><span>Estimate No.</span><span>${esc(pr.estimate_no || '—')}</span></div>
          <div class="stat-line"><span>Technical Sanction</span><span>${esc(pr.ts_no || '—')} · ${dt(pr.ts_date)}</span></div>
          <div class="stat-line"><span>Agreement</span><span>${esc(pr.agreement_no || '—')}</span></div>
          <div class="stat-line"><span>MB No. / CSR</span><span>${esc(pr.mb_no || '—')} / ${esc(pr.csr_fy)} ${esc(pr.csr_region)}</span></div>
          <div class="stat-line"><span>Engineer in charge</span><span>${esc((p.team && p.team.name) || '—')}</span></div>
          <div class="hr"></div>
          <div class="row between">
            <span class="tiny muted">Danger zone — deletes the project with its checklist, measurements and photos.</span>
            <button class="btn sm danger" data-act="del-project" data-id="${pr.id}">Delete project</button>
          </div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="hd"><h3>Measurement progress by location</h3><div style="flex:1"></div>
            <button class="btn sm" data-act="add-room">${ICON.plus} Room</button></div>
          <div class="bd stack">
            ${checklist.rooms.length ? checklist.rooms.map((r) => `
              <div class="stat-line"><span>${esc(r.floor ? r.floor + ' · ' : '')}${esc(r.name)}</span>
                <span class="small">${r.items_touched} item(s) · <b>${smartNum(r.qty)}</b> qty recorded</span></div>`).join('')
      : '<div class="muted small">No rooms yet — add rooms to group measurements like the MB.</div>'}
          </div>
        </div>
        <div class="card">
          <div class="hd"><h3>Deviation alerts</h3><div style="flex:1"></div>
            <span class="badge ${critical.length ? 'bad' : 'ok'}">${critical.length ? critical.length + ' to review' : 'all within limits'}</span></div>
          <div class="bd stack">
            ${critical.length ? critical.map((r) => `
              <div class="stat-line"><span><span class="pill-code">${esc(r.item_code)}</span> ${esc((r.description || '').slice(0, 52))}…</span>
                <span class="nowrap ${r.dev_qty > 0 ? 'badge bad' : 'badge ok'}">${r.dev_qty > 0 ? '+' : ''}${smartNum(r.dev_qty)} ${esc(r.unit)} (${r.dev_pct}%)</span></div>`).join('')
      : '<div class="muted small">No item crosses the ±10% variation limit. Excess/savings statement is generated with the MB.</div>'}
            <div class="row" style="margin-top:6px">
              <span class="badge ok">Savings ₹ ${inr(dev.totals.saving_amount, 0)}</span>
              <span class="badge bad">Excess ₹ ${inr(dev.totals.excess_amount, 0)}</span>
              <span class="badge brand">Net ₹ ${inr(dev.totals.net_amount, 0)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>`;
}

async function tabImport(body, p) {
  const pr = p.project;
  const hasEst = (p.items || 0) > 0;
  let schedCount = 0;
  try {
    const wl = await api(`/api/projects/${pr.id}/verify`);
    schedCount = ((wl.totals || {}).cells || 0);
  } catch (e) { schedCount = 0; }
  body.innerHTML = `
    <div class="card import-steps" style="margin-bottom:14px"><div class="bd row between wrap-gap">
      <div class="small"><b>Two things to upload</b>
        <div class="muted">① the estimate abstract · ② the descriptive schedule of the building</div></div>
      <div class="row wrap-gap">
        <button class="btn sm" data-act="jump-estimate">① Estimate abstract ${hasEst ? '<span class="badge ok">done</span>' : '<span class="badge warn">to do</span>'}</button>
        <button class="btn sm pri" data-act="jump-schedule">② Descriptive schedule ${schedCount ? `<span class="badge ok">${schedCount} qty</span>` : '<span class="badge warn">to do</span>'}</button>
      </div>
    </div></div>
    <div class="grid g2" style="align-items:start">
      <div class="card" id="card-estimate">
        <div class="hd">${ICON.sparkle}<h3>Step 1 · Technical Sanction estimate (abstract)</h3></div>
        <div class="bd stack">
          <div class="notice info">${ICON.shield}<div>Descriptions and rates are taken from the <b>Master CSR ${esc(pr.csr_fy)} (${esc(pr.csr_region)})</b>. Only the item codes and quantities are read from your estimate — so a blurry or badly formatted PDF cannot corrupt the legal text.</div></div>
          <div><label class="f">Estimate file (PDF / Excel / CSV)</label><input class="i" type="file" id="est-file" accept=".pdf,.xlsx,.xlsm,.csv,.txt"></div>
          <div class="row">
            <button class="btn pri" data-act="parse-file">${ICON.up} Parse estimate</button>
            <button class="btn" data-act="sample-file">Try sample estimate</button>
          </div>
          <div class="hr"></div>
          <label class="f">…or paste the estimate / abstract text (works on mobile)</label>
          <textarea class="i" id="est-text" placeholder="1-9-1  Concealed type light point wiring with 1.5 sq.mm ...  Point  84
1-3-6  S&E mains with 3x4 sq.mm FRLSH copper PVC insulated wire  m  190
6-2-4  S&F RCCB double pole 40A 30 mA  No  6"></textarea>
          <button class="btn" data-act="parse-text">${ICON.sparkle} Parse pasted text</button>
          <div id="parse-note"></div>
        </div>
      </div>
      <div class="card" id="card-schedule">
        <div class="hd">${ICON.doc}<h3>Step 2 · Descriptive schedule (rooms, floors &amp; quantities)</h3></div>
        <div class="bd stack" id="import-schedule"></div>
      </div>
      </div>
      <div class="card" style="margin-top:14px">
        <div class="hd"><h3>How the Smart Map works</h3></div>
        <div class="bd small stack">
          <div><b>1 · Anchor-based extraction</b><div class="muted">The parser hunts for the item-number pattern <code class="kbd">^\\d+-\\d+-\\d+$</code> anywhere in the document, repairs OCR artefacts (O→0, l→1, S→5) and then looks for the quantity column.</div></div>
          <div><b>2 · Master lookup</b><div class="muted">If the anchor exists in the Master CSR, the description/unit/rate are taken from the database — the PDF text is ignored. PDF text is used only as a fallback for non-schedule items.</div></div>
          <div><b>3 · Reconciliation</b><div class="muted">Findings vs Master DB: matched · unit mismatch · low confidence · <b>Unknown_Item</b> (flagged for manual entry).</div></div>
          <div><b>4 · Checklist</b><div class="muted">Confirmed items become the mobile measurement checklist, grouped by room.</div></div>
          <div class="hr"></div>
          <button class="btn sm" data-act="show-prompt">${ICON.doc} View the parsing-engine prompt</button>
        </div>
      </div>
    <div id="import-result" style="margin-top:14px"></div>`;

  const schedHost = document.getElementById('import-schedule');   // the Step 2 card body
  if (schedHost) schedHost.innerHTML = svImportHTML(pr, true);
  svWireImport(schedHost || body, pr, () => {
    S.verify.section = 'rooms'; S.proj.tab = 'verify'; render();
    toast('Schedule imported — opening Site Verify', 'ok');
  });

  const f = body.querySelector('#est-file');
  const t = body.querySelector('#est-text');
  body.querySelector('[data-act="parse-file"]')?.addEventListener('click', async () => {
    if (!f || !f.files || !f.files[0]) { toast('Choose the estimate PDF/Excel/CSV first', 'bad'); return; }
    await runParse({ file: f.files[0] });
  });
  body.querySelector('[data-act="parse-text"]')?.addEventListener('click', async () => {
    const text = (t && t.value || '').trim();
    if (text.length < 10) { toast('Paste at least a couple of estimate rows', 'bad'); return; }
    await runParse({ text });
  });
  body.querySelector('[data-act="sample-file"]')?.addEventListener('click', async () => {
    const sample = await fetch('/api/samples/sample_estimate.xlsx').then((r) => r.blob())
      .then((b) => new File([b], 'sample_estimate.xlsx')).catch(() => null);
    if (!sample) { toast('Sample file not reachable — use paste mode instead', 'warn'); return; }
    await runParse({ file: sample });
  });
  if (S.importPreview) renderImportPreview(S.importPreview);
}

async function runParse({ file, text }) {
  const res = document.getElementById('import-result');
  res.innerHTML = loading('Parsing estimate — anchors, quantities, master lookup…');
  try {
    let data;
    if (file) {
      const fd = new FormData(); fd.append('file', file); fd.append('engine', 'auto');
      data = await api(`/api/projects/${S.proj.id}/parse-estimate`, { method: 'POST', form: fd });
    } else {
      data = await api(`/api/projects/${S.proj.id}/parse-text`, { method: 'POST', body: { text } });
    }
    S.importPreview = data;
    renderImportPreview(data);
    toast(`Parsed ${data.stats.unique_codes} item codes · ${data.stats.matched} matched`, 'ok');
  } catch (e) {
    const gone = /not found/i.test(e.message || '');
    res.innerHTML = gone ? `
      <div class="card"><div class="hd">${ICON.warn}<h3>This work is no longer on the server</h3></div>
        <div class="bd stack">
          <div class="notice warn">${ICON.warn}<div>The upload was refused because the server has no record of
            <b>#${S.proj.id}</b> any more. On hosting without a permanent disk, a restart or a redeploy starts the
            database empty — every work created before that moment is gone, and any upload against it says
            <i>Project not found</i>.</div></div>
          <div class="row wrap-gap">
            <button class="btn pri" data-act="recreate-project">Create this work again</button>
            <button class="btn" data-act="goto-projects">See the works that exist now</button>
          </div>
          <div class="small muted">An administrator can also restore the last automatic backup from
            <b>Admin Control → Data safety</b> and get the work back.</div>
        </div></div>` : `
      <div class="notice bad">${ICON.warn}<div><b>Parsing failed.</b><br>${esc(e.message)}</div></div>`;
  }
}

function renderImportPreview(data) {
  const res = document.getElementById('import-result');
  const rows = data.rows || [];
  const st = data.stats || {};
  const pj = (S.proj.data && S.proj.data.project) || {};
  const csrLabel = `${pj.csr_fy || ''} ${pj.csr_region || ''}`.trim();
  res.innerHTML = `
    <div class="card">
      <div class="hd">${ICON.check}<h3>Reconciliation preview</h3><div style="flex:1"></div>
        <span class="badge ok">${st.matched || 0} matched</span>
        ${st.matched_other_version ? `<span class="badge warn">${st.matched_other_version} from another CSR year</span>` : ''}
        <span class="badge ${st.unknown ? 'ns' : ''}">${st.unknown || 0} non-schedule</span>
        ${st.amount_cross_checked ? `<span class="badge ok">${st.amount_cross_checked} rows cross-checked</span>` : ''}
        ${st.low_confidence ? `<span class="badge warn">${st.low_confidence} low confidence</span>` : ''}
        ${st.duplicates ? `<span class="badge">${st.duplicates} duplicate anchor merged</span>` : ''}
      </div>
      <div class="bd">
        <div class="notice ${st.unknown ? 'warn' : 'ok'}">${ICON.sparkle}<div>
          Found <b>${st.unique_codes || st.anchors_found}</b> item row(s) in <b>${esc(data.filename || 'input')}</b>.
          <b>${st.matched}</b> matched the Master CSR database${st.matched_other_version ? ` (${st.matched_other_version} of them from another CSR year — confirm the rate)` : ''}${st.unknown ? `, and <b>${st.unknown}</b> item code(s) are <b>not in the Master CSR ${esc(csrLabel)}</b> — their description, unit and rate are taken from your abstract so they can still be billed as non-schedule items` : ' — nothing needs manual entry'}.
          Estimated value: <b>₹ ${inr(st.estimated_amount)}</b>.
          <br><span class="tiny">${esc(data.engine_note || '')}</span>
        </div></div>
        ${data.hint ? `<div class="notice info" style="margin-top:8px">${ICON.shield}<div>${esc(data.hint)}</div></div>` : ''}
        <div class="row" style="margin:10px 0">
          <button class="btn pri" data-act="confirm-import">${ICON.check} Confirm &amp; generate measurement checklist</button>
          <button class="btn" data-act="toggle-all-import">Select / deselect all</button>
          <label class="row small" style="gap:6px"><input type="checkbox" id="auto-rooms" checked> auto-create rooms from the estimate text</label>
          <div style="flex:1"></div>
          <span class="muted small">${rows.filter((r) => r.include).length} of ${rows.length} rows selected</span>
        </div>
      </div>
      <div class="bd tight scrollx">
        <table class="tbl" id="imp-table">
          <thead><tr><th></th><th>Printed code</th><th>Master code</th><th>Description (editable for non-schedule)</th>
            <th>Unit</th><th class="num">Qty</th><th class="num">Rate (₹)</th><th>Status &amp; flags</th></tr></thead>
          <tbody>${rows.map((r, i) => `
            <tr data-row="${i}">
              <td><input type="checkbox" data-act="inc" data-i="${i}" ${r.include ? 'checked' : ''}></td>
              <td class="mono small">${esc(r.printed_code)}${r.printed_code !== r.item_code ? `<br><span class="tiny muted">→ ${esc(r.item_code)}</span>` : ''}</td>
              <td><span class="pill-code">${esc(r.item_code)}</span></td>
              <td class="small">${r.is_non_schedule
      ? `<textarea class="i" style="min-height:52px;font-family:inherit" data-act="desc" data-i="${i}">${esc(r.description)}</textarea>`
      : esc((r.description || '').slice(0, 150)) + (r.description.length > 150 ? '…' : '')}</td>
              <td class="nowrap small">${esc(r.unit)}</td>
              <td class="num"><input class="i right" style="padding:5px 7px;min-width:74px" type="number" step="0.001" data-act="qty" data-i="${i}" value="${r.tendered_qty != null ? r.tendered_qty : ''}"></td>
              <td class="num">${r.is_non_schedule
      ? `<input class="i right" style="padding:5px 7px;min-width:80px" type="number" step="0.01" data-act="rate" data-i="${i}" value="${r.rate || ''}">`
      : inr(r.rate)}</td>
              <td class="small">${badgeFor(r.status)}
                ${(r.flags || []).map((f) => `<div class="tiny muted">• ${esc(f)}</div>`).join('')}
                <div class="tiny muted">conf ${(r.confidence * 100).toFixed(0)}% · ${esc(r.method || '')}${r.line_no ? ' · line ' + r.line_no : ''}</div></td>
            </tr>`).join('')}</tbody>
        </table>
      </div>
      <div class="bd">
        <details><summary class="small muted">Show extracted text (first 6 000 characters)</summary>
          <pre class="mono tiny" style="max-height:260px;overflow:auto;background:#0f172a;color:#cbd5e1;padding:12px;border-radius:10px">${esc(data.text_excerpt || '')}</pre>
        </details>
      </div>
    </div>`;

  res.querySelectorAll('[data-act="inc"]').forEach((cb) => cb.addEventListener('change', (e) => {
    rows[Number(e.target.dataset.i)].include = e.target.checked;
  }));
  res.querySelectorAll('[data-act="qty"]').forEach((inp) => inp.addEventListener('change', (e) => {
    const r = rows[Number(e.target.dataset.i)];
    r.tendered_qty = Number(e.target.value || 0); r.pdf_qty = r.tendered_qty;
  }));
  res.querySelectorAll('[data-act="rate"]').forEach((inp) => inp.addEventListener('change', (e) => {
    rows[Number(e.target.dataset.i)].rate = Number(e.target.value || 0);
  }));
  res.querySelectorAll('[data-act="desc"]').forEach((ta) => ta.addEventListener('change', (e) => {
    rows[Number(e.target.dataset.i)].description = e.target.value;
  }));
  res.querySelector('[data-act="confirm-import"]')?.addEventListener('click', async () => {
    const autoRooms = !!res.querySelector('#auto-rooms')?.checked;
    try {
      const r = await api(`/api/projects/${S.proj.id}/import-estimate`,
        { method: 'POST', body: { rows, create_rooms_from_text: autoRooms } });
      toast(`Checklist generated — ${r.created.length} item(s) added${r.skipped.length ? `, ${r.skipped.length} skipped` : ''}`, 'ok');
      S.importPreview = null; S.proj.tab = 'checklist'; render();
    } catch (e) { toast(e.message, 'bad'); }
  });
  res.querySelector('[data-act="toggle-all-import"]')?.addEventListener('click', () => {
    const anyOff = rows.some((r) => !r.include);
    rows.forEach((r) => { r.include = anyOff; });
    renderImportPreview(data);
  });
}

async function tabChecklist(body, p, cl) {
  const rooms = cl.rooms;
  const items = cl.items;
  body.innerHTML = `
    <div class="card" style="margin-bottom:12px">
      <div class="bd row between">
        <div class="row" style="overflow-x:auto;flex-wrap:nowrap;max-width:100%">
          <button class="chip ${!S.proj.room ? 'on' : ''}" data-act="room" data-id="">All locations</button>
          ${rooms.map((r) => `<button class="chip ${S.proj.room === r.id ? 'on' : ''}" data-act="room" data-id="${r.id}">
            ${esc(r.floor ? r.floor + ' · ' : '')}${esc(r.name)} ${r.rows_count ? `<span class="badge ok tiny">${smartNum(r.qty)}</span>` : ''}</button>`).join('')}
          <button class="chip ghost" data-act="add-room">${ICON.plus} Add room</button>
        </div>
        <div class="row">
          <button class="btn pri" data-act="extra-item">${ICON.plus} Extra / additional item</button>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="hd"><h3>Measurement checklist ${S.proj.room ? '· ' + esc((rooms.find((r) => r.id === S.proj.room) || {}).name || '') : ''}</h3>
        <div style="flex:1"></div><span class="badge ${cl.totals.done === cl.totals.items ? 'ok' : 'warn'}">${cl.totals.done}/${cl.totals.items} started</span>
        <span class="badge brand">${cl.totals.fully} fully measured</span></div>
      <div class="bd stack">
        ${items.length ? items.map((it) => {
    const pct = it.tendered_qty ? Math.min(100, (it.measured_qty / it.tendered_qty) * 100) : (it.measured_qty ? 100 : 0);
    const cls = it.measured_qty > it.tendered_qty + 1e-6 ? 'excess' : it.done ? 'done' : 'pending';
    return `
          <div class="item-card ${cls}">
            <div class="row between">
              <div class="row" style="min-width:0">
                <span class="pill-code">${esc(it.item_code)}</span>
                ${it.is_non_schedule ? '<span class="badge ns">Non-schedule</span>' : ''}
                ${it.source === 'extra' ? '<span class="badge info">Extra item</span>' : ''}
                ${it.measured_qty > it.tendered_qty + 1e-6 ? '<span class="badge bad">Excess</span>' : ''}
                ${it.fully_measured ? '<span class="badge ok">Complete</span>' : ''}
              </div>
              <button class="btn sm pri" data-act="measure" data-id="${it.id}">${ICON.ruler} Measure</button>
            </div>
            <div class="small">${esc(it.description)}</div>
            <div class="progress ${pct < 34 ? 'warn' : ''}"><i style="width:${pct}%"></i></div>
            <div class="row between tiny muted">
              <span>Tendered <b>${smartNum(it.tendered_qty)}</b> ${esc(it.unit)} · measured <b>${smartNum(it.measured_qty)}</b> · pending <b>${smartNum(it.pending_qty)}</b></span>
              <span>₹ ${inr(it.rate)} / ${esc(it.unit)} · value ₹ ${inr(it.measured_qty * it.rate, 0)}</span>
            </div>
          </div>`;
  }).join('') : `<div class="empty">${ICON.ruler}<div>No checklist items yet.<br>Import the Technical Sanction estimate from the <b>Smart Import</b> tab.</div></div>`}
      </div>
    </div>`;
}

async function tabMeasurements(body, p) {
  const { measurements, count, total_amount } = await api(`/api/projects/${S.proj.id}/measurements`);
  const grouped = {};
  measurements.forEach((m) => { const k = m.room_name || 'Unassigned'; (grouped[k] = grouped[k] || []).push(m); });
  body.innerHTML = `
    <div class="row between" style="margin-bottom:12px">
      <div class="muted small">${count} measurement entries · valued ₹ ${inr(total_amount)} at CSR rates</div>
      <button class="btn" data-act="gen" data-fmt="xlsx">${ICON.down} Excel MB</button>
    </div>
    ${Object.keys(grouped).length ? Object.entries(grouped).map(([room, rows]) => `
      <div class="card" style="margin-bottom:12px">
        <div class="hd">${ICON.ruler}<h3>${esc(room)}</h3><div style="flex:1"></div>
          <span class="badge">${rows.length} entries</span>
          <span class="badge brand">₹ ${inr(rows.reduce((a, r) => a + r.amount, 0), 0)}</span></div>
        <div class="bd tight scrollx">
          <table class="tbl"><thead><tr><th>Date</th><th>Item</th><th>Description</th><th class="num">No</th><th class="num">L</th>
            <th class="num">B</th><th class="num">H</th><th class="num">Qty</th><th class="num">Amount</th><th>Photos</th><th></th></tr></thead>
          <tbody>${rows.map((m) => `
            <tr><td class="small nowrap">${dt(m.measured_on)}</td>
              <td><span class="pill-code">${esc(m.item_code)}</span></td>
              <td class="small">${esc((m.description || '').slice(0, 70))}</td>
              <td class="num">${smartNum(m.nos)}</td><td class="num">${smartNum(m.length)}</td>
              <td class="num">${smartNum(m.breadth)}</td><td class="num">${smartNum(m.height)}</td>
              <td class="num"><b>${smartNum(m.measured_qty)}</b> ${esc(m.unit)}</td>
              <td class="num">₹ ${inr(m.amount, 0)}</td>
              <td class="nowrap">${m.photos ? `<button class="btn sm" data-act="gallery" data-id="${m.id}">${ICON.cam} ${m.photos}</button>` : ''}
                <button class="btn sm" data-act="photo" data-id="${m.id}" title="Attach site photo">${ICON.plus}${ICON.cam}</button></td>
              <td class="nowrap">
                <button class="btn sm" data-act="edit-meas" data-id="${m.id}">Edit</button>
                <button class="btn sm danger" data-act="del-meas" data-id="${m.id}">Del</button></td></tr>`).join('')}
          </tbody></table>
        </div>
      </div>`).join('') : `<div class="card"><div class="empty">${ICON.ruler}<div>No measurements recorded yet.<br>Open the <b>Checklist</b> tab and tap <b>Measure</b> against an item.</div></div></div>`}`;
}

async function tabDeviations(body, p) {
  const d = await api(`/api/projects/${S.proj.id}/deviations`);
  const t = d.totals;
  body.innerHTML = `
    <div class="grid g4" style="margin-bottom:12px">
      <div class="card kpi"><div class="lbl">Tendered value</div><div class="val sm">₹ ${inr(t.tendered_amount, 0)}</div><div class="foot">as per TS estimate</div></div>
      <div class="card kpi"><div class="lbl">Measured value</div><div class="val sm">₹ ${inr(t.measured_amount, 0)}</div><div class="foot">at Master CSR rates</div></div>
      <div class="card kpi"><div class="lbl">Excess</div><div class="val sm" style="color:var(--bad)">₹ ${inr(t.excess_amount, 0)}</div><div class="foot">${t.critical.length} item(s) need variation approval</div></div>
      <div class="card kpi"><div class="lbl">Savings</div><div class="val sm" style="color:var(--ok)">₹ ${inr(t.saving_amount, 0)}</div><div class="foot">net ₹ ${inr(t.net_amount, 0)}</div></div>
    </div>
    <div class="card">
      <div class="hd">${ICON.chart}<h3>Item-wise deviation — measured vs tendered</h3></div>
      <div class="bd tight scrollx">
        <table class="tbl">
          <thead><tr><th>Item code</th><th>Description</th><th>Unit</th><th class="num">Rate</th><th class="num">Tendered</th>
            <th class="num">Measured</th><th class="num">Deviation</th><th class="num">%</th><th class="num">Excess / Saving (₹)</th><th></th></tr></thead>
          <tbody>${d.rows.map((r) => `
            <tr>
              <td><span class="pill-code">${esc(r.item_code)}</span></td>
              <td class="small">${esc((r.description || '').slice(0, 88))}${r.is_non_schedule ? ' <span class="badge ns tiny">NS</span>' : ''}</td>
              <td class="nowrap">${esc(r.unit)}</td>
              <td class="num mono">${inr(r.rate)}</td>
              <td class="num">${smartNum(r.tendered)}</td>
              <td class="num"><b>${smartNum(r.measured)}</b></td>
              <td class="num" style="color:${r.dev_qty > 0 ? 'var(--bad)' : r.dev_qty < 0 ? 'var(--ok)' : 'inherit'}">${r.dev_qty > 0 ? '+' : ''}${smartNum(r.dev_qty)}</td>
              <td class="num">${r.dev_pct > 0 ? '+' : ''}${r.dev_pct}%</td>
              <td class="num" style="color:${r.dev_amount > 0 ? 'var(--bad)' : 'var(--ok)'}">${r.dev_amount > 0 ? '+' : ''}${inr(r.dev_amount, 0)}</td>
              <td>${r.severity === 'critical' ? '<span class="badge bad">Approval</span>' : r.severity === 'review' ? '<span class="badge warn">Review</span>' : badgeFor(r.status)}</td>
            </tr>`).join('')}</tbody>
        </table>
      </div>
      <div class="bd small muted">Excess beyond the permissible variation limit requires a formal variation / excess-item approval before the quantity is entered in the final bill. Savings are recouped automatically.</div>
    </div>`;
}

async function tabReports(body, p) {
  const pr = p.project;
  const cl = await api(`/api/projects/${p.project.id}/checklist`);
  body.innerHTML = `
    <div class="grid g2" style="align-items:start">
      <div class="card">
        <div class="hd">${ICON.doc}<h3>Form No. 23 — Measurement Book</h3></div>
        <div class="bd stack">
          <div class="notice ok">${ICON.check}<div>The MB is generated from the <b>Master CSR descriptions</b>, so the legal text is clean even when the source estimate was a bad scan. Item heads show the CSR code; sub-entries show the location (room) and the joint-measurement dimensions.</div></div>
          <div class="stat-line"><span>Work</span><b style="text-align:right">${esc(pr.name)}</b></div>
          <div class="stat-line"><span>MB No. / Agreement</span><span>${esc(pr.mb_no || '—')} / ${esc(pr.agreement_no || '—')}</span></div>
          <div class="stat-line"><span>Measurement period</span><span>${dt(pr.created_at)} → today</span></div>
          <div class="stat-line"><span>Items measured</span><span>${cl.totals.done} of ${cl.totals.items}</span></div>
          <div class="row">
            <button class="btn pri" data-act="dl" data-path="/api/projects/${pr.id}/form23.pdf" data-name="Form23_MB_${esc(pr.mb_no || pr.id)}.pdf">${ICON.down} Form-23 PDF (with deviation statement)</button>
            <button class="btn" data-act="dl" data-path="/api/projects/${pr.id}/form23.xlsx" data-name="Form23_MB_${esc(pr.mb_no || pr.id)}.xlsx">${ICON.down} Excel MB (3 sheets)</button>
            <button class="btn" data-act="dl" data-path="/api/projects/${pr.id}/form23.pdf?measured_only=true" data-name="Form23_MB_measured_only.pdf">Measured items only</button>
          </div>
          <div class="hint">PDF carries: work particulars, item-wise measurement table in MB column format (No · L · B · H · Qty · Rate · Amount), grand total, deviation statement and the four signature blocks (JE / DE / Contractor / EE).</div>
        </div>
      </div>
      <div class="card">
        <div class="hd"><h3>Report readiness</h3></div>
        <div class="bd stack small">
          <div class="stat-line"><span>Estimate items on checklist</span><b>${p.items}</b></div>
          <div class="stat-line"><span>Items with measurements</span><b>${p.items_measured}</b></div>
          <div class="stat-line"><span>Pending items</span><b>${p.items_pending}</b></div>
          <div class="stat-line"><span>Non-schedule items</span><b>${p.non_schedule_items}</b></div>
          <div class="stat-line"><span>Site photographs</span><b>${p.photos}</b></div>
          <div class="stat-line"><span>Critical deviations</span><b>${p.critical_deviations}</b></div>
          <div class="hr"></div>
          ${p.items_pending ? `<div class="notice warn">${ICON.warn}<div>${p.items_pending} item(s) have no measurement yet — they will print as “Not yet measured / nil” in the MB.</div></div>` : `<div class="notice ok">${ICON.check}<div>All checklist items carry measurements. The MB is ready for checking.</div></div>`}
          <div class="hr"></div>
          <div class="muted">Excel export contains three sheets: <b>Form-23 Measurements</b>, <b>Deviation Statement</b> and <b>Estimate Items</b> (with match method + confidence for audit).</div>
        </div>
      </div>
    </div>`;
}

/* ------------------------------------------------------- measurement capture */
function measureSheet(itemId, editRow) {
  sheet({
    title: editRow ? 'Edit measurement' : 'Record joint measurement',
    body: `<div id="ms-body">${loading('Loading item…')}</div>`,
    footer: `<button class="btn" data-act="close-sheet">Cancel</button>
      <button class="btn pri" id="ms-save" disabled>${editRow ? 'Update measurement' : 'Save measurement'}</button>`,
    onOpen: (el) => { el._saveBtn = el.querySelector('#ms-save'); },
  });
  (async () => {
    const cl = await api(`/api/projects/${S.proj.id}/checklist`);
    const item = (cl.items || []).find((x) => x.id === itemId);
    const rooms = cl.rooms || [];
    if (!item && !editRow) { document.getElementById('ms-body').innerHTML = '<div class="notice bad">Item not found</div>'; return; }
    const unit = (editRow && editRow.unit) || item.unit;
    const u = (unit || '').toLowerCase();
    const isLen = u === 'm' || u === 'kg' || u === 'rm';
    const rows = editRow || { nos: 1, length: 0, breadth: 0, height: 0, notes: '', measured_on: today(), room_id: S.proj.room || (rooms[0] && rooms[0].id) };
    document.getElementById('ms-body').innerHTML = `
      <div class="row" style="margin-bottom:10px">
        <span class="pill-code">${esc((editRow && editRow.item_code) || item.item_code)}</span>
        <span class="badge brand">${esc(unit)}</span>
        ${item ? `<span class="badge">Tendered ${smartNum(item.tendered_qty)}</span>` : ''}
      </div>
      <div class="small" style="margin-bottom:12px">${esc((editRow && editRow.description) || item.description)}</div>
      <div class="row" style="margin-bottom:10px">
        <div style="flex:1"><label class="f">Location / room</label>
          <select class="i" id="ms-room">
            <option value="">— unassigned —</option>
            ${rooms.map((r) => `<option value="${r.id}" ${String(rows.room_id) === String(r.id) ? 'selected' : ''}>${esc(r.floor ? r.floor + ' · ' : '')}${esc(r.name)}</option>`).join('')}
          </select></div>
        <div><label class="f">Measured on</label><input class="i" id="ms-date" type="date" value="${rows.measured_on || today()}"></div>
      </div>
      <div class="grid ${isLen ? 'g2' : 'g3'}">
        <div><label class="f">${isLen ? 'Length (m)' : 'No. / count'}</label>
          <input class="i" id="ms-a" type="number" step="0.01" value="${isLen ? (rows.length || '') : (rows.nos || 1)}"></div>
        ${isLen ? `<div><label class="f">No. of runs / sets</label><input class="i" id="ms-nos" type="number" step="1" value="${rows.nos || 1}"></div>` : ''}
        ${!isLen ? `<div><label class="f">Length (m)</label><input class="i" id="ms-l" type="number" step="0.01" value="${rows.length || ''}"></div>
                    <div><label class="f">Breadth (m)</label><input class="i" id="ms-b" type="number" step="0.01" value="${rows.breadth || ''}"></div>` : ''}
      </div>
      <div class="notice info" style="margin:12px 0">${ICON.ruler}<div>Quantity is computed from the MB columns and can be overridden if the site measurement follows a different basis.</div></div>
      <label class="f">Computed quantity (${esc(unit)}) — editable override</label>
      <input class="i" id="ms-qty" type="number" step="0.001" value="${rows.measured_qty || ''}" placeholder="auto">
      <div style="margin-top:10px"><label class="f">Joint measurement note</label>
        <textarea class="i" style="min-height:64px;font-family:inherit" id="ms-notes" placeholder="e.g. measured in presence of contractor representative; cable route as per approved drawing">${esc(rows.notes || '')}</textarea></div>
      <div style="margin-top:10px"><label class="f">Site photograph (optional)</label>
        <input class="i" type="file" id="ms-photo" accept="image/*" capture="environment"></div>`;
    let sheetRef = '';
    const calc = () => {
      const g = (x) => Number((document.getElementById(x) || {}).value || 0);
      const q = isLen ? g('ms-a') * (g('ms-nos') || 1) : ((g('ms-l') || 0) > 0 ? (g('ms-a') || 0) * g('ms-l') * ((g('ms-b') || 1) || 1) : g('ms-a'));
      if (!document.getElementById('ms-qty').dataset.touched) document.getElementById('ms-qty').value = q ? Number(q.toFixed(3)) : '';
    };
    ['ms-a', 'ms-l', 'ms-b', 'ms-nos'].forEach((id) => {
      const el = document.getElementById(id); if (el) el.addEventListener('input', calc);
    });
    document.getElementById('ms-qty').addEventListener('input', (e) => { e.target.dataset.touched = '1'; });
    const saveBtn = document.getElementById('ms-save');
    if (saveBtn) { saveBtn.disabled = false; saveBtn.addEventListener('click', async () => {
      const g = (x) => Number((document.getElementById(x) || {}).value || 0);
      const val = (x) => (document.getElementById(x) || {}).value || '';
      const payload = {
        project_item_id: itemId, room_id: val('ms-room') ? Number(val('ms-room')) : null,
        nos: isLen ? (g('ms-nos') || 1) : (g('ms-a') || 0),
        length: isLen ? g('ms-a') : g('ms-l'), breadth: isLen ? 0 : g('ms-b'), height: 0,
        notes: val('ms-notes'), measured_on: val('ms-date'),
        measured_qty: val('ms-qty') ? Number(val('ms-qty')) : null,
        // one id per sheet: if the reply is lost and the reading is replayed, the server
        // recognises it and does not record the same site reading twice
        client_ref: editRow ? undefined : (sheetRef || (sheetRef = 'm-' + Date.now() + '-' + Math.random().toString(16).slice(2, 8))),
      };
      try {
        let mid;
        if (editRow) { await api(`/api/measurements/${editRow.id}`, { method: 'PATCH', body: payload }); mid = editRow.id; }
        else {
          const r = await queueable('/api/measurements', { method: 'POST', body: payload });
          mid = r.measurement_id;
          if (r.queued) {
            toast(`Saved on this phone: ${smartNum(payload.measured_qty)} ${unit}. It will be recorded as soon as there is network.`, 'warn', 6000);
          } else {
            toast(`Recorded ${smartNum(r.measured_qty)} ${unit} · item total ${smartNum(r.item_total_qty)} (tendered ${smartNum(r.tendered_qty)})`,
              Math.abs(r.deviation) > 1e-6 ? 'warn' : 'ok', 5000);
          }
        }
        const ph = document.getElementById('ms-photo');
        if (ph && ph.files && ph.files[0]) {
          if (!mid) { toast('Photo kept on the phone — attach it after the reading syncs', 'warn', 6000); }
          else {
            const fd = new FormData(); fd.append('file', ph.files[0]); fd.append('caption', 'Site evidence');
            await api(`/api/measurements/${mid}/photos`, { method: 'POST', form: fd });
          }
        }
        closeSheet(); render();
      } catch (e) { toast(e.message, 'bad'); }
    }); }
  })();
}

function extraItemSheet() {
  sheet({
    title: 'Add extra / additional item from Master CSR',
    body: `
      <div class="notice info">${ICON.sparkle}<div>Type what was actually installed (e.g. <b>exhaust fan</b>, <b>chemical earthing</b>, <b>LED street light</b>). The app attaches the correct item code, legal description, unit and rate from the Master CSR — no manual rate entry.</div></div>
      <div style="margin:12px 0"><input class="i" id="xi-q" placeholder="Search the Master CSR…" autofocus></div>
      <div id="xi-res"><div class="muted small">Start typing to search ${esc((S.csr.fy || '2024-25'))} · ${esc(S.csr.region || 'Pune')} region.</div></div>
      <div class="hr"></div>
      <details><summary class="small muted">Item not in the Schedule of Rates? Enter a non-schedule item</summary>
        <div class="grid g2" style="margin-top:10px">
          <div style="grid-column:1/-1"><label class="f">Non-schedule description</label><input class="i" id="ns-desc" placeholder="Providing and fixing ... as approved by competent authority"></div>
          <div><label class="f">Unit</label><input class="i" id="ns-unit" value="Each"></div>
          <div><label class="f">Rate (₹)</label><input class="i" id="ns-rate" type="number" step="0.01"></div>
          <div style="grid-column:1/-1"><label class="f">Reason / approval reference</label><input class="i" id="ns-reason" placeholder="Not covered in CSR — approved vide letter No ..."></div>
          <div><label class="f">Quantity</label><input class="i" id="ns-qty" type="number" step="0.001" value="1"></div>
          <div style="display:flex;align-items:flex-end"><button class="btn pri block" data-act="add-ns">Add non-schedule item</button></div>
        </div>
      </details>`,
  });
  const run = debounce(async () => {
    const q = document.getElementById('xi-q').value.trim();
    const box = document.getElementById('xi-res');
    if (q.length < 2) { box.innerHTML = '<div class="muted small">Type at least 2 characters.</div>'; return; }
    box.innerHTML = loading('Searching Master CSR…');
    try {
      const r = await api(`/api/csr/suggest?q=${encodeURIComponent(q)}&fy=${S.csr.fy}&region=${S.csr.region}`);
      box.innerHTML = (r.results || []).length ? r.results.map((it) => `
        <div class="item-card" style="margin-bottom:8px">
          <div class="row between"><span class="pill-code">${esc(it.item_code)}</span>
            <span class="badge brand">₹ ${inr(it.rate)} / ${esc(it.unit)}</span></div>
          <div class="small">${esc(it.description)}</div>
          <div class="row">${(it.tags || []).map((t) => `<span class="badge tiny">${esc(t)}</span>`).join('')}
            <div style="flex:1"></div>
            <input class="i" style="max-width:110px" type="number" step="0.001" placeholder="qty" id="xq-${it.id}">
            <button class="btn pri sm" data-act="add-extra" data-id="${it.id}">${ICON.plus} Add to project</button></div>
        </div>`).join('') : '<div class="muted small">No CSR item matched. Use the non-schedule entry below.</div>';
    } catch (e) { box.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
  }, 350);
  document.getElementById('xi-q').addEventListener('input', run);
}

/* ------------------------------------------------------------------- admin */
async function viewAdmin(view) {
  setTitle('Admin Control', 'Master CSR database, users, audit trail and parsing jobs');
  const [o, jobs] = await Promise.all([api('/api/admin/overview'), api('/api/admin/parse-jobs').catch(() => ({ jobs: [] }))]);
  const st = o.stats;
  view.innerHTML = `
    <div class="grid g4" style="margin-bottom:14px">
      <div class="card kpi"><div class="lbl">Master CSR rows</div><div class="val">${inr(st.master_rows, 0)}</div>
        <div class="foot">${st.versions} FY × region versions</div></div>
      <div class="card kpi"><div class="lbl">Engineers</div><div class="val">${st.engineer_count}</div>
        <div class="foot">${st.user_count} users total</div></div>
      <div class="card kpi"><div class="lbl">Projects</div><div class="val">${st.project_count}</div>
        <div class="foot">${st.measurement_count} measurement entries</div></div>
      <div class="card kpi"><div class="lbl">Verified value</div><div class="val sm">₹ ${inr(st.measurement_value, 0)}</div>
        <div class="foot">${st.photo_count} site photos</div></div>
    </div>
    <div class="tabs" style="margin-bottom:14px">
      ${[['master', 'Master CSR versions'], ['users', 'Users & roles'], ['audit', 'Audit trail'], ['jobs', 'Parsing jobs'], ['data', 'Data safety']]
      .map(([k, l]) => `<button data-act="atab" data-t="${k}" class="${S.adminTab === k ? 'on' : ''}">${l}</button>`).join('')}
    </div>
    <div id="adminbody"></div>`;
  const b = document.getElementById('adminbody');
  if (S.adminTab === 'data') {
    let bk = { configured: false };
    try { bk = await api('/api/admin/backup'); } catch (e) { bk = { configured: false, error: e.message }; }
    const when = (v) => (v ? new Date(v).toLocaleString() : 'never');
    b.innerHTML = `
      <div class="card" style="margin-bottom:14px">
        <div class="hd">${ICON.shield}<h3>Is this server's storage permanent?</h3>
          <div style="flex:1"></div>
          <span class="badge ${bk.storage === 'persistent' ? 'ok' : 'warn'}">${bk.storage === 'persistent' ? 'persistent disk attached' : 'no persistent disk'}</span></div>
        <div class="bd stack">
          ${bk.storage === 'persistent'
    ? `<div class="notice ok">${ICON.check}<div>This service has a mounted disk, so works, measurements and photos survive restarts.</div></div>`
    : `<div class="notice warn">${ICON.warn}<div><b>A restart can erase every work on this server.</b>
              Free/plain container hosting keeps the database in the container, so a redeploy, a crash or an idle
              spin-down starts from an empty database — that is how a work "disappears" and an upload then says
              <i>Project not found</i>. Keep the backup below switched on, or attach a persistent disk.</div></div>`}
          <div class="stat-line"><span>Data directory</span><span class="small">${esc(bk.data_dir || '')}</span></div>
          <div class="stat-line"><span>Database size</span><span>${inr(bk.db_bytes || 0, 0)} bytes</span></div>
        </div>
      </div>
      <div class="card">
        <div class="hd">${ICON.up}<h3>Backups &amp; restore</h3><div style="flex:1"></div>
          <span class="badge ${bk.configured ? 'ok' : ''}">${bk.configured ? 'automatic backup on' : 'automatic backup off'}</span></div>
        <div class="bd stack">
          ${bk.configured
    ? `<div class="notice ok">${ICON.check}<div>A copy of the database, uploaded estimates and site photos is pushed to
                <b>${esc(bk.repo)}</b> at most every ${bk.interval}s while people are working, and is put back automatically
                when the server comes up empty.</div></div>`
    : `<div class="notice info">${ICON.shield}<div>Automatic backup is off. Set <code class="kbd">SMARTMB_BACKUP_REPO</code> and
                <code class="kbd">SMARTMB_BACKUP_TOKEN</code> (a fine-grained token with contents:write on a private repo) and
                the platform keeps itself safe — or use the buttons below to keep a copy by hand.</div></div>`}
          <div class="stat-line"><span>Last backup pushed</span><span>${when(bk.last_push)} ${bk.last_size ? '· ' + inr(bk.last_size, 0) + ' bytes' : ''}</span></div>
          <div class="stat-line"><span>Last restore</span><span>${when(bk.last_restore)}</span></div>
          ${bk.last_error ? `<div class="notice bad">${ICON.warn}<div>${esc(bk.last_error)}</div></div>` : ''}
          <div class="row wrap-gap">
            <button class="btn pri" data-act="bk-now">Back up now</button>
            <button class="btn" data-act="bk-download">Download a copy</button>
            <button class="btn" data-act="bk-restore-repo" ${bk.configured ? '' : 'disabled'}>Restore latest from backup</button>
          </div>
          <div class="hr"></div>
          <label class="f">Restore from a snapshot file (.tar.gz)</label>
          <input class="i" type="file" id="bk-file" accept=".gz,.tgz,application/gzip">
          <button class="btn" data-act="bk-restore-file">Restore this file</button>
        </div>
      </div>`;
    return;
  }
  if (S.adminTab === 'master') {
    b.innerHTML = `
      <div class="card">
        <div class="hd">${ICON.book}<h3>CSR versions in the Master Database</h3><div style="flex:1"></div>
          <button class="btn sm" data-act="reseed-master">Rebuild demo master data</button></div>
        <div class="bd tight scrollx">
          <table class="tbl"><thead><tr><th>FY</th><th>Region</th><th class="num">Items</th><th>Status</th><th>Source file</th><th>Uploaded</th><th></th></tr></thead>
          <tbody>${st.items_per_version.map((v) => `<tr>
            <td><b>${esc(v.fy)}</b></td><td>${esc(v.region)}</td><td class="num">${v.item_count}</td>
            <td>${badgeFor(v.status)}</td><td class="small muted">${esc(v.source_file || '—')}</td>
            <td class="small">${dtt(v.uploaded_at)}</td>
            <td class="nowrap"><button class="btn sm" data-act="dl" data-path="/api/csr/export?fy=${encodeURIComponent(v.fy)}&region=${encodeURIComponent(v.region)}" data-name="MasterCSR_${esc(v.fy)}_${esc(v.region)}.xlsx">Export</button>
              <button class="btn sm danger" data-act="del-version" data-fy="${esc(v.fy)}" data-region="${esc(v.region)}">Delete</button></td></tr>`).join('')}</tbody></table>
        </div>
        <div class="bd small muted">Upload a new financial year from the <a href="#/csr">Master CSR</a> screen (Super Admin panel at the top). Imports are two-step: preview &amp; validate, then commit. Existing item codes are updated, new codes are inserted, malformed rows are reported with the row number.</div>
      </div>`;
  } else if (S.adminTab === 'users') {
    b.innerHTML = `
      <div class="card" style="margin-bottom:12px">
        <div class="hd">${ICON.shield}<h3>Create user</h3></div>
        <div class="bd">
          <div class="grid g4">
            <div><label class="f">Name</label><input class="i" id="u-name" placeholder="Er. ..."></div>
            <div><label class="f">Email</label><input class="i" id="u-email" placeholder="je.*@pwd.maharashtra.gov.in"></div>
            <div><label class="f">Role</label><select class="i" id="u-role"><option value="engineer">Site Engineer</option><option value="admin">Super Admin</option></select></div>
            <div><label class="f">Designation</label><input class="i" id="u-desig" value="Junior Engineer (Electrical)"></div>
            <div><label class="f">Division</label><input class="i" id="u-div" placeholder="PWD Electrical Sub-Division, ..."></div>
            <div><label class="f">Circle</label><input class="i" id="u-circle" placeholder="... Circle"></div>
            <div><label class="f">Region</label><select class="i" id="u-region">${['Pune', 'Mumbai', 'Nagpur', 'Nashik', 'Chhatrapati Sambhajinagar', 'Konkan', 'Amravati'].map((r) => `<option>${r}</option>`).join('')}</select></div>
            <div><label class="f">Temp. password</label><input class="i" id="u-pass" value="Welcome@123"></div>
          </div>
          <div class="row" style="margin-top:12px"><button class="btn pri" data-act="create-user">${ICON.plus} Create account</button></div>
        </div>
      </div>
      <div class="card">
        <div class="hd"><h3>Users (${o.users.length})</h3></div>
        <div class="bd tight scrollx">
          <table class="tbl"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Division / circle</th><th>Region</th><th>Last login</th><th>Status</th><th></th></tr></thead>
          <tbody>${o.users.map((u) => `<tr>
            <td><b>${esc(u.name)}</b><div class="tiny muted">${esc(u.designation || '')}</div></td>
            <td class="small">${esc(u.email)}</td>
            <td>${u.role === 'admin' ? '<span class="badge brand">Super Admin</span>' : '<span class="badge">Site Engineer</span>'}</td>
            <td class="small">${esc(u.division || '—')}<div class="tiny muted">${esc(u.circle || '')}</div></td>
            <td class="small">${esc(u.region || '—')}</td>
            <td class="small">${u.last_login ? dtt(u.last_login) : '—'}</td>
            <td>${u.is_active ? '<span class="badge ok">Active</span>' : '<span class="badge bad">Disabled</span>'}</td>
            <td class="nowrap"><button class="btn sm" data-act="toggle-user" data-id="${u.id}" data-v="${u.is_active ? 0 : 1}">${u.is_active ? 'Disable' : 'Enable'}</button>
              <button class="btn sm" data-act="reset-pass" data-id="${u.id}">Reset password</button>
              <button class="btn sm danger" data-act="del-user" data-id="${u.id}" data-name="${esc(u.name)}">Delete</button></td>
          </tr>`).join('')}</tbody></table>
        </div>
      </div>`;
  } else if (S.adminTab === 'audit') {
    b.innerHTML = `
      <div class="card">
        <div class="hd">${ICON.doc}<h3>Audit trail — every master change, import and measurement</h3></div>
        <div class="bd tight scrollx">
          <table class="tbl"><thead><tr><th>When</th><th>User</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
          <tbody>${o.audit.map((a) => `<tr>
            <td class="small nowrap">${dtt(a.created_at)}</td>
            <td class="small">${esc(a.user_name)}</td>
            <td><span class="badge info">${esc(a.action)}</span></td>
            <td class="small mono">${esc(a.entity)} ${a.entity_id ? '#' + esc(a.entity_id) : ''}</td>
            <td class="tiny muted">${esc((a.detail || '').slice(0, 130))}</td></tr>`).join('')}</tbody></table>
        </div>
      </div>`;
  } else {
    b.innerHTML = `
      <div class="card">
        <div class="hd">${ICON.doc}<h3>Parsing jobs</h3></div>
        <div class="bd tight scrollx">
          <table class="tbl"><thead><tr><th>When</th><th>Project</th><th>File</th><th>Engine</th><th class="num">Anchors</th><th class="num">Matched</th><th class="num">Unknown</th></tr></thead>
          <tbody>${(jobs.jobs || []).map((j) => `<tr><td class="small">${dtt(j.created_at)}</td>
            <td class="small">${esc(j.project_code || '—')}</td><td class="small">${esc(j.filename)}</td>
            <td class="small">${esc(j.engine)}</td><td class="num">${j.anchors || 0}</td>
            <td class="num">${j.matched || 0}</td><td class="num">${j.unknown || 0}</td></tr>`).join('')
      || '<tr><td colspan="7" class="empty">No parsing jobs yet.</td></tr>'}</tbody></table>
        </div>
      </div>`;
  }
}

/* --------------------------------------------------------- global handlers */
document.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  const act = t.dataset.act;
  const stop = () => { e.preventDefault(); e.stopPropagation(); };

  if (act === 'close-sheet') { closeSheet(); return; }
  if (act === 'outbox-sync') { syncNow(); return; }
  if (act === 'bk-now') {
    const btn = t; btn.disabled = true; btn.textContent = 'Backing up…';
    try { const r = await api('/api/admin/backup/now', { method: 'POST' }); toast(`Backup pushed (${inr(r.bytes || 0, 0)} bytes)`, 'ok'); }
    catch (e) { toast(e.message, 'bad'); }
    btn.disabled = false; btn.textContent = 'Back up now';
    render(); return;
  }
  if (act === 'bk-download') {
    try { await download('/api/admin/backup/download', 'smart-mb-data.tar.gz'); }
    catch (e) { toast('Download failed — ' + e.message, 'bad'); }
    return;
  }
  if (act === 'bk-restore-repo') {
    if (!confirm('Replace the data on this server with the latest backup?')) return;
    const btn = t; btn.disabled = true; btn.textContent = 'Restoring…';
    try { const r = await api('/api/admin/backup/restore', { method: 'POST', form: (() => { const f = new FormData(); f.append('source', 'repo'); return f; })() });
      toast(`Restored ${r.db ? 'the database' : 'files'} — reloading`, 'ok'); setTimeout(() => location.reload(), 1200); }
    catch (e) { toast(e.message, 'bad'); btn.disabled = false; btn.textContent = 'Restore latest from backup'; }
    return;
  }
  if (act === 'bk-restore-file') {
    const f = document.getElementById('bk-file');
    if (!f || !f.files[0]) { toast('Choose a .tar.gz snapshot first', 'bad'); return; }
    if (!confirm('Replace the data on this server with this snapshot?')) return;
    const fd = new FormData(); fd.append('file', f.files[0]); fd.append('source', 'file');
    try { await api('/api/admin/backup/restore', { method: 'POST', form: fd }); toast('Restored — reloading', 'ok'); setTimeout(() => location.reload(), 1200); }
    catch (e) { toast(e.message, 'bad'); }
    return;
  }
  if (act === 'jump-estimate') { (document.getElementById('card-estimate') || document.body).scrollIntoView({ block: 'start' }); const f = document.getElementById('est-file'); if (f) f.focus(); return; }
  if (act === 'jump-schedule') {
    const pr = S.proj.data && S.proj.data.project;
    const narrow = window.matchMedia('(max-width: 760px)').matches;
    if (narrow && pr) {
      /* on a phone, bring the uploader to the engineer instead of making them scroll */
      sheet({ title: 'Descriptive schedule', subtitle: 'Upload the PDF/Excel, paste the table, or load the sample',
        body: svImportHTML(pr, true), body_id: 'sv-sheet-body', sticky: true,
        onOpen: (el) => {
          const host = el.querySelector('#sv-sheet-body');
          svWireImport(host, pr, () => { closeSheet(); S.verify.section = 'rooms'; S.proj.tab = 'verify'; render(); });
        } });
      return;
    }
    const c = document.getElementById('card-schedule');
    if (c) c.scrollIntoView({ block: 'start' });
    const f = document.getElementById('sv-file'); if (f) f.focus();
    return;
  }
  if (act === 'goto-projects') { location.hash = '#/projects'; render(); return; }
  if (act === 'bk-hint') {
    toast('Ask the admin to open Admin Control → Data safety: switch on automatic backup, or attach a persistent disk.', 'warn', 9000);
    return;
  }
  if (act === 'recreate-project') {
    S.newProjectPrefill = (S.proj.data && S.proj.data.project) ? S.proj.data.project : null;
    location.hash = '#/projects'; render();
    setTimeout(() => document.querySelector('[data-act="new-project"]')?.click(), 250);
    return;
  }
  if (act === 'goto-schedule') {
    const pr = S.proj && S.proj.project;
    if (S.proj) S.proj.tab = 'import';
    if (pr) { location.hash = '#/project/' + pr.id; }
    render();
    setTimeout(() => {
      const host = document.getElementById('import-schedule');
      if (host) { host.scrollIntoView({ block: 'center' }); const fi = host.querySelector('#sv-file'); if (fi) fi.focus(); }
    }, 150);
    return;
  }
  if (act === 'goto-verify') { if (S.proj) S.proj.tab = 'verify'; S.verify.section = 'rooms'; render(); return; }
  if (act === 'logout') { logout(); return; }
  if (act === 'demo') { enterDemo(); return; }
  if (act === 'fill') {
    document.getElementById('l-email').value = t.dataset.email;
    document.getElementById('l-pass').value = t.dataset.pass;
    return;
  }
  if (act === 'login') {
    const email = document.getElementById('l-email').value.trim();
    const password = document.getElementById('l-pass').value;
    const btn = document.getElementById('l-btn');
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Signing in…';
    try {
      const r = await api('/api/auth/login', { method: 'POST', body: { email, password } });
      S.token = r.token; S.user = r.user; S.demo = false;
      localStorage.setItem('smartmb_token', r.token);
      localStorage.setItem('smartmb_user', JSON.stringify(r.user));
      S.csr.region = r.user.region || 'Pune';
      toast(`Welcome, ${r.user.name}`, 'ok');
      location.hash = '#/dashboard'; render();
    } catch (err) {
      document.getElementById('login-err').innerHTML = `<div class="notice bad">${ICON.warn}<div>${esc(err.message)}</div></div>`;
      btn.disabled = false; btn.textContent = 'Sign in';
      if (err.offline && DEMO) {
        document.getElementById('login-err').insertAdjacentHTML('beforeend',
          `<button class="btn block" data-act="demo" style="margin-top:8px">${ICON.sparkle} Explore offline demo instead</button>`);
      }
    }
    return;
  }
  if (act === 'help') {
    sheet({
      title: 'How Smart-MB works', body: `
      <div class="small stack">
        <div><b>1 · Master CSR (admin)</b><div class="muted">The full Electrical Schedule of Rates lives in the Master Database with item codes, legal descriptions, units and rates, tagged with metadata (Fan, Earthing, LED…).</div></div>
        <div><b>2 · Project estimate (engineer)</b><div class="muted">Upload the Technical Sanction estimate. Anchor-based parsing finds the item codes and quantities.</div></div>
        <div><b>3 · Smart Map</b><div class="muted">Codes are resolved against the Master CSR — the description/unit/rate always come from the database, so OCR noise never reaches the MB.</div></div>
        <div><b>4 · Joint measurement</b><div class="muted">Room-wise checklist, dimension entry (No · L · B · H), photo evidence, extra items pulled straight from the CSR.</div></div>
        <div><b>5 · Form-23 MB</b><div class="muted">Official MB PDF/Excel with deviation statement (excess/savings valued at CSR rates) and signature blocks.</div></div>
        <div class="hr"></div>
        <div class="muted tiny">Smart-MB · Maharashtra PWD Electrical · demo build</div>
      </div>`});
    return;
  }
  if (act === 'open-project') { location.hash = '#/project/' + t.dataset.id; return; }
  if (act === 'ptab') { S.proj.tab = t.dataset.t; render(); return; }
  if (act === 'atab') { S.adminTab = t.dataset.t; render(); return; }
  if (act === 'room') { S.proj.room = t.dataset.id ? Number(t.dataset.id) : null; render(); return; }
  if (act === 'add-room') {
    sheet({
      title: 'Add measurement location', body: `
        <label class="f">Floor / level</label><input class="i" id="r-floor" placeholder="Ground Floor">
        <div style="height:10px"></div>
        <label class="f">Room / location name</label><input class="i" id="r-name" placeholder="Class Room 1">`,
      footer: `<button class="btn" data-act="close-sheet">Cancel</button><button class="btn pri" id="r-save">Add room</button>`,
    });
    document.getElementById('r-save').addEventListener('click', async () => {
      const name = document.getElementById('r-name').value.trim();
      if (!name) { toast('Enter the room name', 'bad'); return; }
      try {
        await api(`/api/projects/${S.proj.id}/rooms`, { method: 'POST', body: { name, floor: document.getElementById('r-floor').value } });
        closeSheet(); toast('Room added', 'ok'); render();
      } catch (err) { toast(err.message, 'bad'); }
    });
    return;
  }
  if (act === 'measure') { measureSheet(Number(t.dataset.id)); return; }
  if (act === 'extra-item') { extraItemSheet(); return; }
  if (act === 'add-extra') {
    const id = Number(t.dataset.id);
    const qtyEl = document.getElementById('xq-' + id);
    const qty = Number((qtyEl && qtyEl.value) || 0);
    if (!qty) { toast('Enter the quantity actually executed', 'bad'); return; }
    try {
      const r = await api(`/api/projects/${S.proj.id}/items`, { method: 'POST', body: { master_item_id: id, tendered_qty: qty, source: 'extra' } });
      closeSheet();
      toast(`Added ${r.item_code} — ${r.description.slice(0, 44)}… @ ₹ ${inr(r.rate)}/${r.unit}`, 'ok', 5000);
      S.proj.tab = 'checklist'; render();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'add-ns') {
    const g = (x) => (document.getElementById(x) || {}).value || '';
    if (!g('ns-desc').trim()) { toast('Enter the non-schedule description', 'bad'); return; }
    try {
      const r = await api(`/api/projects/${S.proj.id}/items`, {
        method: 'POST', body: {
          description: g('ns-desc'), unit: g('ns-unit'), rate: Number(g('ns-rate') || 0),
          tendered_qty: Number(g('ns-qty') || 0), is_non_schedule: true, ns_reason: g('ns-reason'), source: 'manual',
        }
      });
      closeSheet(); toast(`Non-schedule item ${r.item_code} added`, 'ok'); S.proj.tab = 'checklist'; render();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'del-project') {
    const p = S.proj.data && S.proj.data.project;
    if (!confirm(`Delete project "${(p && p.name) || ''}" and all its measurements? This cannot be undone.`)) return;
    try {
      await api(`/api/projects/${t.dataset.id}`, { method: 'DELETE' });
      toast('Project deleted', 'ok');
      S.proj.id = null; S.proj.tab = 'overview';
      location.hash = '#/projects'; render();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'del-meas') {
    if (!confirm('Delete this measurement entry?')) return;
    try { await api(`/api/measurements/${t.dataset.id}`, { method: 'DELETE' }); toast('Deleted', 'ok'); render(); }
    catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'edit-meas') {
    const { measurements } = await api(`/api/projects/${S.proj.id}/measurements`);
    const row = measurements.find((m) => m.id === Number(t.dataset.id));
    if (row) measureSheet(row.project_item_id, row);
    return;
  }
  if (act === 'photo') {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = 'image/*'; input.capture = 'environment';
    input.onchange = async () => {
      if (!input.files[0]) return;
      const fd = new FormData(); fd.append('file', input.files[0]); fd.append('caption', 'Site evidence');
      try { await api(`/api/measurements/${t.dataset.id}/photos`, { method: 'POST', form: fd }); toast('Photo attached to the measurement record', 'ok'); render(); }
      catch (err) { toast(err.message, 'bad'); }
    };
    input.click();
    return;
  }
  if (act === 'gallery') {
    const mid = t.dataset.id;
    const { photos } = await api(`/api/measurements/${mid}/photos`);
    const auth = S.token ? '?token=' + encodeURIComponent(S.token) : '';
    sheet({
      title: `${photos.length} site photo(s) — measurement evidence`,
      wide: true,
      body: photos.length ? `<div class="grid g-auto">${photos.map((p) => `
        <figure style="margin:0">
          <img src="${esc(p.url)}${auth}" alt="${esc(p.caption || 'site photo')}" style="width:100%;border-radius:12px;border:1px solid var(--line)">
          <figcaption class="tiny muted" style="margin-top:6px">${esc(p.caption || '')} · ${dtt(p.uploaded_at)}</figcaption>
        </figure>`).join('')}</div>` : '<div class="muted small">No photos attached.</div>',
    });
    return;
  }
  if (act === 'gen') {
    const fmt = t.dataset.fmt;
    const mb = (S.proj.data && S.proj.data.project && S.proj.data.project.mb_no) || S.proj.id;
    await download(`/api/projects/${S.proj.id}/form23.${fmt}`, `Form23_MB_${mb}.${fmt}`);
    return;
  }
  if (act === 'dl') {
    await download(t.dataset.path, t.dataset.name || 'download');
    return;
  }
  if (act === 'new-project') { newProjectSheet(); return; }
  if (act === 'csr-cat') { S.csr.category = t.dataset.v; S.csr.page = 0; render(); return; }
  if (act === 'csr-page') { S.csr.page = Math.max(0, Number(t.dataset.p)); render(); return; }
  if (act === 'csr-item') { csrItemSheet(t.dataset.id); return; }
  if (act === 'save-tags') {
    try {
      const tags = document.getElementById('tag-input').value.split(',').map((s) => s.trim()).filter(Boolean);
      await api(`/api/csr/items/${t.dataset.id}/tags`, { method: 'POST', body: tags });
      toast('Metadata tags saved — they drive the Extra-Item search', 'ok'); closeSheet();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'csr-export') {
    await download(`/api/csr/export?fy=${encodeURIComponent(S.csr.fy)}&region=${encodeURIComponent(S.csr.region)}`,
      `MasterCSR_${S.csr.fy}_${S.csr.region}.xlsx`);
    return;
  }
  if (act === 'csr-template') { await download('/api/samples/MasterCSR_import_template.xlsx', 'MasterCSR_import_template.xlsx'); return; }
  if (act === 'csr-preview' || act === 'csr-commit') {
    const file = document.getElementById('imp-file');
    if (!file.files[0]) { toast('Choose the CSR Excel/CSV file first', 'bad'); return; }
    const fd = new FormData();
    fd.append('file', file.files[0]);
    fd.append('fy', document.getElementById('imp-fy').value);
    fd.append('region', document.getElementById('imp-region').value);
    fd.append('mode', document.getElementById('imp-mode').value);
    fd.append('commit', act === 'csr-commit' ? 'true' : 'false');
    const box = document.getElementById('imp-result');
    box.innerHTML = loading(act === 'csr-commit' ? 'Importing into the Master Database…' : 'Validating file…');
    try {
      const r = await api('/api/csr/import', { method: 'POST', form: fd });
      if (!r.ok) {
        box.innerHTML = `<div class="notice bad">${ICON.warn}<div><b>${esc(r.error)}</b><br>Detected columns: ${esc(JSON.stringify(r.columns_detected || r.columns || {}))}</div></div>`;
        return;
      }
      box.innerHTML = `
        <div class="notice ${r.committed ? 'ok' : 'info'}">${ICON.check}<div>
          ${r.committed ? '<b>Committed to the Master Database.</b><br>' : '<b>Validation preview (nothing written yet).</b><br>'}
          ${r.item_count} valid rows parsed · <b>${r.new}</b> new codes · <b>${r.updates}</b> existing codes updated
          ${r.error_count ? ` · <b>${r.error_count}</b> row(s) rejected` : ''}
          <div class="tiny">Columns detected: ${esc(Object.entries(r.columns_detected || {}).map(([k, v]) => `${k}=col${v + 1}`).join(' · '))}</div>
        </div></div>
        ${r.errors && r.errors.length ? `<div class="notice warn" style="margin-top:8px">${ICON.warn}<div>${r.errors.slice(0, 6).map((x) => `row ${x.row}: ${esc(x.reason)}`).join('<br>')}</div></div>` : ''}
        <div class="scrollx" style="max-height:320px;overflow:auto;margin-top:10px">
          <table class="tbl"><thead><tr><th>Item code</th><th>Description</th><th>Unit</th><th class="num">Rate</th><th>Category</th></tr></thead>
          <tbody>${r.items.slice(0, 60).map((i) => `<tr><td><span class="pill-code">${esc(i.item_code)}</span></td>
            <td class="small">${esc((i.description || '').slice(0, 90))}</td><td>${esc(i.unit)}</td>
            <td class="num">${inr(i.rate)}</td><td class="small muted">${esc(i.category || '')}</td></tr>`).join('')}</tbody></table>
        </div>`;
      document.getElementById('imp-commit').disabled = r.committed;
      if (r.committed) toast(`Master CSR updated — ${r.live_count} live items for ${r.fy} ${r.region}`, 'ok');
    } catch (err) { box.innerHTML = `<div class="notice bad">${ICON.warn}<div>${esc(err.message)}</div></div>`; }
    return;
  }
  if (act === 'reseed-master') {
    if (!confirm('Rebuild the demo master data (2 FY × 7 regions)? This resets the CSR tables.')) return;
    try { const r = await api('/api/admin/reseed?master=true', { method: 'POST' }); toast(`Master rebuilt: ${r.master.rows} rows`, 'ok'); render(); }
    catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'create-user') {
    const g = (x) => (document.getElementById(x) || {}).value || '';
    try {
      const r = await api('/api/admin/users', {
        method: 'POST', body: {
          name: g('u-name'), email: g('u-email'), role: g('u-role'), designation: g('u-desig'),
          division: g('u-div'), circle: g('u-circle'), region: g('u-region'), password: g('u-pass'),
        }
      });
      toast(`Account created. Temporary password: ${r.default_password}`, 'ok', 6000); render();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'toggle-user') {
    try { await api(`/api/admin/users/${t.dataset.id}`, { method: 'PATCH', body: { is_active: Number(t.dataset.v) } }); toast('User updated', 'ok'); render(); }
    catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'del-version') {
    const { fy, region } = t.dataset;
    if (!confirm(`Delete the entire Master CSR version ${fy} · ${region}?\nProjects that reference it will keep their imported rows but can no longer resolve new items from it.`)) return;
    try {
      await api(`/api/csr/versions?fy=${encodeURIComponent(fy)}&region=${encodeURIComponent(region)}`, { method: 'DELETE' });
      toast(`CSR version ${fy} · ${region} deleted`, 'ok'); render();
    } catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'del-user') {
    if (!confirm(`Delete the account of ${t.dataset.name}?`)) return;
    try { await api(`/api/admin/users/${t.dataset.id}`, { method: 'DELETE' }); toast('User deleted', 'ok'); render(); }
    catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'reset-pass') {
    const pwd = prompt('New temporary password for this user:', 'Welcome@123');
    if (!pwd) return;
    try { await api(`/api/admin/users/${t.dataset.id}`, { method: 'PATCH', body: { password: pwd } }); toast('Password reset', 'ok'); }
    catch (err) { toast(err.message, 'bad'); }
    return;
  }
  if (act === 'show-prompt') {
    const sample = S.importPreview && S.importPreview.ai_prompt ? S.importPreview.ai_prompt
      : `You are an Estimate Mapper.\n\nInput 1: A raw text stream from a scanned PWD Estimate PDF.\nInput 2: A list of valid Item Codes from our Master Database (e.g. ['1-1-1','1-1-2', ...]).\n\nTask:\n1. Identify every valid Item Code in the PDF text.\n2. Extract the 'Quantity' associated with that code.\n3. Return JSON: [{"code":"1-1-2","pdf_qty":50}]\n4. If a code is found in the PDF but NOT in the Master List, flag it as 'Unknown_Item'.`;
    sheet({ title: 'Parsing engine prompt', wide: true, body: `<pre class="mono tiny" style="white-space:pre-wrap;background:#0f172a;color:#cbd5e1;padding:14px;border-radius:12px">${esc(sample)}</pre>
      <div class="notice info" style="margin-top:10px">${ICON.sparkle}<div>The production engine is deterministic (regex + column heuristics). This prompt is kept for the optional LLM fallback mode — plug in an API key on the server to enable it for pathological scans.</div></div>` });
    return;
  }
  if (act === 'sample-file') { return; }
  if (stop) stop();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeSheet();
});

window.addEventListener('hashchange', () => { if (S.token || S.demo) render(); else renderLogin(); });
window.addEventListener('load', () => setTimeout(checkBuildAndStorage, 800));
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkBuildAndStorage(); });
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-act="do-reload"]')) location.reload();
});
window.addEventListener('online', () => { paintOutboxBadge(); if (S.outbox.length) outboxFlush(); });
window.addEventListener('offline', () => paintOutboxBadge());
setInterval(() => { if (S.outbox.length && isOnline()) outboxFlush(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && S.outbox.length) outboxFlush(); });

(async function boot() {
  if (!S.token && !S.demo) { renderLogin(); return; }
  if (S.token) {
    try {
      const r = await api('/api/auth/me');
      S.user = r.user;
      S.csr.region = r.user.region || 'Pune';
    } catch (e) {
      if (e.offline && DEMO) { S.token = ''; renderLogin(); return; }
      S.token = ''; localStorage.removeItem('smartmb_token');
      renderLogin(); return;
    }
  }
  if (!location.hash) location.hash = '#/dashboard';
  render();
})();

/* ============================================================================
   SITE VERIFY - descriptive schedule reconciliation
   The descriptive schedule lists every room / floor of the building with its own
   quantities.  The engineer walks room by room: quantities that match the schedule
   are kept with one tap; only the ones that differ need the actual figure.
   ========================================================================== */
function svChip(cell) {
  if (cell.queued) return `<span class="badge warn">⏳ saved on this phone — not synced yet</span>`;
  if (cell.verify_status === 'kept') return `<span class="badge ok">✓ as per schedule</span>`;
  if (cell.verify_status === 'changed') return `<span class="badge warn">changed → ${smartNum(cell.actual_qty)}</span>`;
  if (cell.verify_status === 'not_applicable') return `<span class="badge">n/a</span>`;
  return `<span class="badge">pending</span>`;
}

async function tabVerify(body, p, wl) {
  const pr = p.project;
  if (!S.verify.docId && wl && wl.docs && wl.docs.length) S.verify.docId = wl.docs[0].id;
  const q = S.verify.docId ? `?doc_id=${S.verify.docId}` : '';
  wl = await api(`/api/projects/${pr.id}/verify${q}`).catch(() => wl);
  if (!wl || !wl.docs || !wl.docs.length) return svImportPanel(body, pr);
  const t = wl.totals || { cells: 0, pending: 0, kept: 0, changed: 0, progress_pct: 0 };
  const rec = await api(`/api/projects/${pr.id}/reconciliation${q}`).catch(() => null);
  const sec = S.verify.section;
  body.innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <div class="bd">
        <div class="row between" style="align-items:flex-start">
          <div>
            <div class="row"><b style="font-size:15px">Descriptive schedule · room-wise verification</b>
              <span class="badge brand">${esc(wl.docs[0].filename || 'schedule')}</span>
              <span class="badge">${esc(wl.docs[0].engine || '')}</span></div>
            <div class="small muted" style="margin-top:5px">${esc((wl.docs[0].name_of_work || '').slice(0, 140))}</div>
          </div>
          <div class="row">
            <button class="btn pri" data-act="sv-import-again">${ICON.up} Upload descriptive schedule</button>
            ${wl.docs.length > 1 ? `<select class="i" id="sv-doc" style="max-width:220px">${wl.docs.map((d) => `<option value="${d.id}" ${d.id === S.verify.docId ? 'selected' : ''}>${esc(d.filename)} · ${d.checked}/${d.cells}</option>`).join('')}</select>` : ''}
          </div>
        </div>
        <div class="grid g4" style="margin-top:14px">
          <div class="kpi" style="padding:8px 0"><div class="lbl">Rooms / locations</div><div class="val sm">${t.locations || wl.rooms.length}</div><div class="foot">from the descriptive schedule</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Verified</div><div class="val sm">${t.kept + t.changed} <span class="muted" style="font-size:14px">/ ${t.cells}</span></div><div class="foot">${t.kept} as per schedule · ${t.changed} changed to actual</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Pending</div><div class="val sm" style="color:${t.pending ? 'var(--warn)' : 'var(--ok)'}">${t.pending}</div><div class="foot">quantities still to be seen at site</div></div>
          <div class="kpi" style="padding:8px 0"><div class="lbl">Schedule value</div><div class="val sm">₹ ${inr((rec && rec.totals.schedule_amount) || 0, 0)}</div><div class="foot">at Master CSR rates</div></div>
        </div>
        <div class="progress ${t.progress_pct < 100 ? 'warn' : ''}" style="margin-top:12px"><i style="width:${Math.min(100, t.progress_pct)}%"></i></div>
      </div>
      <div class="bd tight" style="padding:10px 12px">
        <div class="tabs" id="svtabs">
          ${[['rooms', 'Rooms'], ['recon', 'Estimate vs Schedule'], ['docs', 'Schedule files']]
      .map(([k, l]) => `<button data-act="sv-sec" data-t="${k}" class="${sec === k ? 'on' : ''}">${l}</button>`).join('')}
        </div>
      </div>
    </div>
    <div id="sv-body"></div>`;
  const sb = document.getElementById('sv-body');
  body.querySelectorAll('[data-act="sv-sec"]').forEach((b) => b.addEventListener('click', () => {
    S.verify.section = b.dataset.t; tabVerify(body, p, wl);
  }));
  body.querySelector('[data-act="sv-import-again"]')?.addEventListener('click', () => svImportPanel(body, pr));
  const sel = body.querySelector('#sv-doc');
  if (sel) {
    sel.value = S.verify.docId;
    sel.addEventListener('change', () => { S.verify.docId = Number(sel.value); tabVerify(body, p, wl); });
  }
  if (sec === 'rooms') svRenderRooms(sb, pr, wl);
  else if (sec === 'recon') svRenderRecon(sb, rec, wl, pr);
  else svRenderDocs(sb, pr, wl);
}

/* ------------------------------------------------------- import & preview */
function svImportHTML(pr, embedded) {
  return `
    <div class="grid g2" style="align-items:start">
      <div class="card">
        <div class="hd">${ICON.ruler}<h3>Upload the descriptive schedule</h3></div>
        <div class="bd stack">
          <div class="notice info">${ICON.shield}<div>The descriptive schedule carries the <b>rooms, floors and location-wise quantities</b> of the whole building.
            It is read as a matrix — <b>columns are work items, rows are rooms</b> — with the rotated column headers recovered from the PDF, and every column is checked against the printed
            <b>GRAND TOTAL</b> before you see it.</div></div>
          <div>
            <label class="f" for="sv-file">Descriptive schedule file</label>
            <input class="i" type="file" id="sv-file" accept=".pdf,.xlsx,.xlsm,.csv,.txt">
            <div class="small muted" style="margin-top:4px">PDF (digital or scanned) · Excel · CSV — the columns can be in any order.</div>
          </div>
          <button class="btn pri block big" data-act="sv-parse">${ICON.up} Upload &amp; read the schedule</button>
          <div class="row"><button class="btn block" data-act="sv-sample">Use the sample schedule (Ahilyabai Holkar Sabhamandap)</button></div>
          <div class="hr"></div>
          <label class="f" for="sv-text">…or paste the schedule table (works for a scanned page)</label>
          <textarea class="i" id="sv-text" style="min-height:96px"
            placeholder="Location	Item	Unit	Qty&#10;HALL	Conduit Light / fan point	Point	9&#10;HALL	LED panel 18W	Nos	27&#10;TOILET	Ex. Fan	Nos	1"></textarea>
          <button class="btn block" data-act="sv-parse-text">${ICON.sparkle} Read pasted table</button>
        </div>
      </div>
      <div class="card">
        <div class="hd"><h3>What happens next</h3></div>
        <div class="bd small stack">
          <div><b>1 · Rooms &amp; floors</b><div class="muted">Every location in the schedule becomes a room of the project (matched to existing rooms by exact name).</div></div>
          <div><b>2 · Item mapping</b><div class="muted">Each schedule column is matched to the estimate item and hence to the Master CSR — description, unit and rate come from the database.</div></div>
          <div><b>3 · Control sheet</b><div class="muted">Estimate quantity vs descriptive schedule quantity vs actual measured, item by item.</div></div>
          <div><b>4 · Room-wise verification</b><div class="muted">At site: <b>keep</b> what matches the schedule, <b>change</b> only what differs. A change writes a measurement, so Form-23 and the deviation statement follow automatically.</div></div>
          ${embedded ? '' : `<div class="hr"></div><div class="muted tiny">Uploaded already? The schedule stays attached to this project — open <b>Site Verify</b> to walk the rooms.</div>`}
        </div>
      </div>
    </div>
    <div id="sv-preview" style="margin-top:14px"></div>`;
}

function svWireImport(scope, pr, onImported) {
  if (!scope || !scope.querySelector) return;
  const f = scope.querySelector('#sv-file');
  const tx = scope.querySelector('#sv-text');
  const go = (src) => svRunParse(pr, src, scope, onImported);
  scope.querySelector('[data-act="sv-parse"]')?.addEventListener('click', async () => {
    if (!f || !f.files || !f.files[0]) { toast('Choose the descriptive schedule file first', 'bad'); return; }
    await go({ file: f.files[0] });
  });
  scope.querySelector('[data-act="sv-parse-text"]')?.addEventListener('click', async () => {
    const text = (tx && tx.value || '').trim();
    if (text.length < 10) { toast('Paste the schedule table first', 'bad'); return; }
    await go({ text });
  });
  scope.querySelector('[data-act="sv-sample"]')?.addEventListener('click', async () => {
    const blob = await fetch('/api/samples/descriptive_schedule_sample.pdf').then((r) => r.blob()).catch(() => null);
    if (!blob) { toast('Sample not reachable — use paste mode instead', 'warn'); return; }
    await go({ file: new File([blob], 'descriptive_schedule_sample.pdf') });
  });
  if (S.schedulePreview) svRenderPreview(S.schedulePreview, pr, scope, onImported);
}

function svImportPanel(body, pr) {
  body.innerHTML = svImportHTML(pr, false);
  svWireImport(body, pr, () => { S.verify.section = 'rooms'; render(); });
}

async function svRunParse(pr, src, body, onImported) {
  const prev = body.querySelector('#sv-preview');
  prev.innerHTML = loading();
  try {
    let data;
    if (src.file) {
      const fd = new FormData(); fd.append('file', src.file);
      data = await api(`/api/projects/${pr.id}/parse-schedule`, { method: 'POST', form: fd });
    } else {
      data = await api(`/api/projects/${pr.id}/parse-schedule-text`, { method: 'POST', body: { text: src.text } });
    }
    S.schedulePreview = data.ok ? data : null;
    if (!data.ok) {
      prev.innerHTML = `<div class="notice bad">${ICON.warn}<div><b>Could not read this schedule automatically.</b>
        <br>${esc((data.warnings || []).join(' ') || '')} ${esc(data.hint || '')}</div></div>`;
      toast('Schedule not recognised', 'bad');
      return;
    }
    svRenderPreview(data, pr, body, onImported);
    toast(`Read ${data.stats.locations} locations × ${data.stats.columns} items`, 'ok');
  } catch (err) {
    prev.innerHTML = `<div class="notice bad">${ICON.warn}<div>${esc(err.message)}</div></div>`;
  }
}

function svPreviewRows(rows, render) {
  /* Phones get a compact preview: 8 rows and a tap to reveal the rest. */
  const key = 'sv_more_' + (S.verify.previewId || 'x');
  const more = !!(S.verify && S.verify[key]);
  const limit = 8;
  const show = more ? rows : rows.slice(0, limit);
  const rest = rows.length - show.length;
  return show.map(render).join('') + (rows.length > limit ? `
    <tr><td colspan="99" style="padding:8px">
      <button class="btn sm rows-toggle" data-act="sv-more">${more ? 'Show fewer rows' : `Show all ${rows.length} rows`}</button>
    </td></tr>` : '');
}

function svRenderPreview(data, pr, scope, onImported) {
  const box = (scope && scope.querySelector ? scope.querySelector('#sv-preview') : null)
    || document.getElementById('sv-preview');
  if (!box) return;
  const st = data.stats || {};
  const cols = (data.columns || []).filter((c) => Object.keys(c.cells || {}).length);
  const linked = data.columns.filter((c) => c.suggested_project_item_id);
  const weak = cols.filter((c) => (c.match_confidence || 0) < 0.5 || c.match_ambiguous);
  const locs = data.locations || [];
  const cellOf = (loc) => {                       // rooms × items mini-matrix
    const map = {};
    data.columns.forEach((c) => { if (c.cells && c.cells[loc.order] != null) map[c.order] = c.cells[loc.order]; });
    return map;
  };
  box.innerHTML = `
    <div class="card" style="margin-bottom:14px">
      <div class="hd">${ICON.check}<h3>Parsed: ${locs.length} locations × ${st.columns} items (${st.cells} quantities)</h3>
        <span class="badge ${st.columns_reconciled === st.columns_checked ? 'ok' : 'warn'}">${st.columns_reconciled}/${st.columns_checked} columns reconcile with the printed GRAND TOTAL</span></div>
      <div class="bd stack">
        ${(data.warnings || []).length ? `<div class="notice warn">${ICON.warn}<div>${data.warnings.map(esc).join('<br>')}</div></div>` : ''}
        <div class="notice ok">${ICON.check}<div>Every parsed quantity was cross-checked against the totals printed on the sheet — the columns that reconcile are safe to verify against.</div></div>
        <div class="hint mob-only">Swipe the table sideways — it scrolls within the card →</div>
        <div class="scrollx tall"><table class="tbl"><thead><tr><th>Location (row)</th><th>Floor</th><th class="num">Items</th><th class="num">Row total</th><th>Matrix</th></tr></thead><tbody>
          ${svPreviewRows(locs, (l) => {
    const cs = cellOf(l);
    const chips = Object.keys(cs).slice(0, 14).map((k) => `<span class="chip ghost" style="font-size:11px">${esc((data.columns[+k].label || '').slice(0, 18))} <b>${smartNum(cs[k])}</b></span>`).join('');
    return `<tr><td><b>${esc(l.label)}</b></td><td class="small muted">${esc(l.floor || '—')}</td>
              <td class="num">${Object.keys(cs).length}</td><td class="num">${smartNum(l.row_total)}</td>
              <td>${chips}${Object.keys(cs).length > 14 ? `<span class="muted small">+${Object.keys(cs).length - 14} more</span>` : ''}</td></tr>`;
  })}
        </tbody></table></div>
      </div>
    </div>
    <div class="card sv-head-actions" style="margin-bottom:14px">
      <div class="bd row between wrap-gap">
        <div class="small"><b>${cols.length}</b> item column(s) · <b>${locs.length}</b> location row(s) · <b>${st.cells || 0}</b> quantities found</div>
        <div class="row wrap-gap">
          <button class="btn" data-act="sv-discard">Discard</button>
          <button class="btn pri" data-act="sv-import">Import &amp; start verification</button>
        </div>
      </div>
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="hd"><h3>Column → item mapping</h3>
        <span class="muted small">${data.columns.length} columns · ${data.columns.length - weak.length} auto-linked${weak.length ? ` · ${weak.length} need your confirmation` : ''}</span></div>
      <div class="hint mob-only">Swipe the table sideways to see the mapping and the totals →</div>
      <div class="bd tight scrollx"><table class="tbl"><thead><tr><th>Schedule column</th><th class="num">Total</th><th>Matched item (Master CSR)</th><th>Confidence</th><th>Reconcile</th></tr></thead><tbody>
        ${svPreviewRows(data.columns, (c) => {
    const qty = Object.values(c.cells || {}).reduce((a, b) => a + b, 0);
    const conf = c.match_confidence || 0;
    const isWeak = conf < 0.5 || c.match_ambiguous;
    return `<tr>
        <td><b>${esc(c.label)}</b></td><td class="num">${qty ? smartNum(qty) : '—'}</td>
        <td>${c.suggested_item_code ? `<span class="pill-code">${esc(c.suggested_item_code)}</span> <span class="small clamp">${esc((c.suggested_description || '').slice(0, 70))}</span>${c.match_ambiguous ? ' <span class="badge warn">confirm</span>' : ''}` : '<span class="muted small">not matched — link it after import</span>'}</td>
        <td>${qty ? (isWeak ? `<span class="badge warn">check</span>` : conf ? `<span class="badge ${conf >= 0.6 ? 'ok' : 'info'}">${(conf * 100).toFixed(0)}%</span>` : '<span class="badge">—</span>') : '<span class="muted small">no quantity</span>'}</td>
        <td>${c.total_match === true ? `<span class="badge ok">✓ adds up</span>` : c.total_match === false ? `<span class="badge bad">✗ differs</span>` : '<span class="muted small">—</span>'}</td></tr>`;
  })}
      </tbody></table></div>
    </div>
    <div class="card"><div class="bd row between">
      <div class="small muted">Importing creates ${locs.length} room(s) in this project and stores ${st.cells} quantities for room-wise verification.</div>
      <div class="row"><button class="btn" data-act="sv-discard">Discard</button>
        <button class="btn pri" data-act="sv-import">${ICON.check} Import & start verification</button></div>
    </div></div>`;
  box.querySelectorAll('[data-act="sv-more"]').forEach((b) => b.addEventListener('click', () => {
    const k = 'sv_more_' + (S.verify.previewId || 'x');
    S.verify[k] = !S.verify[k];
    svRenderPreview(data, pr, scope, onImported);
  }));
  box.querySelectorAll('[data-act="sv-discard"]').forEach((b) => b.addEventListener('click', () => {
    S.schedulePreview = null; box.innerHTML = ''; toast('Preview discarded');
  }));
  box.querySelectorAll('[data-act="sv-import"]').forEach((btn) => btn.addEventListener('click', async () => {
    const all = [...box.querySelectorAll('[data-act="sv-import"]')];
    all.forEach((b) => { b.disabled = true; b.innerHTML = '<span class="spin"></span> Importing…'; });
    try {
      const r = await api(`/api/projects/${pr.id}/import-schedule`, {
        method: 'POST', body: { filename: data.filename || 'descriptive-schedule', parsed: data } });
      S.schedulePreview = null;
      toast(`Imported: ${r.locations} rooms, ${r.auto_mapped} items linked`, 'ok');
      if (r.need_link && r.need_link.length) {
        toast(`${r.need_link.length} column(s) need linking to the estimate`, 'warn', 6000);
      }
      if (onImported) onImported(r);
      else { S.verify.section = 'rooms'; render(); }
    } catch (err) {
      toast(err.message, 'bad');
      all.forEach((b) => { b.disabled = false; b.innerHTML = 'Import &amp; start verification'; });
    }
  }));
}

/* -------------------------------------------------------------- room list */
function svRoomCard(r, byFloor) {
  const keepable = r.items.filter((i) => i.verify_status === 'pending' && i.project_item_id).length;
  return [
    '<div class="card room-card"><div class="bd">',
    '<div class="row between"><div><b>' + esc(r.name) + '</b> ',
    (r.floor && !byFloor ? '<span class="badge">' + esc(r.floor) + '</span>' : ''),
    '</div>',
    (r.pending ? '<span class="badge warn">' + r.pending + ' pending</span>' : '<span class="badge ok">✓ verified</span>'),
    '</div>',
    '<div class="small muted" style="margin:4px 0">' + r.items.length + ' schedule item(s) · schedule total ' + smartNum(r.schedule_total) + '</div>',
    '<div class="progress ' + (r.pending ? 'warn' : '') + '"><i style="width:' + r.progress_pct + '%"></i></div>',
    '<div class="row between small muted" style="margin-top:4px"><span>' + r.kept + ' kept · ' + r.changed + ' changed</span><span>' + r.progress_pct + '%</span></div>',
    '<div class="row wrap-gap" style="margin-top:10px">',
    '<button class="btn pri grow" data-open="' + r.location_id + '">' + ICON.ruler + ' ' + (r.pending ? 'Verify' : 'Review') + '</button>',
    (keepable ? '<button class="btn grow" data-keepall="' + r.location_id + '">✓ All as per schedule</button>' : ''),
    '</div></div></div>',
  ].join('');
}

function svRenderRooms(box, pr, wl) {
  const t = wl.totals;
  const rooms = S.verify.onlyPending ? wl.rooms.filter((r) => r.pending) : wl.rooms;
  const pendingRooms = wl.rooms.filter((r) => r.pending);
  const nextRoom = pendingRooms[0];
  let cards;
  if (!S.verify.byFloor) {
    cards = '<div class="grid g-auto">' + (rooms.map((r) => svRoomCard(r, false)).join('')
      || '<div class="card"><div class="empty">' + ICON.check + '<div>Every room has been verified.</div></div></div>') + '</div>';
  } else {
    const groups = {};
    rooms.forEach((r) => { const k = r.floor || 'Other'; (groups[k] = groups[k] || []).push(r); });
    cards = Object.keys(groups).map((floor) => '<div style="margin:4px 0 8px"><span class="badge brand">' + esc(floor) + '</span> '
      + '<span class="small muted">' + groups[floor].length + ' room(s)</span></div>'
      + '<div class="grid g-auto" style="margin-bottom:14px">' + groups[floor].map((r) => svRoomCard(r, true)).join('') + '</div>').join('');
  }
  box.innerHTML = `
    <div class="card" style="margin-bottom:12px">
      <div class="bd row between wrap-gap">
        <div style="min-width:200px">
          <b>${t.pending ? 'Continue the joint verification' : 'Every room is verified'}</b>
          <div class="small muted">${t.pending
    ? `${t.pending} quantit${t.pending === 1 ? 'y' : 'ies'} still to be seen across ${pendingRooms.length} room(s). Keep what matches the schedule, change only what differs.`
    : 'All schedule quantities have been confirmed or corrected.'}</div>
        </div>
        <div class="row wrap-gap">
          ${nextRoom ? `<button class="btn pri big" data-open="${nextRoom.location_id}">${ICON.ruler} Start with ${esc(nextRoom.name)}</button>` : ''}
          <button class="btn" data-act="sv-import-again-top">${ICON.up} Upload a different schedule</button>
        </div>
      </div>
    </div>
    <div class="row between wrap-gap" style="margin-bottom:10px">
      <div class="row wrap-gap">
        <button class="chip ${S.verify.onlyPending ? 'on' : ''}" data-act="sv-only-pending">Only rooms with pending quantities</button>
        <button class="chip ${S.verify.byFloor ? 'on' : ''}" data-act="sv-group-floor">Group by floor</button>
      </div>
      <div class="small muted">Tap a room → check the schedule quantities → keep or change</div>
    </div>
    ${cards}`;
  box.querySelector('[data-act="sv-only-pending"]')?.addEventListener('click', () => {
    S.verify.onlyPending = !S.verify.onlyPending; tabVerify(document.getElementById('view'), { project: pr }, null);
  });
  box.querySelector('[data-act="sv-group-floor"]')?.addEventListener('click', () => {
    S.verify.byFloor = !S.verify.byFloor; tabVerify(document.getElementById('view'), { project: pr }, null);
  });
  box.querySelector('[data-act="sv-import-again-top"]')?.addEventListener('click', () => svImportPanel(box, pr));
  box.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => {
    const room = wl.rooms.find((r) => String(r.location_id) === b.dataset.open);
    svRoomSheet(pr, room, wl);
  }));
  box.querySelectorAll('[data-keepall]').forEach((b) => b.addEventListener('click', async () => {
    const room = wl.rooms.find((r) => String(r.location_id) === b.dataset.keepall);
    b.disabled = true; b.innerHTML = '<span class="spin"></span>…';
    try {
      const r = await queueable(`/api/schedule-docs/${S.verify.docId || wl.docs[0].id}/verify-bulk`,
        { method: 'POST', body: { action: 'keep', location_ids: [room.location_id] } });
      if (r.queued) { toast(`${room.name}: ${keepableCount(room)} quantit(ies) saved on this phone — will sync`, 'warn'); render(); return; }
      toast(`${esc(room.name)}: ${r.verified} quantit${r.verified === 1 ? 'y' : 'ies'} confirmed as per schedule` + (r.failed.length ? ` · ${r.failed.length} need item linking` : ''), r.failed.length ? 'warn' : 'ok');
      render();
    } catch (err) { toast(err.message, 'bad'); b.disabled = false; }
  }));
}

function keepableCount(room) {
  return room.items.filter((i) => i.verify_status === 'pending' && i.project_item_id).length;
}

/* ------------------------------------------------------- room verify sheet */
/* One quantity row inside a room.  Thumb-sized: the two decisions are full-width
   buttons, the actual-quantity box has +/- steppers (site gloves, bright sun), and
   the schedule number stays visible so nothing has to be remembered. */
function svRowHTML(c) {
  const bg = c.verify_status === 'pending' ? 'var(--surface)'
    : c.verify_status === 'changed' ? '#fffbeb' : '#f6fdf9';
  const queued = S.verify.queuedIds.has(c.id)
    || S.outbox.some((o) => o.kind === 'cell' && o.cellId === c.id);
  const status = queued ? svChip({ ...c, verify_status: 'kept', queued: true }) : svChip(c);
  const canVerify = !!c.project_item_id;
  const actions = !canVerify
    ? `<button class="btn pri block" data-sv="link-cell" data-col="${c.column_order}">Link this column to a Master CSR item</button>`
    : c.verify_status === 'pending' ? `
      <button class="btn ok block" data-keep="${c.id}">✓ As per schedule</button>
      <div class="sv-actual">
        <button class="btn step" data-step="-1" data-for="${c.id}" aria-label="decrease">−</button>
        <input class="i" type="number" step="any" inputmode="decimal" value="${smartNum(c.qty)}"
               data-input="${c.id}" aria-label="actual quantity">
        <button class="btn step" data-step="1" data-for="${c.id}" aria-label="increase">+</button>
        <button class="btn pri" data-change="${c.id}">Actual</button>
      </div>`
    : `<button class="btn" data-undo="${c.id}">Re-do</button>`;
  return `
    <div class="sv-row" data-cell="${c.id}" data-status="${c.verify_status}"
         data-linked="${canVerify ? '1' : '0'}" style="background:${bg}">
      <div class="sv-head">
        <div style="flex:1;min-width:0">
          <div class="sv-title">${esc(c.col_label)}</div>
          <div class="small muted">${c.item_code ? `<span class="pill-code">${esc(c.item_code)}</span> ` : '<span class="badge bad">not linked</span> '}
            ${esc((c.item_description || '').slice(0, 58))}${c.unit ? ` · per ${esc(c.unit)}` : ''}</div>
        </div>
        <div class="sv-qty"><div class="tiny muted">SCHEDULE</div><div class="n">${smartNum(c.qty)}</div></div>
      </div>
      <div class="sv-state">${status}${c.verify_status === 'changed' && c.actual_qty != null
    ? ` <span class="small muted">was ${smartNum(c.qty)} → actual ${smartNum(c.actual_qty)}</span>` : ''}</div>
      <div class="sv-actions">${actions}</div>
    </div>`;
}

function svSheetBody(room, unlinked) {
  return `
    <div class="sv-summary">
      <div class="progress ${room.pending ? 'warn' : ''}"><i data-sv="bar" style="width:${room.progress_pct}%"></i></div>
      <div class="row between small" style="margin-top:6px">
        <span><b data-sv="kept">${room.kept}</b> kept · <b data-sv="changed">${room.changed}</b> changed · <b data-sv="pending">${room.pending}</b> pending</span>
        <span class="muted">${room.items.length} item(s) · total ${smartNum(room.schedule_total)}</span>
      </div>
      <button class="btn block big" data-sv="allkeep" style="margin-top:8px">✓ All remaining as per schedule</button>
    </div>
    ${unlinked.length ? `<div class="notice warn" style="margin-top:10px">${ICON.warn}<div><b>${unlinked.length} column(s) in this room are not linked to an estimate item yet</b>
      (${unlinked.map((u) => esc(u.col_label)).slice(0, 4).join(', ')}${unlinked.length > 4 ? '…' : ''}).
      <br><button class="btn sm" data-sv="link">Link them now</button></div></div>` : ''}
    <div class="stack" id="sv-rows" style="margin-top:12px">
      ${room.items.map(svRowHTML).join('') || '<div class="empty">No scheduled quantities in this room.</div>'}
    </div>`;
}

async function svRoomSheet(pr, room, wl, keepScroll) {
  const q = S.verify.docId ? `?doc_id=${S.verify.docId}` : '';
  if (!keepScroll) {
    wl = await api(`/api/projects/${pr.id}/verify${q}`).catch(() => wl);
    room = wl.rooms.find((r) => r.location_id === room.location_id) || room;
  }
  const docId = S.verify.docId || wl.docs[0].id;
  const unlinked = room.items.filter((i) => !i.project_item_id);
  const live = document.querySelector('.sheet-bg');          // our own sheet, if it is open
  const keepScrollTop = live ? (document.getElementById('sheet-body') || {}).scrollTop || 0 : 0;

  const paint = () => {                    // repaint rows + counters without re-binding anything
    const rows = document.getElementById('sv-rows');
    if (rows) rows.innerHTML = room.items.map(svRowHTML).join('') || '<div class="empty">No scheduled quantities.</div>';
    const k = document.querySelector('[data-sv="kept"]'), c = document.querySelector('[data-sv="changed"]'),
      pd = document.querySelector('[data-sv="pending"]'), pr2 = document.querySelector('[data-sv="bar"]');
    if (k) k.textContent = room.kept;
    if (c) c.textContent = room.changed;
    if (pd) pd.textContent = room.pending;
    if (pr2) pr2.style.width = room.progress_pct + '%';
    paintOutboxBadge();
  };
  const applyLocal = (cellId, status, qty) => {
    const c = room.items.find((x) => x.id === cellId);
    if (!c) return;
    c.verify_status = status;
    c.actual_qty = status === 'changed' ? qty : (status === 'kept' ? qty : null);
    room.kept = room.items.filter((x) => x.verify_status === 'kept').length;
    room.changed = room.items.filter((x) => x.verify_status === 'changed').length;
    room.pending = room.items.filter((x) => x.verify_status === 'pending').length;
    room.progress_pct = Math.round(1000 * (room.kept + room.changed) / Math.max(1, room.items.length)) / 10;
  };

  /* One delegated click handler on the sheet: innerHTML repaints never detach it. */
  const onClick = async (e) => {
    const step = e.target.closest('[data-step]');
    if (step) {
      const inp = document.querySelector(`[data-input="${step.dataset.for}"]`);
      if (inp) { inp.value = smartNum(Math.max(0, (parseFloat(inp.value) || 0) + Number(step.dataset.step))); inp.dataset.touched = '1'; }
      return;
    }
    const keep = e.target.closest('[data-keep]');
    if (keep) {
      const id = Number(keep.dataset.keep);
      keep.disabled = true; keep.innerHTML = '<span class="spin"></span>';
      try {
        const r = await queueable(`/api/schedule-cells/${id}/verify`, { method: 'POST', body: { action: 'keep' } });
        if (r.queued) S.verify.queuedIds.add(id);
        applyLocal(id, 'kept', r.qty);
        toast(r.queued ? 'Saved on this phone — will sync automatically' : 'Kept as per schedule', r.queued ? 'warn' : 'ok');
      } catch (err) { toast(err.message, 'bad'); }
      paint();
      return;
    }
    const change = e.target.closest('[data-change]');
    if (change) {
      const id = Number(change.dataset.change);
      const inp = document.querySelector(`[data-input="${id}"]`);
      const v = parseFloat(inp ? inp.value : '');
      if (isNaN(v) || v < 0) { toast('Enter the actual quantity measured at site', 'bad'); return; }
      change.disabled = true; change.innerHTML = '<span class="spin"></span>';
      try {
        const r = await queueable(`/api/schedule-cells/${id}/verify`,
          { method: 'POST', body: { action: 'change', actual_qty: v, note: 'actual at site' } });
        if (r.queued) S.verify.queuedIds.add(id);
        applyLocal(id, r.status, r.qty);
        toast(r.queued ? `Saved on this phone: ${smartNum(v)} — will sync`
          : (r.status === 'changed' ? `Changed to actual ${smartNum(v)} · measurement recorded` : 'Matches the schedule — kept'),
          r.queued ? 'warn' : 'ok');
      } catch (err) { toast(err.message, 'bad'); }
      paint();
      return;
    }
    const undo = e.target.closest('[data-undo]');
    if (undo) {
      const id = Number(undo.dataset.undo);
      try {
        await queueable(`/api/schedule-cells/${id}/verify`, { method: 'POST', body: { action: 'pending' } });
        applyLocal(id, 'pending', null);
        S.verify.queuedIds.delete(id);
        outboxDrop({ kind: 'cell', cellId: id });
        paint(); toast('Back to pending');
      } catch (err) { toast(err.message, 'bad'); }
      return;
    }
    const bulk = e.target.closest('[data-sv="allkeep"]');
    if (bulk) {
      const before = keepableCount(room);
      bulk.disabled = true; bulk.innerHTML = '<span class="spin"></span> Saving…';
      try {
        const r = await queueable(`/api/schedule-docs/${docId}/verify-bulk`,
          { method: 'POST', body: { action: 'keep', location_ids: [room.location_id] } });
        room.items.forEach((c) => { if (c.verify_status === 'pending' && c.project_item_id) applyLocal(c.id, 'kept', c.qty); });
        toast(r.queued ? `Saved on this phone — ${before} quantity(ies) will sync`
          : `${r.verified} quantity(ies) kept as per schedule`, r.queued ? 'warn' : 'ok');
      } catch (err) { toast(err.message, 'bad'); }
      bulk.disabled = false; bulk.innerHTML = '✓ All remaining as per schedule';
      paint();
      return;
    }
    if (e.target.closest('[data-sv="link-cell"]')) {
      const col = Number(e.target.closest('[data-sv="link-cell"]').dataset.col);
      svLinkSheet(pr, wl, room.items.find((i) => i.column_order === col) || null);
      return;
    }
    if (e.target.closest('[data-sv="link"]')) { svLinkSheet(pr, wl, room.items.find((i) => !i.project_item_id)); return; }
    if (e.target.closest('[data-sv="prev-pending"]')) {
      const bd = document.getElementById('sheet-body');
      const next = [...document.querySelectorAll('.sv-row')].find((r) => r.dataset.status === 'pending');
      if (next && bd) {
        bd.scrollTo({ top: next.offsetTop - 12, behavior: 'smooth' });
        next.classList.add('sv-flash'); setTimeout(() => next.classList.remove('sv-flash'), 1200);
      } else toast('Nothing pending in this room', 'ok');
    }
  };

  if (keepScroll && live) {                                   // repaint in place
    const bd = document.getElementById('sheet-body');
    if (bd) { bd.innerHTML = svSheetBody(room, unlinked); bd.scrollTop = keepScrollTop; }
    paint();
    return;
  }
  const el = sheet({
    title: room.name,
    subtitle: `${room.items.length} scheduled quantities · total ${smartNum(room.schedule_total)}`,
    body: loading('Loading the room checklist…'),
    wide: true,
    body_id: 'sheet-body',
    sticky: true,
    footer: `<button class="btn" data-sv="prev-pending">↑ Next pending</button>
             <button class="btn pri" data-act="close-sheet">Done — back to rooms</button>`,
  });
  const body_el = document.getElementById('sheet-body');      // render the rows into the sheet body
  if (body_el) body_el.innerHTML = svSheetBody(room, unlinked);
  el.addEventListener('click', onClick);
  paintOutboxBadge();
}

/* ------------------------------------------------------------- link a column */
async function svLinkSheet(pr, wl, cell) {
  const docId = S.verify.docId || wl.docs[0].id;
  const doc = await api(`/api/schedule-docs/${docId}`);
  let parsedCols = [];
  try { parsedCols = (JSON.parse(doc.raw_json || '{}').columns) || []; } catch (e) { parsedCols = []; }
  const cells = doc.cells.filter((c) => !c.project_item_id);
  const orders = [...new Set(cells.map((c) => c.column_order))];
  const info = {}; orders.forEach((o) => { info[o] = cells.find((c) => c.column_order === o); });
  const start = (cell && cell.column_order) || orders[0];
  sheet({
    title: 'Link schedule columns to Master CSR items',
    wide: true,
    body: `<div class="notice info">${ICON.shield}<div>These columns carry quantities but are not yet tied to an estimate item.
      Matching a column attaches the <b>Master CSR description, unit and rate</b> — or creates an extra item for work that is in the schedule but not in the estimate abstract.</div></div>
      <div class="row" style="margin:10px 0"><span class="small muted">${orders.length} column(s)</span>
        <select class="i" id="sv-col" style="flex:1">${orders.map((o) => `<option value="${o}" ${o === start ? 'selected' : ''}>${esc(info[o].col_label)} — ${Object.keys(cells.filter((c) => c.column_order === o)).length ? smartNum(cells.filter((c) => c.column_order === o).map((c) => c.qty).reduce((a, b) => a + b, 0)) : 0} nos</option>`).join('')}</select></div>
      <div class="bd tight" style="padding-top:0">
        <div id="sv-suggest"></div>
        <label class="f" style="margin-top:10px">…or search the Master CSR</label>
        <input class="i" id="sv-q" placeholder="e.g. exhaust fan, LED panel, 6A switch"></div>
      <div id="sv-results" class="stack" style="margin-top:8px"></div>`,
    footer: `<button class="btn" data-act="close-sheet">Close</button>`,
    onOpen: (el) => {
      const sel = el.querySelector('#sv-col'), q = el.querySelector('#sv-q'), res = el.querySelector('#sv-results');
      const sugBox = el.querySelector('#sv-suggest');
      if (!sel || !q || !res || !sugBox) return;
      const paintSuggest = () => {
        const pc = parsedCols.find((c) => Number(c.order) === Number(sel.value));
        const alts = (pc && pc.alternatives) || [];
        sugBox.innerHTML = alts.length ? `
          <div class="small muted" style="margin:6px 0 4px">The wording of this column most resembles these estimate / CSR items — tap the right one:</div>
          <div class="stack" style="gap:6px">
            ${alts.map((a) => `
              <div class="row between" style="border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:${a.kind === 'project_item' ? '#f6fdf9' : 'var(--surface)'}">
                <div style="flex:1;min-width:0"><span class="pill-code">${esc(a.code || '')}</span>
                  <span class="badge ${a.kind === 'project_item' ? 'ok' : 'info'}">${a.kind === 'project_item' ? 'in estimate' : 'Master CSR'}</span>
                  <span class="small">${esc((a.description || '').slice(0, 92))}</span>
                  <div class="small muted">${esc(a.unit || '')}${a.rate ? ` · ₹ ${inr(a.rate)}` : ''} · wording match ${(a.score * 100).toFixed(0)}%</div></div>
                <button class="btn sm ${a.kind === 'project_item' ? 'pri' : ''}" data-alt='${JSON.stringify({ k: a.kind, id: a.id })}'>Use this</button>
              </div>`).join('')}
          </div>` : `<div class="small muted" style="margin:6px 0 4px">No close match in the wording — search the Master CSR below and link the correct item, or create an extra item for work that is not in the estimate abstract.</div>`;
        sugBox.querySelectorAll('[data-alt]').forEach((b) => b.addEventListener('click', async () => {
          const spec = JSON.parse(b.dataset.alt);
          b.disabled = true; b.innerHTML = '<span class="spin"></span>';
          try {
            const payload = spec.k === 'project_item' ? { project_item_id: spec.id } : { master_item_id: spec.id };
            const out = await api(`/api/schedule-docs/${docId}/columns/${sel.value}/map`, { method: 'POST', body: payload });
            toast(`Linked to ${out.item_code}`, 'ok');
            svLinkSheet(pr, wl, null);
          } catch (err) { toast(err.message, 'bad'); b.disabled = false; b.textContent = 'Use this'; }
        }));
      };
      let timer;
      const run = async () => {
        const term = q.value.trim() || info[Number(sel.value)].col_label;
        res.innerHTML = loading();
        try {
          const r = await api(`/api/csr/suggest?q=${encodeURIComponent(term)}&fy=${encodeURIComponent(pr.csr_fy || '')}&region=${encodeURIComponent(pr.csr_region || '')}&limit=6`);
          res.innerHTML = (r.results || []).map((it) => `
            <div class="row between" style="border:1px solid var(--line);border-radius:10px;padding:8px 10px">
              <div style="flex:1;min-width:0"><span class="pill-code">${esc(it.item_code)}</span>
                <span class="small">${esc((it.description || '').slice(0, 92))}</span>
                <div class="small muted">${esc(it.unit)} · ₹ ${inr(it.rate)}</div></div>
              <div class="row" style="gap:6px">
                <button class="btn sm pri" data-master="${it.id}">Link as estimate item</button></div></div>`).join('')
            || `<div class="muted small">No suggestion for “${esc(term)}”. Try different words, or create an extra item below.</div>`;
          res.querySelectorAll('[data-master]').forEach((b) => b.addEventListener('click', async () => {
            b.disabled = true; b.innerHTML = '<span class="spin"></span>';
            try {
              const out = await api(`/api/schedule-docs/${docId}/columns/${sel.value}/map`, { method: 'POST', body: { master_item_id: Number(b.dataset.master) } });
              toast(`Linked to ${out.item_code}`, 'ok');
              svLinkSheet(pr, wl, null);
            } catch (err) { toast(err.message, 'bad'); b.disabled = false; b.textContent = 'Link'; }
          }));
        } catch (err) { res.innerHTML = `<div class="notice bad">${ICON.warn}<div>${esc(err.message)}</div></div>`; }
      };
      q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 320); });
      sel.addEventListener('change', () => { q.value = ''; paintSuggest(); run(); });
      paintSuggest();
      run();
    },
  });
}

/* -------------------------------------------------------- reconciliation view */
function svRenderRecon(box, rec, wl, pr) {
  if (!rec) { box.innerHTML = `<div class="empty">No data</div>`; return; }
  const rows = rec.rows.filter((r) => r.locations.length || r.measured_qty);
  const t = rec.totals;
  box.innerHTML = `
    <div class="notice info" style="margin-bottom:12px">${ICON.shield}<div>The descriptive schedule is the quantity actually distributed over the building.
      Where it differs from the estimate abstract, the difference is shown here <b>before</b> the joint measurement — so you only visit the rooms that differ.</div></div>
    <div class="grid g3" style="margin-bottom:14px">
      <div class="card kpi"><div class="lbl">Estimate (tendered)</div><div class="val sm">₹ ${inr(t.tendered_amount, 0)}</div><div class="foot">all project items</div></div>
      <div class="card kpi"><div class="lbl">Descriptive schedule</div><div class="val sm">₹ ${inr(t.schedule_amount, 0)}</div><div class="foot">${t.scheduled_items} item(s) distributed room-wise</div></div>
      <div class="card kpi"><div class="lbl">To be verified</div><div class="val sm" style="color:${t.pending_cells ? 'var(--warn)' : 'var(--ok)'}">${t.pending_cells}</div>
        <div class="foot">${t.changed_cells} changed so far${t.unmapped_columns ? ` · ${t.unmapped_columns} unlinked column(s)` : ''}</div></div>
    </div>
    ${rec.orphans.length ? `<div class="card" style="margin-bottom:14px"><div class="bd"><div class="row between">
        <div class="small"><b>${rec.orphans.length} schedule quantities are not linked to an estimate item yet.</b>
        <div class="muted">Link them to pull the legal description and rate from the Master CSR.</div></div>
        <button class="btn sm pri" data-act="sv-link-open">Link columns</button></div></div></div>` : ''}
    <div class="card"><div class="hd"><h3>Item-wise control sheet</h3><span class="muted small">estimate vs schedule vs actual</span></div>
      <div class="hint mob-only">Swipe the table sideways for estimate / schedule / actual →</div>
      <div class="bd tight scrollx"><table class="tbl"><thead><tr>
        <th>Item</th><th>Description (Master CSR)</th><th class="num">Rate</th>
        <th class="num">Estimate qty</th><th class="num">Schedule qty</th><th class="num">Δ sched−est</th>
        <th class="num">Actual so far</th><th>Rooms</th></tr></thead><tbody>
        ${rows.map((r) => {
    const d = r.schedule_vs_tendered;
    return `<tr><td class="nowrap"><span class="pill-code">${esc(r.item_code)}</span>${r.is_non_schedule ? ' <span class="badge ns">NS</span>' : ''}</td>
          <td class="small clamp">${esc((r.description || '').slice(0, 84))}</td>
          <td class="num nowrap">₹ ${inr(r.rate, 0)}</td>
          <td class="num">${smartNum(r.tendered_qty)} <span class="muted small">${esc(r.unit || '')}</span></td>
          <td class="num"><b>${smartNum(r.schedule_qty)}</b></td>
          <td class="num" style="color:${Math.abs(d) < 0.001 ? 'var(--muted)' : (d > 0 ? 'var(--bad)' : 'var(--ok)')}">${d > 0 ? '+' : ''}${smartNum(d)}</td>
          <td class="num">${smartNum(r.measured_qty)}</td>
          <td class="small muted">${r.locations.length ? `${r.locations.filter((l) => l.verify_status !== 'pending').length}/${r.locations.length} verified` : '—'}</td></tr>`;
  }).join('') || `<tr><td colspan="8"><div class="empty">No schedule quantities imported yet.</div></td></tr>`}
      </tbody></table></div></div>
    <div class="card" style="margin-top:14px"><div class="bd">
      <div class="row between"><div class="small muted">Room totals were checked against the printed GRAND TOTAL when the schedule was read.</div>
        <button class="btn sm" data-act="dl" data-path="/api/projects/${rec.project_id}/reconciliation.xlsx" data-name="Schedule_Reconciliation.xlsx">Export</button></div>
    </div></div>`;
  box.querySelector('[data-act="sv-link-open"]')?.addEventListener('click', () => svLinkSheet(pr || { csr_fy: '', csr_region: '' }, wl, null));
}

function svRenderDocs(box, pr, wl) {
  box.innerHTML = `<div class="stack">
    ${wl.docs.map((d) => `
      <div class="card"><div class="bd">
        <div class="row between">
          <div><b>${esc(d.filename || 'schedule')}</b> <span class="badge brand">${esc(d.engine || '')}</span>
            <div class="small muted">${d.locations} locations · ${d.cells} quantities · ${d.checked} verified (${d.progress_pct}%) · imported ${dtt(d.created_at)} by ${esc(d.uploaded_by_name || '—')}</div>
            ${(d.warnings || []).length ? `<div class="small" style="color:var(--warn)">${d.warnings.map(esc).join('<br>')}</div>` : ''}</div>
          <div class="row">
            <button class="btn sm danger" data-del="${d.id}">Remove</button></div>
        </div>
        <div class="progress ${d.progress_pct < 100 ? 'warn' : ''}" style="margin-top:8px"><i style="width:${d.progress_pct}%"></i></div>
      </div></div>`).join('')}
  </div>`;
  box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Remove this descriptive schedule and its verification state?')) return;
    try { await api(`/api/schedule-docs/${b.dataset.del}`, { method: 'DELETE' }); toast('Schedule removed'); render(); }
    catch (err) { toast(err.message, 'bad'); }
  }));
}
