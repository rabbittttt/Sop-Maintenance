"""Shared paths for the editable plan and raw sales workbooks."""

import os
from pathlib import Path


PLAN_WORKBOOK_NAME = "SOP计划维护.xlsx"


def source_directory(root):
    return Path(os.environ["SOP_SOURCE_DIR"]) if os.environ.get("SOP_SOURCE_DIR") else Path(root) / "input_file"


def runtime_data_directory(root):
    return Path(os.environ["SOP_DATA_DIR"]) if os.environ.get("SOP_DATA_DIR") else Path(root) / "data"


def plan_workbook_path(root):
    return Path(root) / "input_file" / PLAN_WORKBOOK_NAME


def sales_source_files(source_dir):
    """Return raw sales files; the editable SOP workbook is not a sales source."""
    return sorted(
        path for path in Path(source_dir).glob("*.xlsx")
        if not path.name.startswith("~$")
        and path.name.casefold() != PLAN_WORKBOOK_NAME.casefold()
        and not path.name.lower().endswith(".pending.xlsx")
    )
