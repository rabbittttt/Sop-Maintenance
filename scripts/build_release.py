"""Build a source-owned SOP runtime release without business data or credentials.

Run from the authoritative SOP project with Python 3.10+:
    python scripts/build_release.py --destination <new absolute release directory>
The portal consumes this release; it does not carry another SOP source tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


MANIFEST_NAME = "sop-release.json"
ROOT = Path(__file__).resolve().parents[1]
WEB_EXTENSIONS = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".png", ".ico",
                  ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2"}
REQUIRED_FILES = {"server.py", "sop/__init__.py", "sop/gateway.py", "sop/paths.py", "sop/store.py",
                  "requirements.txt", "requirements-intranet.txt", "web/index.html",
                  "integration/iis/SopIdentityBridge.cs"}


def reject_reparse(path: Path) -> None:
    """Check the path and every existing ancestor, including Windows junctions."""
    cursor = path.absolute()
    while True:
        try:
            details = cursor.lstat()
        except FileNotFoundError:
            details = None
        if details is not None and (stat.S_ISLNK(details.st_mode) or
                                    getattr(details, "st_file_attributes", 0) & 0x400):
            raise ValueError(f"Reparse points are not allowed in release inputs/output: {cursor}")
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def allowed_release_path(relative: str) -> bool:
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
        return False
    parts = relative.split("/")
    if any(not part or part.startswith(".") or part in {"data", "input_file", "output_file", "tests", "node_modules", "__pycache__"} for part in parts):
        return False
    path = Path(relative)
    if len(parts) == 1:
        return path.suffix == ".py" or relative in {"requirements.txt", "requirements-intranet.txt", "README.md", "PORTAL-INTEGRATION.md"}
    return ((parts[0] == "web" and path.suffix.lower() in WEB_EXTENSIONS) or
            (len(parts) == 2 and parts[0] == "sop" and path.suffix == ".py") or
            (len(parts) == 2 and parts[0] == "scripts" and path.suffix == ".mjs") or
            (len(parts) == 3 and parts[:2] == ["integration", "iis"] and path.suffix == ".cs"))


def source_files(source: Path) -> list[Path]:
    reject_reparse(source)
    files = [path for path in source.iterdir() if path.is_file() and allowed_release_path(path.name)]
    package = source / "sop"
    if package.is_dir():
        reject_reparse(package)
        # The runtime package is flat. Do not include local state, caches or
        # configuration merely because they happen to be stored beside code.
        for path in package.iterdir():
            reject_reparse(path)
            if path.is_file() and allowed_release_path(path.relative_to(source).as_posix()):
                files.append(path)
    web = source / "web"
    if not web.is_dir():
        raise ValueError("SOP web directory is missing")
    reject_reparse(web)
    for directory, dirs, names in os.walk(web, followlinks=False):
        for name in dirs:
            reject_reparse(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            reject_reparse(path)
            if not allowed_release_path(path.relative_to(source).as_posix()):
                raise ValueError(f"Unexpected non-public file under web/: {path.relative_to(source)}")
            files.append(path)
    scripts = source / "scripts"
    if scripts.is_dir():
        reject_reparse(scripts)
        files.extend(path for path in scripts.iterdir() if path.is_file() and path.suffix == ".mjs")
    iis = source / "integration" / "iis"
    if iis.is_dir():
        reject_reparse(iis)
        files.extend(path for path in iis.iterdir() if path.is_file() and path.suffix == ".cs")
    for path in files:
        reject_reparse(path)
    names = {path.relative_to(source).as_posix() for path in files}
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError("SOP source has not completed gateway integration: " + ", ".join(sorted(missing)))
    return sorted(files, key=lambda path: path.relative_to(source).as_posix())


def git_provenance(source: Path) -> tuple[str | None, bool | None]:
    def git_output(*arguments: str) -> str:
        # Git for Windows emits UTF-8 paths even when Python's preferred text
        # encoding is a legacy code page. Never decode a CJK source root with
        # locale defaults or mistake its parent repository for this project.
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", str(source), *arguments],
            capture_output=True, encoding="utf-8", errors="strict", check=True,
        ).stdout

    try:
        repository = git_output("rev-parse", "--show-toplevel").strip()
        if os.path.normcase(str(Path(repository).resolve())) != os.path.normcase(str(source.resolve())):
            return None, None
        head = git_output("rev-parse", "HEAD").strip()
        dirty = git_output("status", "--porcelain")
        return head, bool(dirty.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None


def build_release(source: Path, destination: Path) -> dict:
    source = source.absolute()
    destination = destination.absolute()
    if not source.is_dir():
        raise ValueError("SOP source root is missing")
    if destination.exists():
        raise ValueError("Destination already exists; choose a new release directory")
    reject_reparse(destination)
    # Resolve without creating anything so '..' cannot put output inside source.
    source = source.resolve()
    destination = destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Destination must be outside the SOP source project")
    files = source_files(source)
    entries = []
    content = []
    for path in files:
        data = path.read_bytes()
        relative = path.relative_to(source).as_posix()
        entries.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        content.append((relative, data))
    commit, dirty = git_provenance(source)
    manifest = {"schemaVersion": 1, "appId": "hima-sop", "sourceCommit": commit,
                "sourceDirty": dirty, "files": entries}
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in content:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).hexdigest() != next(entry["sha256"] for entry in entries if entry["path"] == relative):
            raise OSError("Release copy verification failed: " + relative)
    (destination / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a SOP runtime release from this authoritative source project.")
    parser.add_argument("--destination", type=Path, required=True, help="A new absolute directory outside the source project")
    args = parser.parse_args()
    if not args.destination.is_absolute():
        parser.error("--destination must be absolute")
    try:
        manifest = build_release(ROOT, args.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, "SOP release failed: " + str(error) + "\n")
    print(f"SOP release built: {args.destination} ({len(manifest['files'])} files)")
    print("Business data, tests, secrets, local runtimes and Git metadata were excluded.")


if __name__ == "__main__":
    main()
