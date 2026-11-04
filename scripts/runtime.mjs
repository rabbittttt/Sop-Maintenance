import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const configured = process.env.SOP_PYTHON_PATH;
const venvSuffix = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
const environment = [root, path.dirname(root)].map(directory => path.join(directory, '.venv')).find(existsSync);
const candidate = configured ? path.resolve(root, configured) :
  (environment ? path.join(environment, ...venvSuffix) : (process.platform === 'win32' ? 'python' : 'python3'));
if ((configured || environment) && !existsSync(candidate)) {
  throw new Error(`${configured ? 'SOP_PYTHON_PATH' : '.venv'} points to a missing Python: ${candidate}. Rebuild the environment on this computer.`);
}
const probe = spawnSync(candidate, ['-X', 'utf8', '-c',
  'import json,sys; sys.version_info >= (3,10) or sys.exit("Python 3.10+ is required"); import openpyxl; print(json.dumps({"executable":sys.executable},ensure_ascii=True))'],
{ encoding: 'utf8', windowsHide: true, timeout: 10000 });
if (probe.error || probe.status !== 0) {
  throw new Error(`Python is unavailable or cannot import openpyxl: ${candidate}. Install requirements.txt with this interpreter. ${probe.error?.message || probe.stderr}`);
}

export const PYTHON = JSON.parse(probe.stdout).executable;
