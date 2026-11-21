import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { pythonRuntime } from './python-runtime.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(root, 'frontend/package.json'));
const dotenv = require('dotenv');
const file = path.join(root, '.env.development.local');
if (!fs.existsSync(file)) throw new Error('Create .env.development.local using .env.development.example before starting local services.');
const config = { ...dotenv.parse(fs.readFileSync(file)), ...process.env };
const frontendOnly = process.argv.includes('--frontend-only');
const local = value => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(value).hostname);
const mongo = config.MONGO_URL || 'mongodb://127.0.0.1:27017';
const posUrl = config.POS_CORE_API_BASE_URL || 'http://127.0.0.1:4001';
if (!local(mongo) || !local(posUrl)) throw new Error('Local development requires local MongoDB and POS URLs. Remote services will not be used.');
if (!config.JWT_SECRET || !config.ADMIN_EMAIL || !config.ADMIN_PASSWORD) {
  throw new Error('Local configuration requires JWT_SECRET, ADMIN_EMAIL and ADMIN_PASSWORD.');
}
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) {
    if (process.platform === 'win32') spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else child.kill('SIGTERM');
  }
  setTimeout(() => process.exit(code), 500).unref();
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
function run(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env: { ...process.env, ...env }, stdio: 'inherit', windowsHide: true });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) { console.error(`A local service exited (${code}); stopping the others.`); stop(code || 1); } });
}
async function free(port) {
  await new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once('error', () => reject(new Error(`Port ${port} is in use. Stop the existing local service before running npm run dev.`)));
    listener.listen(port, '0.0.0.0', () => listener.close(resolve));
  });
}
async function ready(url, timeout = 180000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline && !stopping) {
    try { if ((await fetch(url, { signal: AbortSignal.timeout(2000) })).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`Local service did not become ready: ${url}. No remote fallback was attempted.`);
}
try {
  for (const port of frontendOnly ? [3001] : [3001, 8000]) await free(port);
  console.log('ADMINCORE: http://localhost:3001 (AdminCore API :8000, local MongoDB).');
  if (!frontendOnly) {
    const python = config.PYTHON_PATH || path.join(root, process.platform === 'win32' ? 'backend/.venv/Scripts/python.exe' : 'backend/.venv/bin/python');
    const runtime = pythonRuntime(python, config);
    run(runtime.command, ['-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '8000'], path.join(root, 'backend'), {
      ...config, ...runtime.env, ENVIRONMENT: 'development', APP_ENV: 'development', NODE_ENV: 'development', RENDER: '',
      MONGO_URL: mongo, DB_NAME: config.DB_NAME || 'admincore_local', POS_CORE_API_BASE_URL: posUrl,
      FRONTEND_URL: 'http://localhost:3001', COOKIE_SECURE: 'false', COOKIE_SAMESITE: 'lax',
      CORS_ORIGINS: '', CORS_ORIGIN_REGEX: '', SEED_DEMO_DATA: 'false',
    });
    await ready('http://127.0.0.1:8000/api/ready');
  }
  run(process.execPath, [path.join(root, 'frontend/node_modules/@craco/craco/dist/bin/craco.js'), 'start'], path.join(root, 'frontend'), {
    NODE_ENV: 'development', PORT: '3001', BROWSER: process.env.BROWSER || '', REACT_APP_BACKEND_URL: '', API_PROXY_TARGET: 'http://127.0.0.1:8000',
  });
  await ready('http://127.0.0.1:3001', 600000);
  const page = await fetch('http://127.0.0.1:3001').then(response => response.text());
  if (!/<title>\s*AdminCore\b/i.test(page)) throw new Error('Port 3001 is not serving AdminCore. Check the local server before continuing.');
  console.log('AdminCore ready: http://localhost:3001');
} catch (error) { console.error(error.message); stop(1); }
