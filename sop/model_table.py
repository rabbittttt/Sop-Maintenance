"""Read ordinary model/month plan tables without trusting hidden identifiers."""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

from openpyxl.utils import get_column_letter

from .plan_scope import MODEL_PLAN_LABEL
from .store import ValidationError, catalog_name_key, text_field
from .table import _VERTICAL_LABEL_MERGE, _label_merges, _quantity

MAX_ROWS = 15000
MAX_COLUMNS = 200
MAX_CELLS = 1_500_000
MAX_ITEMS = 20000
MAX_MONTHS = 120
_YEAR = re.compile(r"(20\d{2})(?:年)?$")
_FULL_MONTH = re.compile(r"(20\d{2})-(\d{2})$|(?P<year>20\d{2})年(?P<month>\d{1,2})月$")
_MONTH = re.compile(r"(\d{1,2})月$")
_MODEL_YEAR = re.compile(r"(?<!\d)20\d{2}\s*款(?!\d)")
_VERSION_HEADERS = {"版本", "版本名称", "车型版本", "配置", "配置名称", "配置版本", "版本明细"}
_TOTAL_HEADERS = {"年度合计", "期间合计", "年度计划量", "年度计划", "合计", "小计", "总计"}


def _token(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _full_month(value):
    match = _FULL_MONTH.fullmatch(_token(value))
    if match is None:
        return None
    year, month = (match.group(1), match.group(2)) if match.group(1) else (match.group("year"), match.group("month"))
    if not 1 <= int(month) <= 12:
        raise ValidationError("表头月份须为1至12月，请使用明确的YYYY-MM年月。")
    return f"{year}-{int(month):02d}"


def _month_number(value):
    match = _MONTH.fullmatch(_token(value))
    if match is None:
        return None
    number = int(match.group(1))
    if not 1 <= number <= 12:
        raise ValidationError("表头月份须为1至12月。")
    return number


def _early_label_merges(sheet, first_data_row):
    """The shared maintenance-table helper deliberately begins at row five."""
    if first_data_row >= 5:
        return
    if hasattr(sheet, "merged_cells"):
        references = (str(area) for area in sheet.merged_cells.ranges)
    else:
        archive, path = sheet.parent._archive, sheet._worksheet_path
        if archive.getinfo(path).file_size > 96 * 1024 * 1024:
            raise ValidationError("主表文件过大，请缩小导入范围。")
        def references_from_xml():
            count = 0
            with archive.open(path) as source:
                for _, element in ET.iterparse(source, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] == "mergeCell":
                        count += 1
                        if count > 30000:
                            raise ValidationError("主表合并区域过多，请缩小导入范围。")
                        yield element.attrib.get("ref", "")
                    element.clear()
        references = references_from_xml()
    for reference in references:
        match = _VERTICAL_LABEL_MERGE.fullmatch(reference)
        if match and first_data_row <= int(match.group(2)) < 5 and int(match.group(3)) > int(match.group(2)):
            yield (1 if match.group(1) == "A" else 2), int(match.group(2)), int(match.group(3))


def parse_model_table(sheet):
    """Return model-level plan entries from a visible, unmarked main table.

    Empty month cells have no update meaning. No row absence, subtotal, version
    label or hidden product identifier can deactivate or select a stored item.
    """
    if ((sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS
            or (sheet.max_row or 0) * (sheet.max_column or 0) > MAX_CELLS):
        raise ValidationError("车型计划表范围过大，请最多保留15000行、200列并缩小导入范围。")
    rows, size = [], 0
    for row in sheet.iter_rows(values_only=False):
        size += len(row)
        if len(rows) >= MAX_ROWS or len(row) > MAX_COLUMNS or size > MAX_CELLS:
            raise ValidationError("车型计划表范围过大，请缩小导入范围。")
        rows.append(row)

    def cell(number, column):
        return rows[number - 1][column - 1] if 1 <= number <= len(rows) and column <= len(rows[number - 1]) else None

    def value(number, column):
        item = cell(number, column)
        return item.value if item is not None else None

    header, brand_column, model_column = None, None, None
    for number, row in enumerate(rows[:20], 1):
        labels = [_token(item.value) for item in row]
        if "品牌" in labels and "车型" in labels:
            if labels.count("品牌") != 1 or labels.count("车型") != 1:
                raise ValidationError("主表品牌或车型列重复，请只保留一组主表表头。")
            header, brand_column, model_column = number, labels.index("品牌") + 1, labels.index("车型") + 1
            break
    if header is None:
        raise ValidationError("未找到车型计划主表，请保留“品牌”“车型”和明确的月份表头。")
    width = max((len(row) for row in rows), default=0)
    top = [_token(value(header, c)) for c in range(1, width + 1)]
    version_columns = [index + 1 for index, label in enumerate(top) if label in _VERSION_HEADERS]
    if len(version_columns) > 1:
        raise ValidationError("版本列重复，无法区分车型总量与版本明细，请只保留一列版本说明。")
    version_column = version_columns[0] if version_columns else None
    fields = {label: index + 1 for index, label in enumerate(top) if label in {"指标", "层级"}}
    identity = {brand_column, model_column, *fields.values()}
    if version_column is not None:
        identity.add(version_column)
    others = [c for c in range(1, width + 1) if c not in identity]
    # A string such as "2027-01" in a quantity cell is invalid data, not a
    # second header when the first row already declares explicit months.
    explicit_top = any(_FULL_MONTH.fullmatch(top[c - 1]) for c in others)
    second_header = any(_YEAR.fullmatch(top[c - 1]) for c in others) or (not explicit_top and any(
        _MONTH.fullmatch(_token(value(header + 1, c))) or _FULL_MONTH.fullmatch(_token(value(header + 1, c))) for c in others))
    month_header = header + 1 if second_header else header
    months, seen_months, year, previous = [], set(), None, None
    for column in others:
        anchor = _YEAR.fullmatch(top[column - 1]) if second_header else None
        if anchor:
            year = anchor.group(1)
        raw = value(month_header, column)
        if _token(raw) in _TOTAL_HEADERS:
            continue
        month = _full_month(raw)
        number = _month_number(raw) if month is None else None
        if number is not None:
            if year is None:
                raise ValidationError("月份表头缺少年份，不能确认年月；请在月份上方填写完整年份，或使用YYYY-MM表头。")
            if previous and previous[0] == year and number <= previous[1]:
                raise ValidationError("月份顺序或跨年年份不明确，请为每个年份保留完整的年份表头。")
            month = f"{year}-{number:02d}"
            previous = year, number
        if month is None:
            continue
        if month in seen_months:
            raise ValidationError(f"月份表头重复：{month}，请每个月只保留一列。")
        seen_months.add(month); months.append((column, month))
    if not months:
        raise ValidationError("未找到可确认年月的月份列；请保留年份和1月至12月表头，或使用YYYY-MM表头。")
    if len(months) > MAX_MONTHS:
        raise ValidationError("一次最多导入120个月，请缩小月份范围。")

    merged = {}
    label_ranges = [*_label_merges(sheet), *_early_label_merges(sheet, month_header + 1)]
    for column, start, end in label_ranges:
        if column not in {brand_column, model_column} or start <= month_header or start > len(rows):
            continue
        anchor = cell(start, column)
        for number in range(start, min(end, len(rows)) + 1):
            merged[number, column] = anchor

    def label(number, column):
        item = cell(number, column)
        return merged.get((number, column)) if item is None or item.value in (None, "") else item

    def model_name(number):
        brand_cell, model_cell = label(number, brand_column), label(number, model_column)
        if (brand_cell is None or model_cell is None or brand_cell.data_type == "f" or model_cell.data_type == "f"
                or not isinstance(brand_cell.value, str) or not isinstance(model_cell.value, str)):
            return None
        model = re.sub(r"\s+合计$", "", model_cell.value.strip()).strip()
        if not _MODEL_YEAR.search(model):
            return None
        return catalog_name_key({"brand": brand_cell.value, "model": model, "version": MODEL_PLAN_LABEL})

    # Full maintenance tables include a raw model input plus an automatically
    # computed model subtotal. Only the input can be imported. A version-only
    # table's automatic subtotals must never manufacture model overrides either.
    model_inputs, version_groups = set(), set()
    for number in range(month_header + 1, len(rows) + 1):
        version = _token(value(number, version_column)) if version_column is not None else ""
        level = _token(value(number, fields["层级"])) if "层级" in fields else ""
        name = model_name(number)
        if name is None or "锁单" in version:
            continue
        if version == _token(MODEL_PLAN_LABEL):
            model_inputs.add(name)
        elif version or level == "版本明细":
            version_groups.add(name)

    entries, seen_items, count, had_version_rows = {}, set(), 0, False
    for number in range(month_header + 1, len(rows) + 1):
        version_cell = cell(number, version_column) if version_column is not None else None
        if version_cell is not None and version_cell.data_type == "f":
            raise ValidationError(f"第{number}行版本说明不能填写公式，请保留车型总量标签或留空。")
        version = _token(version_cell.value) if version_cell is not None else ""
        is_model_input = version == _token(MODEL_PLAN_LABEL)
        level = _token(value(number, fields["层级"])) if "层级" in fields else ""
        if "锁单" in version:
            continue
        if (version and not is_model_input) or (level == "版本明细" and not is_model_input):
            had_version_rows = True
            continue
        labels = {}
        for field, title, column in (("brand", "品牌", brand_column), ("model", "车型", model_column)):
            item = label(number, column)
            if item is not None and item.data_type == "f":
                raise ValidationError(f"第{number}行{title}不能填写公式。")
            labels[field] = text_field(item.value if item is not None else None, title, 200)
        brand, model = labels["brand"], labels["model"]
        level = _token(value(number, fields["层级"])) if "层级" in fields else ""
        metric = _token(value(number, fields["指标"])) if "指标" in fields else ""
        if "锁单" in model or "锁单" in metric:
            continue
        if (not _MODEL_YEAR.search(model) and re.search(r"(?:合计|小计|总计)$", _token(model))
                or (not model and re.search(r"(?:合计|小计|总计)$", _token(brand)))
                or level in {"品牌汇总", "品牌合计", "总计"}):
            continue
        automatic_subtotal = (not is_model_input and bool(re.search(r"\s+合计$", model))
                              and model_name(number) in model_inputs | version_groups)
        amounts = [(column, month, cell(number, column)) for column, month in months]
        if not any(item is not None and item.value not in (None, "") for _, _, item in amounts):
            if model and not _MODEL_YEAR.search(model):
                raise ValidationError(f"第{number}行车型缺少年款，请填写如“新车型 2027款”，不能自动猜测。")
            continue
        if not brand or not model:
            raise ValidationError(f"第{number}行请完整填写品牌和带年款的车型；普通空白单元格不会沿用上一行名称。")
        if metric and metric not in {"SOP计划量", "SOP计划", "计划量"}:
            raise ValidationError(f"第{number}行指标不是SOP计划量，请只保留车型月计划行。")
        model = re.sub(r"\s+合计$", "", model).strip()
        if not _MODEL_YEAR.search(model):
            raise ValidationError(f"第{number}行车型缺少年款，请填写如“新车型 2027款”，不能自动猜测。")
        labels = {"brand": brand, "model": model, "version": MODEL_PLAN_LABEL}
        name = catalog_name_key(labels)
        for column, month, item in amounts:
            if item is None or item.value in (None, ""):
                continue
            address = f"{get_column_letter(column)}{number}"
            if item.data_type == "f":
                if automatic_subtotal:
                    continue
                raise ValidationError(f"{address}的计划数量不能填写公式，请粘贴非负整数数值。")
            try:
                amount = _quantity(item.value)
            except ValidationError as error:
                raise ValidationError(f"{address}：{error}") from error
            if (name, month) in seen_items:
                raise ValidationError(f"第{number}行重复填写同一品牌、车型年款和月份：{brand} {model} {month}。")
            seen_items.add((name, month)); count += 1
            if count > MAX_ITEMS:
                raise ValidationError("一次最多导入20000项月计划，请缩小导入范围。")
            entries.setdefault(name, {**labels, "plans": []})["plans"].append({"month": month, "sop": amount, "cell": address})
    if not entries:
        if had_version_rows:
            raise ValidationError("普通表只导入车型总量，未找到已填写的车型月计划。版本列可以留空，或在“车型总量（不分版本）”行填写；带标识原表可维护版本明细。")
        raise ValidationError("表中没有可导入的车型月计划量；空白月份不会覆盖已有计划，请至少填写一个非负整数（0可填写）。")
    return list(entries.values())
