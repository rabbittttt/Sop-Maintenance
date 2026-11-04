"""Shared paths for the editable plan and raw sales workbooks."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_WORKBOOK_NAME = "SOP计划维护.xlsx"


def project_path(root, value):
    """Resolve relative configuration against the project, not the launch cwd."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path(root).resolve() / path).resolve()


def _shared_data_root(root):
    if os.environ.get("SHARED_DATA_ROOT"):
        return project_path(root, os.environ["SHARED_DATA_ROOT"])
    shared_root = Path(root).resolve().parent / "共享数据"
    if shared_root.exists():
        if not shared_root.is_dir():
            raise NotADirectoryError(f"共享数据根路径不是目录：{shared_root}")
        return shared_root
    return None


def source_directory(root):
    if os.environ.get("SOP_SOURCE_DIR"):
        return project_path(root, os.environ["SOP_SOURCE_DIR"])
    shared_root = _shared_data_root(root)
    return shared_root / "订单汇总" if shared_root is not None else Path(root) / "input_file"


def runtime_data_directory(root):
    return project_path(root, os.environ["SOP_DATA_DIR"]) if os.environ.get("SOP_DATA_DIR") else Path(root) / "data"


def plan_workbook_path(root):
    if os.environ.get("SOP_PLAN_DIR"):
        return project_path(root, os.environ["SOP_PLAN_DIR"]) / PLAN_WORKBOOK_NAME
    shared_root = _shared_data_root(root)
    directory = shared_root / "SOP计划" if shared_root is not None else Path(root) / "input_file"
    return directory / PLAN_WORKBOOK_NAME


def sales_source_files(source_dir):
    """Return raw sales files; the editable SOP workbook is not a sales source."""
    return sorted(
        path for path in Path(source_dir).glob("*.xlsx")
        if not path.name.startswith("~$")
        and path.name.casefold() != PLAN_WORKBOOK_NAME.casefold()
        and not path.name.lower().endswith(".pending.xlsx")
    )
