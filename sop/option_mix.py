"""Read the source workbook's displayed near-28-day option proportions.

These are marginal option distributions, separate from the SOP plan ledger and
from the monthly lock quantities. No daily shares or member generations are
combined to manufacture a different proportion.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import math
from pathlib import Path
import re
import threading
from zipfile import BadZipFile

import openpyxl

from .paths import PROJECT_ROOT, source_directory
from .source_rules import load_merge_rules, normalize_generation


SOURCE_NAME = "鸿蒙智行锁单选配比例分析.xlsx"
WINDOW_DAYS = 28
# These are source dimension names, never product or configuration labels. New
# dimensions stay unclassified until their business meaning is reviewed.
VEHICLE_OPTION_DIMENSIONS = frozenset({"动力", "电池包", "版本", "外观", "内饰", "轮毂"})
NON_VEHICLE_DIMENSIONS = frozenset({"门店类型", "地理区域"})
_CACHE_LOCK = threading.RLock()
_LAST_GOOD = {}


class OptionMixError(Exception):
    """The optional source cannot currently provide option proportions."""


def _text(value):
    return str(value).strip() if value is not None else ""


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    match = re.fullmatch(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", _text(value))
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            pass
    return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _share(value):
    number = _number(value)
    return float(number) if number is not None and 0 <= number <= 1 else None


def _share_display(share, number_format):
    if share is None:
        return None
    match = re.search(r"0(?:\.(0+))?%", number_format or "")
    digits = len(match.group(1) or "") if match else 2
    amount = (Decimal(str(share)) * 100).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{amount:.{digits}f}%"


def _source_sheet(sheet):
    iterator = iter(sheet.iter_rows())
    header = next(iterator, ())
    if tuple(_text(cell.value) for cell in header[:3]) != ("指标", "统计类型", "分类"):
        return {"error": "表头不是指标／统计类型／分类。", "rows": [], "observed_dates": []}
    near_columns = [i for i, cell in enumerate(header) if re.fullmatch(r"近\s*28\s*天", _text(cell.value))]
    if len(near_columns) != 1:
        return {"error": "缺少唯一的“近28天”列。", "rows": [], "observed_dates": []}
    near = near_columns[0]
    day_columns = [(i, day) for i, cell in enumerate(header) if (day := _date(cell.value)) is not None]
    observed_dates = set()
    entries = {}
    dimension = statistic = ""
    for row_number, cells in enumerate(iterator, 2):
        if len(cells) < 3:
            continue
        if _text(cells[0].value):
            dimension = _text(cells[0].value)
        if _text(cells[1].value):
            statistic = _text(cells[1].value)
        option = _text(cells[2].value)
        if statistic not in {"占比", "数量"} or not option or option == "数量":
            continue
        slot = entries.setdefault((dimension, option), {"dimension": dimension, "option": option,
                                                         "share": None, "share_display": None,
                                                         "count": None, "source_cell": None,
                                                         "source_error": None})
        source_cell = cells[near] if near < len(cells) else None
        value = source_cell.value if source_cell is not None else None
        if statistic == "占比":
            slot["share"] = _share(value)
            slot["share_display"] = _share_display(slot["share"], source_cell.number_format if source_cell is not None else "")
            slot["source_cell"] = f"{openpyxl.utils.get_column_letter(near + 1)}{row_number}"
            if slot["share"] is None:
                slot["source_error"] = ("近28天占比为空或公式结果未保存。" if value is None else
                                        "近28天占比必须是 0%—100% 的有限数值。")
        else:
            count = _number(value)
            slot["count"] = count if count is not None and count >= 0 else None
        for col, day in day_columns:
            if col < len(cells) and _number(cells[col].value) is not None:
                observed_dates.add(day)
    # Quantity-only blocks are not option proportions. Counts are annotations;
    # they must never replace or recalculate the source's percentage cell.
    rows = [row for row in entries.values() if row["source_cell"] is not None]
    dimension_counts = {}
    for row in rows:
        dimension_counts.setdefault(row["dimension"], []).append(row["count"])
    for row in rows:
        counts = dimension_counts[row["dimension"]]
        row["sample_count"] = sum(counts) if counts and all(v is not None for v in counts) else None
    latest = max(observed_dates, default=None)
    coverage = sum(day >= latest - timedelta(days=WINDOW_DAYS - 1) for day in observed_dates) if latest else 0
    return {"error": None, "rows": rows, "latest_date": latest.isoformat() if latest else None,
            "covered_days": coverage, "observed_dates": [day.isoformat() for day in sorted(observed_dates)]}


@lru_cache(maxsize=4)
def _read_cached(path_string, size, modified_ns, changed_ns):
    path = Path(path_string)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = {}
        for sheet in workbook.worksheets:
            if not sheet.title.endswith("by天"):
                continue
            generation = sheet.title[:-3].strip()
            if " " not in generation:  # The workbook also has brand and group totals.
                continue
            sheets[generation] = {"source_sheet": sheet.title, **_source_sheet(sheet)}
        if not sheets:
            raise OptionMixError("锁单选配表没有可识别的车型代际 by天 工作表。")
        return sheets
    finally:
        workbook.close()


def _source_rows(path):
    identity = str(path.resolve())
    with _CACHE_LOCK:
        try:
            stat = path.stat()
            cache_key = (identity, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            sheets = _read_cached(*cache_key)
            after = path.stat()
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != cache_key[1:]:
                _read_cached.cache_clear()
                raise OptionMixError("选配文件正在更新，请稍后重试。")
            _LAST_GOOD[identity] = sheets
            return sheets, "ready", None
        except (OSError, ValueError, KeyError, BadZipFile, OptionMixError) as error:
            if identity in _LAST_GOOD:
                return _LAST_GOOD[identity], "stale", f"选配源文件暂不可读取，展示上次有效结果：{error}"
            raise OptionMixError(f"无法读取锁单选配表：{error}") from error


def read_option_mix(source_dir=None):
    """Return original displayed shares and dynamically map summary source names."""
    path = Path(source_dir) / SOURCE_NAME if source_dir is not None else source_directory(PROJECT_ROOT) / SOURCE_NAME
    sheets, status, error = _source_rows(path)
    rules = load_merge_rules()  # Shared generation rules can change without restarting the service.
    groups = rules["groups"]
    members = {normalize_generation(member) for values in groups.values() for member in values}
    alias_to_summary = {}
    for summary in groups:
        normalized = normalize_generation(summary)
        alias_to_summary[normalized] = summary
        short = normalized.removesuffix("汇总").strip()
        if short not in members:
            alias_to_summary[short] = summary
    sources = {}
    for raw_generation, sheet in sheets.items():
        normalized = normalize_generation(raw_generation)
        summary = alias_to_summary.get(normalized)
        generation = summary or raw_generation
        priority = 1 if summary and normalized == normalize_generation(summary) else 0
        if generation not in sources or priority > sources[generation][0]:
            sources[generation] = (priority, raw_generation, sheet)
    rows, missing_summaries, issues, unclassified_dimensions = [], [], [], set()
    source_coverage = []
    for summary in groups:
        chosen = sources.get(summary)
        if chosen is None:
            missing_summaries.append({"generation": summary, "error": "原始锁单选配表缺少该汇总代际自身的 by天 比例。"})
        elif chosen[2]["error"] or not any(row["share"] is not None for row in chosen[2]["rows"]):
            missing_summaries.append({"generation": summary, "error": chosen[2]["error"] or "汇总代际近28天占比没有可读取数值。"})
    for generation, (_, raw_generation, sheet) in sources.items():
        if sheet["error"]:
            issues.append({"generation": generation, "error": sheet["error"]})
            continue
        if generation in groups and any(item["generation"] == generation for item in missing_summaries):
            continue
        if sheet["latest_date"]:
            source_coverage.append((sheet["latest_date"], sheet["covered_days"]))
        brand, _, model = generation.partition(" ")
        for row in sheet["rows"]:
            dimension = row["dimension"]
            classified = True if dimension in VEHICLE_OPTION_DIMENSIONS else False if dimension in NON_VEHICLE_DIMENSIONS else None
            if classified is None:
                unclassified_dimensions.add(dimension)
            if row["source_error"]:
                issues.append({"generation": generation, "source_sheet": sheet["source_sheet"],
                               "source_cell": row["source_cell"], "dimension": dimension,
                               "option": row["option"], "error": row["source_error"]})
            rows.append({"generation": generation, "normalized_generation": normalize_generation(generation),
                         "source_generation": raw_generation,
                         "brand": brand, "model": model,
                         "source_sheet": sheet["source_sheet"], "latest_date": sheet["latest_date"],
                         "covered_days": sheet["covered_days"], "is_vehicle_option": classified, **row})
    latest = max((day for day, _ in source_coverage), default=None)
    # A union of model dates could overstate coverage for every single model.
    # Keep per-row coverage authoritative and make the global badge conservative.
    covered_days = min((days for day, days in source_coverage if day == latest), default=0)
    return {"source_file": SOURCE_NAME, "status": status, "error": error,
            "latest_date": latest, "covered_days": covered_days, "window_days": WINDOW_DAYS,
            "merge_groups": groups, "missing_summaries": missing_summaries,
            "unclassified_dimensions": sorted(unclassified_dimensions),
            "issues": issues, "rows": rows}
