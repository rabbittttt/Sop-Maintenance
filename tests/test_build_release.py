"""Release builds preserve source/data and expose only the declared runtime."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("sop_release_builder", Path(__file__).resolve().parents[1] / "scripts" / "build_release.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class BuildReleaseTest(unittest.TestCase):
    def source(self, folder):
        root = Path(folder) / "source"
        for name in BUILDER.REQUIRED_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture runtime " + name, encoding="utf-8")
        (root / "data").mkdir()
        (root / "data" / "sop.sqlite3").write_bytes(b"private database")
        (root / "input_file").mkdir()
        (root / "input_file" / "source.xlsx").write_bytes(b"private workbook")
        (root / ".env").write_text("private secret", encoding="utf-8")
        return root

    def test_manifest_covers_only_runtime_and_preserves_source_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
            output = Path(folder) / "release"
            manifest = BUILDER.build_release(source, output)
            self.assertEqual(manifest["appId"], "hima-sop")
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual({entry["path"] for entry in manifest["files"]}, BUILDER.REQUIRED_FILES)
            for entry in manifest["files"]:
                data = (output / entry["path"]).read_bytes()
                self.assertEqual(entry["bytes"], len(data))
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(json.loads((output / "sop-release.json").read_text())["files"], manifest["files"])
            self.assertFalse((output / "data").exists())
            self.assertFalse((output / "input_file").exists())
            self.assertFalse((output / ".env").exists())
            self.assertEqual(before, {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()})

    def test_existing_destination_and_source_internal_output_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            existing = Path(folder) / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                BUILDER.build_release(source, existing)
            with self.assertRaisesRegex(ValueError, "outside"):
                BUILDER.build_release(source, source / "release")
            self.assertFalse((source / "release").exists())

    def test_package_modules_are_included_without_private_files_or_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            module = source / "sop" / "data.py"
            module.write_text('"""Runtime sales reader."""\n', encoding="utf-8")
            excluded = ("sop/.env", "sop/local-settings.json", "sop/__pycache__/data.cpython-310.pyc",
                        "sop/data/private.xlsx", "sop/tests/fixture.py")
            for name in excluded:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"private local content")
            output = Path(folder) / "release"
            manifest = BUILDER.build_release(source, output)
            names = {entry["path"] for entry in manifest["files"]}
            self.assertEqual(names, BUILDER.REQUIRED_FILES | {"sop/data.py"})
            self.assertEqual((output / "sop" / "data.py").read_bytes(), module.read_bytes())
            for name in excluded:
                self.assertFalse((output / name).exists(), name)

    def test_missing_runtime_package_is_rejected_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            (source / "sop" / "paths.py").unlink()
            output = Path(folder) / "release"
            with self.assertRaisesRegex(ValueError, "sop/paths.py"):
                BUILDER.build_release(source, output)
            self.assertFalse(output.exists())

    def test_package_symlink_is_rejected_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            external = Path(folder) / "external.py"
            external.write_text("private linked source", encoding="utf-8")
            try:
                os.symlink(external, source / "sop" / "linked.py")
            except OSError as error:
                self.skipTest(f"This machine cannot create symlinks: {error}")
            output = Path(folder) / "release"
            with self.assertRaisesRegex(ValueError, "Reparse"):
                BUILDER.build_release(source, output)
            self.assertFalse(output.exists())

    def test_non_public_web_file_fails_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(folder)
            (source / "web" / "secret.env").write_text("secret", encoding="utf-8")
            output = Path(folder) / "release"
            with self.assertRaisesRegex(ValueError, "non-public"):
                BUILDER.build_release(source, output)
            self.assertFalse(output.exists())

    def test_path_contract_blocks_private_and_traversal_names(self):
        for value in ("../server.py", "web/../server.py", "web/.env", "web/data/input.json", "data/sales.json", "scripts/node_modules/x.mjs", "C:/server.py", "web\\index.html",
                      "sop/.env", "sop/../server.py", "sop/__pycache__/store.pyc", "sop/data/private.py", "sop/tests/fixture.py"):
            with self.subTest(path=value):
                self.assertFalse(BUILDER.allowed_release_path(value))

    @unittest.skipUnless(shutil.which("git"), "Git is required for source provenance integration")
    def test_real_git_cjk_path_records_clean_and_dirty_source_without_parent_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self.source(Path(folder) / "计划部 临时项目")

            def git(*arguments):
                return subprocess.run(
                    ["git", "-c", "core.quotepath=false", "-c", "core.autocrlf=false",
                     "-c", "user.name=SOP Release Test", "-c", "user.email=release-test@example.invalid",
                     "-c", "commit.gpgsign=false", "-C", str(source), *arguments],
                    capture_output=True, encoding="utf-8", errors="strict", check=True,
                ).stdout.strip()

            git("init", "-b", "main")
            git("add", ".")
            git("commit", "-m", "Local release provenance fixture")
            expected = git("rev-parse", "HEAD")
            clean = BUILDER.build_release(source, Path(folder) / "clean-release")
            self.assertEqual(clean["sourceCommit"], expected)
            self.assertIs(clean["sourceDirty"], False)

            # A non-repository subdirectory must not inherit this parent's SHA.
            nested = source / "nested-source"
            nested.mkdir()
            self.assertEqual(BUILDER.git_provenance(nested), (None, None))

            server = source / "server.py"
            server.write_text(server.read_text(encoding="utf-8") + "\nmodified", encoding="utf-8")
            dirty = BUILDER.build_release(source, Path(folder) / "dirty-release")
            self.assertEqual(dirty["sourceCommit"], expected)
            self.assertIs(dirty["sourceDirty"], True)


if __name__ == "__main__":
    unittest.main()
