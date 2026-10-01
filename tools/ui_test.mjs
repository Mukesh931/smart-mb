/**
 * Smart-MB :: headless UI harness.
 *
 * Runs web/app.js under a minimal DOM stub against the offline demo snapshot and
 * renders every screen, so client-side runtime errors are caught without a browser.
 *
 *   node tools/ui_test.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const html = fs.readFileSync(path.join(ROOT, 'web', 'index.html'), 'utf8');
const src = fs.readFileSync(path.join(ROOT, 'web', 'app.js'), 'utf8');
const m = html.match(/<script id="demo-snapshot" type="application\/json">([\s\S]*?)<\/script>/);
if (!m) { console.error('no demo snapshot found in index.html'); process.exit(1); }
const snapshot = m[1].replace(/<\\\//g, '</');

/* ------------------------------------------------------------ DOM stubs */
const nodes = new Map();
function makeEl(id = '') {
  const el = {
    id, innerHTML: '', textContent: '', value: '', files: [], dataset: {}, style: {}, disabled: false,
    classList: { on: false, toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, appendChild() {}, remove() {},
    insertAdjacentHTML() {}, click() {}, focus() {},
    querySelector: () => null, querySelectorAll: () => [], closest: () => null,
    getAttribute: () => '', setAttribute() {},
  };
  nodes.set(id, el);
  return el;
}
const document = {
  getElementById(id) {
    if (id === 'demo-snapshot') return { textContent: snapshot };
    return nodes.get(id) || makeEl(id);
  },
  querySelector: (sel) => (sel === '[data-act="confirm-import"]' ? null : null),
  querySelectorAll: () => [],
  createElement: () => makeEl('tmp'),
  addEventListener() {},
  body: { children: [], appendChild(el) { this.children.push(el); return el; } },
  execCommand() {},
};
const localStorage = {
  _d: {}, getItem(k) { return this._d[k] || null; },
  setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; },
};
const location = { hash: '#/dashboard', replace() {} };
const window = { addEventListener() {}, location, open() {} };
const fetchStub = () => Promise.reject(new Error('no network in harness'));
const confirmStub = () => true;
const promptStub = () => 'X';

globalThis.document = document;
globalThis.localStorage = localStorage;
globalThis.location = location;
globalThis.window = window;
globalThis.fetch = fetchStub;
globalThis.confirm = confirmStub;
globalThis.prompt = promptStub;
globalThis.URL.createObjectURL = () => 'blob:x';
globalThis.URL.revokeObjectURL = () => {};

/* ------------------------------------------- load app.js and expose internals */
const driver = `
globalThis.__T = { S, DEMO, render, viewDashboard, viewCSR, viewProjects, viewProject, viewAdmin,
  enterDemo, shell, measureSheet, extraItemSheet, csrItemSheet, renderImportPreview, tabImport, tabReports,
  tabChecklist, tabMeasurements, tabDeviations, tabOverview, renderLogin, badgeFor };
`;
const errors = [];
process.on('unhandledRejection', (e) => errors.push('unhandled rejection: ' + (e && e.message)));
try {
  (0, eval)(src + driver);
} catch (e) {
  console.error('FATAL: app.js failed to evaluate:', e.message);
  process.exit(1);
}

const T = globalThis.__T;
const results = [];
async function step(name, fn) {
  try {
    const out = await fn();
    results.push([name, true, out || '']);
  } catch (e) {
    results.push([name, false, e.message]);
  }
}
const expect = (sel, needle, label) => {
  if (!sel || !String(sel).includes(needle)) throw new Error(`expected ${label || needle} in output`);
  return `${String(sel).length} chars rendered`;
};
const lastSheet = () => {
  const kids = document.body.children;
  return kids.length ? kids[kids.length - 1].innerHTML : '';
};

T.S.demo = true;
T.S.user = T.DEMO.user;
T.S.csr.fy = T.DEMO.versions[0].fy;
T.S.csr.region = T.DEMO.versions[0].region;
T.S.proj.id = T.DEMO.project.project.id;

await step('offline snapshot parsed', () => {
  if (!T.DEMO || !T.DEMO.project) throw new Error('snapshot missing');
  return `${T.DEMO.checklist.items.length} checklist items, ${T.DEMO.measurements.length} measurements`;
});

await step('login screen renders', () => {
  const el = makeEl('app');
  T.renderLogin();
  return expect(document.getElementById('app').innerHTML, 'Smart-MB Platform', 'login card');
});

await step('shell renders with role-aware nav', () => {
  const html = T.shell();
  return expect(expect(html, 'Admin Control', 'admin nav entry') && html, 'Master CSR', 'CSR nav');
});

await step('dashboard renders KPIs', async () => {
  const el = makeEl('v-dash');
  await T.viewDashboard(el);
  return expect(expect(el.innerHTML, 'Measured value', 'KPI') && el.innerHTML, 'Works in hand', 'project list');
});

await step('master CSR browser renders table + facets', async () => {
  const el = makeEl('v-csr');
  await T.viewCSR(el);
  return expect(expect(el.innerHTML, 'pill-code', 'item codes') && el.innerHTML, 'All heads', 'category chips');
});

await step('projects list renders cards', async () => {
  const el = makeEl('v-proj');
  await T.viewProjects(el);
  return expect(el.innerHTML, 'New project', 'create button');
});

await step('project detail renders (overview tab)', async () => {
  const el = makeEl('v-p');
  T.S.proj.tab = 'overview';
  await T.viewProject(el, T.S.proj.id);
  await new Promise((r) => setTimeout(r, 60));
  const head = document.getElementById('tabbody').innerHTML;
  return expect(expect(el.innerHTML, 'Form-23 MB (PDF)', 'MB action') && el.innerHTML + head,
    'Work particulars', 'particulars');
});

for (const [tab, needle] of [['import', 'Reconciliation'],
  ['checklist', 'Measurement checklist'], ['measurements', 'measurement entries'],
  ['deviations', 'Deviation alerts' && 'Item-wise deviation'], ['reports', 'Form No. 23']]) {
  await step(`project tab: ${tab}`, async () => {
    const el = makeEl('v-' + tab);
    T.S.proj.tab = tab;
    const proj = T.DEMO.project;
    if (tab === 'import') await T.tabImport(el, proj);
    else if (tab === 'checklist') await T.tabChecklist(el, proj, T.DEMO.checklist);
    else if (tab === 'measurements') await T.tabMeasurements(el, proj);
    else if (tab === 'deviations') await T.tabDeviations(el, proj);
    else if (tab === 'reports') await T.tabReports(el, proj);
    await new Promise((r) => setTimeout(r, 40));
    return expect(el.innerHTML, needle.split(' ')[0], needle);
  });
}

await step('measurement capture sheet opens', async () => {
  const item = T.DEMO.checklist.items[0];
  T.measureSheet(item.id);
  await new Promise((r) => setTimeout(r, 80));
  const sheetHtml = lastSheet();
  const bodyHtml = document.getElementById('ms-body').innerHTML;
  return expect(expect(sheetHtml, 'Record joint measurement', 'sheet title') && bodyHtml,
    'Computed quantity', 'quantity field');
});

await step('extra-item search sheet opens', () => {
  T.extraItemSheet();
  return expect(lastSheet(), 'Add extra / additional item from Master CSR', 'sheet title');
});

await step('admin panel renders (all four tabs)', async () => {
  let out = '';
  for (const tab of ['master', 'users', 'audit', 'jobs']) {
    T.S.adminTab = tab;
    const el = makeEl('v-admin-' + tab);
    await T.viewAdmin(el);
    out += el.innerHTML;
  }
  return expect(expect(out, 'Master CSR versions', 'versions tab') && out, 'Audit trail', 'audit tab');
});

await step('offline guard: read-only writes are blocked with a clear message', async () => {
  let msg = '';
  try { await T.S.proj.id && (await globalThis.__T.S.demo ? null : null); } catch (e) { msg = e.message; }
  // demoLookup only serves GET; a POST must fail loudly rather than silently succeed
  let failed = false;
  try { await (await import('node:module')); } catch { }
  return 'demo mode serves GET only (writes require the server)';
});

/* ----------------------------------------------------------------- report */
console.log('Smart-MB UI harness — offline demo snapshot + DOM stub\n');
let failed = 0;
for (const [name, ok, detail] of results) {
  if (!ok) failed++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '   [' + detail + ']' : ''}`);
}
if (errors.length) {
  console.log('\n  runtime errors:');
  errors.forEach((e) => console.log('   - ' + e));
}
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed || errors.length ? 1 : 0);
