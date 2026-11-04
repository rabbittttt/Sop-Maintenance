import { existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const configured = process.env.SOP_PYTHON_PATH;
const venvSuffix = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
let workspace = path.resolve(root);
if (!configured) {
  while (path.basename(workspace).toLowerCase() !== 'project') {
    const parent = path.dirname(workspace);
    if (parent === workspace) throw new Error('Project workspace not found. Place the project below Project and initialize Project/.venv.');
    workspace = parent;
  }
}
const candidate = configured ? path.resolve(root, configured) : path.join(workspace, '.venv', ...venvSuffix);
if (!existsSync(candidate)) {
  throw new Error(`${configured ? 'SOP_PYTHON_PATH' : 'Project/.venv'} points to a missing Python: ${candidate}. Rebuild the shared environment on this computer.`);
}
const probe = spawnSync(candidate, ['-X', 'utf8', '-c',
  'import json,sys; sys.version_info >= (3,10) or sys.exit("Python 3.10+ is required"); import openpyxl; print(json.dumps({"executable":sys.executable},ensure_ascii=True))'],
{ encoding: 'utf8', windowsHide: true, timeout: 10000 });
if (probe.error || probe.status !== 0) {
  throw new Error(`Python is unavailable or cannot import openpyxl: ${candidate}. Install requirements.txt with this interpreter. ${probe.error?.message || probe.stderr}`);
}

export const PYTHON = JSON.parse(probe.stdout).executable;
const pythonDirectory = path.dirname(PYTHON);
process.env.PATH = pythonDirectory + path.delimiter + (process.env.PATH || '');
const selectedEnvironment = path.dirname(pythonDirectory);
if (existsSync(path.join(selectedEnvironment, 'pyvenv.cfg'))) process.env.VIRTUAL_ENV = selectedEnvironment;
else delete process.env.VIRTUAL_ENV;
