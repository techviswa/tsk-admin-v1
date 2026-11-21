import { spawnSync } from 'node:child_process';

// Windows' venv python.exe is a redirector. Track the interpreter itself so an
// exited redirector cannot leave the launcher tracking the wrong process.
export function pythonRuntime(launcher, env = process.env) {
  if (process.platform !== 'win32') return { command: launcher, env };
  const probe = spawnSync(launcher, ['-c', 'import json,sys,sysconfig;print(json.dumps({"executable":sys._base_executable,"packages":sysconfig.get_path("purelib")}))'],
    { env, encoding: 'utf8', windowsHide: true, timeout: 15000 });
  if (probe.status !== 0) throw new Error('Could not resolve the configured Python environment. Check PYTHON_PATH and backend dependencies.');
  const runtime = JSON.parse(probe.stdout.trim());
  if (!runtime.executable || !runtime.packages) throw new Error('Invalid Python environment');
  return { command: runtime.executable, env: { ...env, PYTHONPATH: [runtime.packages, env.PYTHONPATH].filter(Boolean).join(';') } };
}
