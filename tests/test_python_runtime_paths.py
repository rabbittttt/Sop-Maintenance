"""Verify actual launchers in relocated workspaces, without business data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows launcher")
class PythonRuntimePathsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-python-paths-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "Project 测试"
        self.root = self.workspace / "SOP维护"
        (self.root / "scripts").mkdir(parents=True)
        self.cwd = self.workspace / "unrelated working directory"
        self.cwd.mkdir()
        for name in ("start-sop.ps1", "setup-environment.ps1", "requirements.txt"):
            shutil.copy2(ROOT / name, self.root / name)
        shutil.copy2(ROOT / "scripts/runtime.mjs", self.root / "scripts/runtime.mjs")
        self.env = {key: value for key, value in os.environ.items()
                    if key not in {"SOP_PYTHON_PATH", "PYTHONHOME", "PYTHONPATH"}}
        self.python = self.make_environment(self.workspace)

    @staticmethod
    def make_environment(root, dependency=True):
        directory = root / ".venv"
        venv.EnvBuilder(with_pip=False).create(directory)
        if dependency:
            # The test probes only imports; no spreadsheet is created.
            module = directory / "Lib/site-packages/openpyxl/__init__.py"
            module.parent.mkdir(parents=True)
            module.write_text('__version__ = "3.1.5"\n', encoding="utf-8")
        return directory / "Scripts/python.exe"

    def run_resolvers(self, expected=None, error=None):
        commands = [["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                     str(self.root / "start-sop.ps1"), "-CheckRuntime"]]
        node = shutil.which("node")
        if node:
            probe = self.root / "scripts/probe.mjs"
            probe.write_text("import {PYTHON} from './runtime.mjs'; console.log(PYTHON);", encoding="utf-8")
            commands.append([node, str(probe)])
        for command in commands:
            with self.subTest(launcher=Path(command[0]).name):
                result = subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True,
                                        encoding="utf-8", errors="replace", timeout=25)
                if error:
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(error, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(Path(result.stdout.strip()).resolve(), expected.resolve())

    def test_workspace_environment_from_unrelated_directory(self):
        self.run_resolvers(expected=self.python)
        self.assertFalse((self.root / "data").exists())

    def test_project_environment_takes_priority(self):
        project_python = self.make_environment(self.root)
        self.run_resolvers(expected=project_python)

    def test_relative_override_is_anchored_to_project(self):
        self.make_environment(self.root)
        self.env["SOP_PYTHON_PATH"] = "../.venv/Scripts/python.exe"
        self.run_resolvers(expected=self.python)

    def test_missing_override_does_not_fall_back(self):
        self.env["SOP_PYTHON_PATH"] = "../missing/Scripts/python.exe"
        self.run_resolvers(error="SOP_PYTHON_PATH")

    def test_broken_project_environment_does_not_switch_to_workspace(self):
        self.make_environment(self.root, dependency=False)
        self.run_resolvers(error="requirements.txt")

    def test_incomplete_project_environment_does_not_switch_to_workspace(self):
        (self.root / ".venv").mkdir()
        self.run_resolvers(error=".venv")

    def test_setup_missing_explicit_python_does_not_create_environment(self):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(self.root / "setup-environment.ps1"), "-PythonPath", "missing/python.exe"],
                                cwd=self.cwd, env=self.env, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".venv").exists())

    def test_setup_rebuilds_a_project_environment_using_an_offline_package(self):
        # Exercise venv + pip in a disposable project, using a synthetic wheel.
        # This validates setup flow without contacting a package server.
        wheelhouse = self.workspace / "offline packages"
        wheelhouse.mkdir()
        package = "openpyxl-3.1.5.dist-info"
        files = {
            "openpyxl/__init__.py": '__version__ = "3.1.5"\n',
            f"{package}/METADATA": "Metadata-Version: 2.1\nName: openpyxl\nVersion: 3.1.5\n",
            f"{package}/WHEEL": "Wheel-Version: 1.0\nGenerator: sop-setup-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        files[f"{package}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{package}/RECORD"])
        with zipfile.ZipFile(wheelhouse / "openpyxl-3.1.5-py3-none-any.whl", "w") as wheel:
            for name, contents in files.items():
                wheel.writestr(name, contents)
        environment = {**self.env, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheelhouse.as_uri(),
                       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": os.devnull}
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(self.root / "setup-environment.ps1"), "-PythonPath", sys.executable],
                                cwd=self.cwd, env=environment, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_resolvers(expected=self.root / ".venv/Scripts/python.exe")
        self.assertFalse((self.root / "data").exists())


if __name__ == "__main__":
    unittest.main()
