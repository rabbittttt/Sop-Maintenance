"""Exercise one-click startup and offline setup in disposable Project workspaces."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows batch launcher")
class StartupBootstrapTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-startup-bootstrap-")

        def cleanup():
            for attempt in range(40):
                try:
                    temporary.cleanup()
                    return
                except PermissionError:
                    if attempt == 39:
                        raise
                    time.sleep(.05)

        self.addCleanup(cleanup)
        self.workspace = Path(temporary.name).resolve() / "迁移目录 & 新电脑" / "Project"
        self.root = self.workspace / "SOP维护"
        self.root.mkdir(parents=True)
        self.cwd = self.workspace / "其他工作目录"
        self.cwd.mkdir()
        for name in ("启动工作台.cmd", "start-sop.ps1", "setup-environment.ps1", "requirements.txt"):
            shutil.copy2(ROOT / name, self.root / name)
        # Record setup entry while exercising the real copied installer unchanged.
        setup = self.root / "setup-environment.ps1"
        source = setup.read_text(encoding="utf-8-sig")
        source = source.replace("param([string]$PythonPath)",
                                "param([string]$PythonPath)\n"
                                "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'setup-invoked'), '1')", 1)
        setup.write_text(source, encoding="utf-8-sig")
        self.started = self.root / "server-ran.json"
        (self.root / "server.py").write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "body={'executable':sys.executable,'prefix':sys.prefix,"
            "'path':os.environ['PATH'].split(os.pathsep)[0],'venv':os.environ.get('VIRTUAL_ENV')}\n"
            "Path(__file__).with_name('server-ran.json').write_text(json.dumps(body),encoding='utf-8')\n"
            "print(json.dumps(body))\n", encoding="utf-8")
        self.python = self.workspace / ".venv/Scripts/python.exe"
        self.wheelhouse = self.workspace / "离线 packages"
        self.wheelhouse.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"SOP_PYTHON_PATH", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}}
        self.env.update({
            "PATH": str(Path(sys.executable).parent) + os.pathsep + self.env.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": self.wheelhouse.as_uri(),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": os.devnull,
        })
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]

    def make_environment(self, root=None, dependency=True):
        directory = (root or self.workspace) / ".venv"
        venv.EnvBuilder(with_pip=False).create(directory)
        if dependency:
            module = directory / "Lib/site-packages/openpyxl/__init__.py"
            module.parent.mkdir(parents=True)
            module.write_text('__version__ = "3.1.5"\n', encoding="utf-8")
        return directory / "Scripts/python.exe"

    def add_offline_package(self):
        package = "openpyxl-3.1.5.dist-info"
        files = {
            "openpyxl/__init__.py": '__version__ = "3.1.5"\n',
            f"{package}/METADATA": "Metadata-Version: 2.1\nName: openpyxl\nVersion: 3.1.5\n",
            f"{package}/WHEEL": "Wheel-Version: 1.0\nGenerator: sop-startup-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        files[f"{package}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{package}/RECORD"])
        with zipfile.ZipFile(self.wheelhouse / "openpyxl-3.1.5-py3-none-any.whl", "w") as wheel:
            for name, contents in files.items():
                wheel.writestr(name, contents)

    def launch(self, *arguments):
        batch = self.root / "启动工作台.cmd"
        command = ('"' + os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe") +
                   '" /d /s /c ""' + str(batch) + '" -NoBrowser -Port ' + str(self.port) +
                   ' ' + ' '.join(arguments) + '"')
        return subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=75,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def assert_started_with_shared_environment(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.started.exists(), result.stdout + result.stderr)
        child = json.loads(self.started.read_text(encoding="utf-8"))
        self.assertEqual(Path(child["executable"]).resolve(), self.python.resolve())
        self.assertEqual(Path(child["prefix"]).resolve(), (self.workspace / ".venv").resolve())
        self.assertEqual(Path(child["path"]).resolve(), self.python.parent.resolve())
        self.assertEqual(Path(child["venv"]).resolve(), (self.workspace / ".venv").resolve())
        self.assertFalse((self.root / ".venv").exists())
        self.assertFalse((self.root / "data").exists())

    def test_healthy_environment_starts_without_setup(self):
        self.make_environment()
        self.assert_started_with_shared_environment(self.launch())
        self.assertFalse((self.root / "setup-invoked").exists())
        self.assertFalse((self.workspace / ".venv/Lib/site-packages/pip").exists())

    def test_missing_environment_is_created_offline_then_starts(self):
        self.add_offline_package()
        # A project-local Python must not become the chosen runtime or install target.
        self.make_environment(self.root, dependency=False)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        child = json.loads(self.started.read_text(encoding="utf-8"))
        self.assertEqual(Path(child["executable"]).resolve(), self.python.resolve())
        self.assertTrue((self.root / "setup-invoked").exists())
        self.assertTrue((self.workspace / ".venv/Lib/site-packages/openpyxl/__init__.py").exists())
        self.assertFalse((self.root / ".venv/Lib/site-packages/openpyxl/__init__.py").exists())
        self.assertFalse((self.root / "data").exists())

    def test_missing_dependency_is_installed_offline_then_starts(self):
        self.make_environment(dependency=False)
        self.add_offline_package()
        self.assert_started_with_shared_environment(self.launch())
        self.assertTrue((self.root / "setup-invoked").exists())
        self.assertTrue((self.workspace / ".venv/Lib/site-packages/openpyxl/__init__.py").exists())

    def test_failed_dependency_install_does_not_start_server(self):
        self.make_environment(dependency=False)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "setup-invoked").exists())
        self.assertFalse(self.started.exists())
        self.assertFalse((self.root / "data").exists())
        self.assertTrue(self.python.exists())

    def test_successful_setup_is_rechecked_before_server_start(self):
        self.make_environment(dependency=False)
        (self.root / "setup-environment.ps1").write_text(
            "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'setup-invoked'), '1')\n",
            encoding="utf-8-sig")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "setup-invoked").exists())
        self.assertIn("环境初始化后检查仍未通过", result.stdout + result.stderr)
        self.assertFalse(self.started.exists())

    def test_missing_venv_configuration_is_not_replaced_or_used(self):
        self.make_environment()
        configuration = self.workspace / ".venv/pyvenv.cfg"
        configuration.unlink()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.python.exists())
        self.assertFalse(configuration.exists())
        self.assertFalse(self.started.exists())

    def test_check_runtime_does_not_create_missing_environment(self):
        self.add_offline_package()
        result = self.launch("-CheckRuntime")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "setup-invoked").exists())
        self.assertFalse((self.workspace / ".venv").exists())
        self.assertFalse(self.started.exists())

    def test_invalid_explicit_override_does_not_initialize_shared_environment(self):
        self.env["SOP_PYTHON_PATH"] = "../missing/python.exe"
        self.add_offline_package()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SOP_PYTHON_PATH", result.stdout + result.stderr)
        self.assertFalse((self.root / "setup-invoked").exists())
        self.assertFalse((self.workspace / ".venv").exists())
        self.assertFalse(self.started.exists())

    def test_explicit_override_missing_dependency_is_not_modified(self):
        selected = self.make_environment(self.workspace / "explicit", dependency=False)
        self.env["SOP_PYTHON_PATH"] = str(selected)
        self.add_offline_package()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "setup-invoked").exists())
        self.assertFalse((selected.parent.parent / "Lib/site-packages/openpyxl").exists())
        self.assertFalse((self.workspace / ".venv").exists())
        self.assertFalse(self.started.exists())

    def test_incomplete_shared_environment_is_preserved(self):
        shared = self.workspace / ".venv"
        shared.mkdir()
        sentinel = shared / "keep-existing-environment.txt"
        sentinel.write_text("Existing environment must be preserved.", encoding="utf-8")
        self.add_offline_package()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "Existing environment must be preserved.")
        self.assertFalse(self.python.exists())
        self.assertFalse(self.started.exists())


if __name__ == "__main__":
    unittest.main()
