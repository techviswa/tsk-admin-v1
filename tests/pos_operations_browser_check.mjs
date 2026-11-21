// Optional local acceptance check: node tests/pos_operations_browser_check.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdtemp, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const config = require('dotenv').parse(await readFile(new URL('../.env.development.local', import.meta.url)));
const origin = 'http://localhost:3001';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const profile = await mkdtemp(path.join(tmpdir(), 'admincore-pos-check-'));
const chrome = spawn(process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  { windowsHide: true, stdio: 'ignore' });
const pending = new Map();
const errors = [];
let socket, sequence = 0;
try {
  let port;
  for (let attempt = 0; attempt < 40 && !port; attempt++) {
    try { port = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; } catch { await pause(250); }
  }
  assert.ok(port, 'Chrome did not start');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) { const call = pending.get(message.id); pending.delete(message.id); message.error ? call.reject(message.error) : call.resolve(message.result); }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (message.method === 'Network.responseReceived' && message.params.response.url.includes('/api/') && message.params.response.status >= 400) errors.push(`${message.params.response.status} ${message.params.response.url}`);
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!result.exceptionDetails, result.exceptionDetails?.text); return result.result.value;
  };
  const waitFor = async expression => {
    for (let attempt = 0; attempt < 80; attempt++) { if (await evaluate(expression)) return; await pause(250); }
    throw new Error(`Browser condition timed out: ${expression}`);
  };
  await send('Network.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false });
  const login = await fetch(`${origin}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify({ email: config.ADMIN_EMAIL, password: config.ADMIN_PASSWORD }) });
  assert.equal(login.status, 200);
  for (const cookie of login.headers.getSetCookie()) {
    const first = cookie.split(';')[0]; const equal = first.indexOf('=');
    await send('Network.setCookie', { name: first.slice(0, equal), value: first.slice(equal + 1), url: origin, httpOnly: true, sameSite: 'Lax' });
  }
  await send('Page.navigate', { url: `${origin}/pos-admin/customer-profiles` });
  await waitFor(`!!document.querySelector('[data-testid="business-switcher"]')`);
  await evaluate(`document.querySelector('[data-testid="business-switcher"]').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'}))`);
  await waitFor(`[...document.querySelectorAll('[role="menuitem"]')].some(item=>item.textContent.includes('Demo POS Business'))`);
  await evaluate(`[...document.querySelectorAll('[role="menuitem"]')].find(item=>item.textContent.includes('Demo POS Business')).click()`);
  await waitFor(`!!document.querySelector('[data-testid="pos-operations-page"] tbody tr button')`);
  await evaluate(`document.querySelector('[data-testid="pos-operations-page"] tbody tr button').click()`);
  await waitFor(`!!document.querySelector('[role="dialog"]')`);
  assert.ok(await evaluate(`document.querySelector('[role="dialog"]').innerText.includes('Total spend')`), 'Customer details must have named business fields');
  await evaluate(`document.querySelector('[role="dialog"] button').click()`);
  await waitFor(`!document.querySelector('[role="dialog"]')`);
  for (const resource of ['customer-profiles', 'attendance', 'tips', 'payroll', 'gift-cards', 'marketing', 'menu-pricing', 'guest-feedback', 'table-assistance', 'qr-ordering', 'central-kitchen']) {
    await evaluate(`document.querySelector('a[href="/pos-admin/${resource}"]').click()`);
    await waitFor(`location.pathname==='/pos-admin/${resource}' && !!document.querySelector('[data-testid="pos-operations-page"]') && !document.body.innerText.includes('Loading POS records')`);
    assert.ok(!(await evaluate(`!!document.querySelector('[role="alert"]')`)), `${resource} displayed an error`);
    assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), `${resource} overflows the desktop viewport`);
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: false });
    await pause(100);
    assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), `${resource} overflows the mobile viewport`);
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false });
    console.log(`Verified AdminCore screen: ${resource}`);
  }
  await evaluate(`document.querySelector('a[href="/outlets"]').click()`);
  await waitFor(`!!document.querySelector('#outlet-status') && !document.body.innerText.includes('Loading...')`);
  assert.equal(await evaluate(`document.querySelector('#outlet-status').value`), 'active');
  for (const width of [1440, 768, 390, 320]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 960, deviceScaleFactor: 1, mobile: false });
    await pause(250);
    assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), `Outlets overflows at ${width}px`);
    assert.equal(await evaluate(`document.querySelector('[data-testid="top-nav"]').getBoundingClientRect().left`), 0);
    if (width >= 768) assert.equal(await evaluate(`document.querySelector('[data-testid="sidebar"]').getBoundingClientRect().left`), 0);
    console.log(`Verified layout at ${width}px`);
  }
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false });
  for (const [route, testId] of [['/','dashboard-page'], ['/control-center','control-center-page'], ['/pos-admin/sales-orders','pos-admin-sales-orders'], ['/pos-bridge','pos-bridge-page'], ['/feature-flags','feature-flags-page'], ['/audit-logs','audit-logs-page'], ['/settings','settings-page'], ['/integrations','integrations-page'], ['/clients','clients-page'], ['/businesses','businesses-page'], ['/users','users-page'], ['/modules','modules-page'], ['/plans','plans-page'], ['/subscriptions','subscriptions-page'], ['/pos-admin/payments','pos-admin-payments'], ['/pos-admin/bills','pos-admin-bills'], ['/pos-admin/reports-analytics','pos-admin-reports-analytics']]) {
    await evaluate(`document.querySelector('a[href="${route}"]').click()`);
    await waitFor(`location.pathname===${JSON.stringify(route)} && !!document.querySelector('[data-testid="${testId}"]') && !document.body.innerText.includes('Loading')`);
    await pause(500);
    assert.ok(!(await evaluate(`!!document.querySelector('[role="alert"]')`)), `${route} displayed a loading error`);
    if (route === '/audit-logs') assert.ok(await evaluate(`document.querySelectorAll('tbody tr').length > 1`), 'Audit log must display existing activity');
    if (route === '/pos-admin/bills') assert.ok(await evaluate(`document.body.innerText.includes('Bill ') && ![...document.querySelectorAll('tbody tr td:first-child p:first-child')].some(node=>node.textContent==='Walk-in Customer')`), 'Bills must use invoice or source references');
    if (['/audit-logs','/feature-flags','/pos-admin/bills','/pos-admin/payments'].includes(route)) {
      await mkdir('test_reports', { recursive: true });
      await writeFile(`test_reports/console${route.replace(/\//g, '-')}.png`, Buffer.from((await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
    }
    console.log(`Verified AdminCore administration screen: ${route}`);
  }
  await evaluate(`document.querySelector('a[href="/outlets"]').click()`);
  await waitFor(`!!document.querySelector('#outlet-status') && !document.body.innerText.includes('Loading...')`);
  await mkdir('test_reports', { recursive: true });
  await writeFile('test_reports/admincore-pos-operations.png', Buffer.from((await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
  const incompleteName = await evaluate(`(async()=>{const rows=await (await fetch('/api/businesses')).json();return rows.find(row=>row.pos_connection_state==='setup_required')?.name || null})()`);
  if (incompleteName) {
    await evaluate(`document.querySelector('[data-testid="business-switcher"]').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'}))`);
    await waitFor(`[...document.querySelectorAll('[role="menuitem"]')].some(item=>item.textContent.includes(${JSON.stringify(incompleteName)}))`);
    await evaluate(`[...document.querySelectorAll('[role="menuitem"]')].find(item=>item.textContent.includes(${JSON.stringify(incompleteName)})).click()`);
    await waitFor(`document.body.innerText.includes("POS connection needs review")`);
    await waitFor(`!!document.querySelector('[data-testid="outlets-page"]') && [...document.querySelectorAll('button')].some(button=>button.textContent==='Refresh from POS')`);
    assert.ok(await evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent==='Refresh from POS')?.disabled`), 'Incomplete mapping must disable unsafe refresh');
    await writeFile('test_reports/admincore-connection-review.png', Buffer.from((await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })).data, 'base64'));
    console.log('Verified incomplete connection warning and blocked refresh');
  }
  assert.deepEqual(errors, [], 'Browser runtime or API errors');
  console.log('POS operations browser acceptance check passed');
} finally {
  if (socket) socket.close(); chrome.kill();
}
