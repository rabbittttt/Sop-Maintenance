"""Read an editable SOP table by row identity, independent of row coordinates."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime

from .store import ValidationError, catalog_name_key, display_version, text_field
from .plan_scope import MODEL_PLAN_LABEL, is_model_plan


def _quantity(value):
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        # Excel can preserve a manually entered integer as a text cell. Accept
        # only decimal digits, without evaluating formulas or parsing dates.
        digits = value.strip().lstrip("0") or "0"
        if len(digits) <= 13:
            value = int(digits)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 10**12 or int(value) != value:
        raise ValidationError("SOP 数量必须为非负整数，空白表示未填写；不能填写公式。")
    return int(value)


def _input_quantity(value, sheet, number, column, label, month):
    """Attach the visible input address when a quantity cannot be imported."""
    try:
        return _quantity(value)
    except ValidationError as error:
        from openpyxl.utils import get_column_letter
        kind = ("公式" if isinstance(value, str) and value.lstrip().startswith("=")
                else "文本" if isinstance(value, str)
                else "日期或时间" if isinstance(value, (date, datetime))
                else "非整数或超出范围的值")
        address = f"{sheet.title}!{get_column_letter(column)}{number}"
        raise ValidationError(f"{address}（{label}，{month}）：{error} 检测到{kind}。") from error


def _blank(value):
    return None if value in (None, "") else value


_VERTICAL_LABEL_MERGE = re.compile(r"^([AB])([1-9][0-9]*):\1([1-9][0-9]*)$")


def _label_merges(sheet):
    """Read A/B vertical merges from either a normal or read-only worksheet."""
    if hasattr(sheet, "merged_cells"):
        references = (str(area) for area in sheet.merged_cells.ranges)
    else:
        # Both upload and managed-workbook readers use openpyxl's read-only
        # worksheets, which omit merged_cells. Walk only the already-open XLSX
        # sheet part; the upload reader has already bounded ZIP/XML contents.
        archive = sheet.parent._archive
        path = sheet._worksheet_path
        if archive.getinfo(path).file_size > 96 * 1024 * 1024:
            raise ValidationError("主表文件过大，请缩小范围后重新导出。")
        def from_xml():
            count = 0
            with archive.open(path) as source:
                for _, element in ET.iterparse(source, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] == "mergeCell":
                        count += 1
                        if count > 30000:
                            raise ValidationError("主表合并区域过多，请缩小范围后重新导出。")
                        yield element.attrib.get("ref", "")
                    element.clear()
        references = from_xml()
    for reference in references:
        match = _VERTICAL_LABEL_MERGE.fullmatch(reference)
        if match:
            column = 1 if match.group(1) == "A" else 2
            start, end = int(match.group(2)), int(match.group(3))
            if start >= 5 and end > start:
                yield column, start, end


def parse_inline_sheet(sheet, manifest):
    """Return plan inputs and catalog edits using an immutable server manifest.

    Headers and identity columns stay fixed; users may insert, move, copy and
    delete complete detail rows. Totals are rebuilt after import, never inputs.
    """
    width = manifest["last_column"]
    if not 6 <= width <= 200:
        raise ValidationError("主表列范围无效，请重新导出。")
    raw_rows = list(sheet.iter_rows(values_only=False))
    rows = [tuple(cell.value for cell in row) for row in raw_rows]
    formulas = {(r, c) for r, row in enumerate(raw_rows, 1) for c, cell in enumerate(row, 1) if cell.data_type == "f"}
    if len(rows) > 15000 or sum(len(row) for row in rows) > 1_500_000:
        raise ValidationError("主表行数过多，请缩小范围后重新导出。")
    # Excel stores a merged range's label only in its top-left cell. Recover
    # that label for covered detail rows without treating ordinary blank cells
    # as an implicit continuation of the preceding brand or model.
    merged_labels = {}
    for column, start, end in _label_merges(sheet):
        if start > len(raw_rows):
            continue
        anchor = raw_rows[start - 1][column - 1]
        for number in range(start, min(end, len(raw_rows)) + 1):
            merged_labels[number, column] = (anchor.value, anchor.data_type == "f")
    def value(row, column):
        return _blank(row[column-1]) if column <= len(row) else None
    def label_value(number, row, column):
        raw = value(row, column)
        if raw is not None:
            return raw
        return _blank(merged_labels.get((number, column), (None, False))[0])
    for offset, expected in enumerate(manifest["headers"]):
        row = rows[2 + offset] if len(rows) > 2 + offset else ()
        if [value(row, c) for c in range(1, width+1)] != [_blank(v) for v in expected]:
            raise ValidationError("主表的年份、月份或隐藏标识列已改变；请只插删明细行，不插删月份列。")
    if any(any(_blank(v) is not None for v in row[width:]) for row in rows):
        raise ValidationError("主表存在未识别的附加列，请保留原有月份列。")
    products = {p["id"]: p for p in manifest["products"]}
    originals = {row["key"]: row for row in manifest["rows"]}
    original_plans = {r["product_id"]: r for r in manifest["rows"]
                      if r["kind"] == "product" and r.get("metric", "SOP计划量") == "SOP计划量"}
    if len(originals) != len(manifest["rows"]) or set(original_plans) != set(products):
        raise ValidationError("主表原始版本映射不完整。")
    original_names = {}
    for pid, product in products.items():
        if not product.get("identity_read_only"):
            original_names.setdefault(catalog_name_key(product), []).append(pid)
    has_merged_detail_labels = any(
        (match := _VERTICAL_LABEL_MERGE.fullmatch(reference)) and int(match.group(2)) >= 5
        for reference in manifest.get("merges", ())
    )
    month_columns = [c for c in manifest["columns"] if c["kind"] == "month"]
    allowed_new = set(manifest["manual_editable_months"])
    candidates, new_rows, locks, seen_nonplans = {}, [], [], set()
    for number, row in enumerate(rows[4:], 5):
        if all(_blank(v) is None for v in row):
            continue
        pid, kind, key = (value(row, manifest[field]) for field in ("product_column", "kind_column", "key_column"))
        labels = [value(row, c) for c in (1, 2, 3)]
        original = originals.get(key) if isinstance(key, str) else None
        if key is not None and original is None:
            raise ValidationError(f"第 {number} 行的隐藏行标识无法识别，请保留原始标识或插入空白行新增。")
        if original:
            if kind != original["kind"] or pid != original.get("product_id"):
                raise ValidationError(f"第 {number} 行的版本标识与行类型不一致。")
            if original.get("metric", "SOP计划量") == "锁单数" and original["kind"] in {"product", "model_total"}:
                if key in seen_nonplans:
                    raise ValidationError("锁单行被重复复制，请只复制版本计划明细行。")
                seen_nonplans.add(key)
                locks.append((number, row, original))
                continue
            if original["kind"] != "product":
                if key in seen_nonplans:
                    raise ValidationError("汇总行被重复复制，请只复制版本计划明细行。")
                seen_nonplans.add(key)
                continue
        elif pid is not None:
            if not isinstance(pid, str) or pid not in products or kind not in (None, "product"):
                raise ValidationError(f"第 {number} 行存在未知版本编码，请插入空白行填写新版本。")
            original = original_plans[pid]
        else:
            if kind not in (None, "product"):
                raise ValidationError(f"第 {number} 行缺少可信行标识。")
            if not any(labels) and not any(value(row, c["column"]) is not None for c in month_columns):
                continue
        # A newly inserted row can also sit inside a real merged brand/model
        # block. Only inherit actual merged anchors, never ordinary blanks.
        labels = [label_value(number, row, c) for c in (1, 2, 3)]
        if original is not None:
            if has_merged_detail_labels:
                # Deleting a merged block's first version can remove its A/B
                # anchor. The retained row's immutable ID still identifies its
                # original brand/model; newly inserted rows have no such fallback.
                for index, field in enumerate(("brand", "model")):
                    if labels[index] is None:
                        labels[index] = products[pid][field]
        if any((number, c) in formulas or merged_labels.get((number, c), (None, False))[1] for c in (1, 2, 3)):
            raise ValidationError(f"第 {number} 行的品牌、车型和版本不能填写公式。")
        parsed_labels = {}
        for field, label, raw in zip(("brand", "model", "version"), ("品牌", "车型", "版本"), labels):
            normalized = text_field(raw, label, 200)
            if field == "version":
                normalized = display_version(normalized, "")
                if not normalized and original is None:
                    normalized = MODEL_PLAN_LABEL
            if not normalized:
                raise ValidationError(f"第 {number} 行请完整填写品牌、车型和版本；减少版本请删除整条明细行。")
            parsed_labels[field] = normalized
        if original is not None and is_model_plan(products[pid]) and parsed_labels["version"] != MODEL_PLAN_LABEL:
            raise ValidationError("车型总量行的版本标识请保留；需要分版本时请新增版本行。")
        candidate = {"number": number, "row": row, "labels": parsed_labels}
        if original is not None and products[pid].get("identity_read_only") and catalog_name_key(parsed_labels) != catalog_name_key(products[pid]):
            raise ValidationError("汇总父车型名称由共享规则管理，请只修改其计划量。")
        if original is not None and products[pid].get("read_only") and catalog_name_key(parsed_labels) != catalog_name_key(products[pid]):
            raise ValidationError(f"第 {number} 行是自动汇总代际，名称只读；请切换单代际维护。")
        if original is None:
            matches = original_names.get(catalog_name_key(parsed_labels), [])
            if len(matches) > 1:
                raise ValidationError(f"第 {number} 行名称对应多个来源代际，请保留该版本原行的隐藏标识。")
            if len(matches) == 1:
                pid = matches[0]
                original = original_plans[pid]
        if original is None:
            scopes = {product.get("parent_generation") for product in products.values()
                      if product.get("brand") == parsed_labels["brand"] and product.get("model") == parsed_labels["model"]
                      and not product.get("identity_read_only")}
            if len(scopes) == 1 and next(iter(scopes)):
                candidate["parent_generation"] = next(iter(scopes))
            new_rows.append(candidate)
        else:
            candidates.setdefault(pid, []).append(candidate)
    retained = {}
    for pid, matches in candidates.items():
        if len(matches) == 1:
            retained[pid] = matches[0]
            continue
        unchanged = [r for r in matches if catalog_name_key(r["labels"]) == catalog_name_key(products[pid])]
        if len(unchanged) != 1:
            raise ValidationError("同一版本的隐藏编码被重复复制且无法识别原行。请保留一行原名称，其余新行使用不同的完整名称。")
        retained[pid] = unchanged[0]
        new_rows.extend(r for r in matches if r is not unchanged[0])
    for number, row, original in locks:
        for col in month_columns:
            raw = _input_quantity(value(row, col["column"]), sheet, number, col["column"], "锁单参考", col["month"])
            if raw != _blank(original.get("values", {}).get(col["month"])):
                raise ValidationError(f"第 {number} 行是锁单参考数，不能修改；请只修改 SOP 计划行。")
    values, entries, catalog_changes, catalog_values = [], [], [], {}
    for pid, product in products.items():
        candidate = retained.get(pid)
        baseline = {field: product.get(field) for field in ("brand", "model", "version", "active", "revision")}
        baseline["active"] = product.get("active", True)
        if candidate is None:
            if product.get("identity_read_only"):
                raise ValidationError("汇总父车型总量行由共享规则管理，不能删除；可清空计划量恢复单代际汇总。")
            if product.get("read_only"):
                raise ValidationError("自动汇总代际行不能删除；请切换单代际维护或调整共享汇总规则。")
            catalog_values[pid] = {field: baseline[field] for field in ("brand", "model", "version", "active")}
            catalog_values[pid]["active"] = False
            if baseline["active"]:
                catalog_changes.append({"id": pid, "baseline": baseline, "active": False,
                                        "baseline_plan_revisions": manifest.get("plan_revisions_by_product", {}).get(pid, {})})
            continue
        labels = candidate["labels"]
        catalog_values[pid] = {**labels, "active": baseline["active"]}
        changes = {field: labels[field] for field in labels if labels[field] != baseline[field]}
        if changes:
            if product.get("read_only"):
                raise ValidationError("自动汇总代际名称只读；请切换单代际维护。")
            catalog_changes.append({"id": pid, "baseline": baseline, **changes})
        original = original_plans[pid]
        allowed = set() if product.get("read_only") else set(manifest["editable_months_by_product"].get(pid, []))
        for col in month_columns:
            month = col["month"]
            raw = _input_quantity(value(candidate["row"], col["column"]), sheet, candidate["number"], col["column"],
                                  f"{product['brand']} {product['model']} / {product['version']}", month)
            if month not in allowed:
                if raw != _blank(original.get("values", {}).get(month)):
                    raise ValidationError(f"第 {candidate['number']} 行 {month} 不可维护，请恢复原计划值。")
                continue
            values.append({"product_id": pid, "month": month, "sop": raw})
    for candidate in new_rows:
        plans = []
        for col in month_columns:
            month = col["month"]
            raw = _input_quantity(value(candidate["row"], col["column"]), sheet, candidate["number"], col["column"],
                                  f"{candidate['labels']['brand']} {candidate['labels']['model']}", month)
            if month not in allowed_new:
                if raw is not None:
                    raise ValidationError(f"新增行的 {month} 尚未开放，不能填写计划。")
                continue
            plans.append({"month": month, "sop": raw, "baseline_sop": None})
        entries.append({**candidate["labels"], "plans": plans,
                        **({"parent_generation": candidate["parent_generation"]} if candidate.get("parent_generation") else {})})
    return {"values": values, "entries": entries, "catalog_changes": catalog_changes,
            "catalog_values": catalog_values, "removed_ids": sorted(set(products) - set(retained))}


def parse_model_input_sheet(sheet, manifest):
    """Read model-only inputs without turning automatic totals into overrides.

    Existing rows retain their server-owned identity. Model and version catalogs
    are intentionally not inferred from absent detail rows in this view.
    """
    from openpyxl.utils import get_column_letter

    width = manifest["last_column"]
    if not 6 <= width <= 200 or not isinstance(manifest.get("model_inputs"), dict):
        raise ValidationError("车型计划行映射不完整，请重新导出。")
    raw_rows = list(sheet.iter_rows(values_only=False))
    if len(raw_rows) > 15000 or sum(len(row) for row in raw_rows) > 1_500_000:
        raise ValidationError("主表行数过多，请缩小范围后重新导出。")
    rows = [tuple(cell.value for cell in row) for row in raw_rows]
    formulas = {(r, c) for r, row in enumerate(raw_rows, 1) for c, cell in enumerate(row, 1) if cell.data_type == "f"}
    merged = {}
    for column, start, end in _label_merges(sheet):
        if start <= len(raw_rows):
            anchor = raw_rows[start - 1][column - 1]
            for number in range(start, min(end, len(raw_rows)) + 1):
                merged[number, column] = (anchor.value, anchor.data_type == "f")

    def value(row, column):
        return _blank(row[column - 1]) if column <= len(row) else None

    def label(number, row, column):
        return value(row, column) if value(row, column) is not None else _blank(merged.get((number, column), (None, False))[0])

    for offset, expected in enumerate(manifest["headers"]):
        row = rows[2 + offset] if len(rows) > 2 + offset else ()
        if [value(row, c) for c in range(1, width + 1)] != [_blank(v) for v in expected]:
            raise ValidationError("主表的年份、月份或隐藏标识列已改变；请保留原有月份列。")
    if any(any(_blank(v) is not None for v in row[width:]) for row in rows):
        raise ValidationError("主表存在未识别的附加列，请保留原有月份列。")
    originals = {row["key"]: row for row in manifest["rows"]}
    if len(originals) != len(manifest["rows"]):
        raise ValidationError("车型计划原始行标识重复，请重新导出。")
    inputs = manifest["model_inputs"]
    months = [col for col in manifest["columns"] if col["kind"] == "month"]
    allowed_new = set(manifest["manual_editable_months"])
    existing_names = {catalog_name_key({**item, "version": MODEL_PLAN_LABEL}) for item in inputs.values()}
    seen, new_names = set(), set()
    values, entries, model_entries = [], [], []
    for number, row in enumerate(rows[4:], 5):
        if all(_blank(v) is None for v in row):
            continue
        pid, kind, key = (value(row, manifest[field]) for field in ("product_column", "kind_column", "key_column"))
        original = originals.get(key) if isinstance(key, str) else None
        if key is not None and original is None:
            raise ValidationError(f"第 {number} 行的隐藏行标识无法识别，请保留原始标识或插入空白行新增车型。")
        if original is not None:
            if key in seen or pid != original.get("product_id") or kind != original["kind"]:
                raise ValidationError(f"第 {number} 行的隐藏行标识被复制或改动，请保留每条原始行。")
            seen.add(key)
            if key in inputs:
                item = inputs[key]
                brand = label(number, row, 1)
                if brand is None and original.get("labels", [None])[0] is None:
                    brand = item["brand"]
                if (brand != item["brand"] or value(row, 2) != _blank(original["labels"][1])
                        or any((number, c) in formulas or merged.get((number, c), (None, False))[1] for c in (1, 2))):
                    raise ValidationError("车型汇总表暂不支持修改已有车型名称，请在网页维护中改名后重新导出；汇总车型名称由共享规则管理。")
                allowed = set(item["editable_months"])
                changed = []
                for col in months:
                    month = col["month"]
                    amount = _input_quantity(value(row, col["column"]), sheet, number, col["column"],
                                             f"{item['brand']} {item['model']} 车型总量", month)
                    displayed = item["display_values"][month]
                    if month not in allowed:
                        if amount != displayed:
                            raise ValidationError(f"第 {number} 行 {month} 不可维护，请恢复原计划值。")
                    elif amount != displayed:
                        if item["product_id"] is not None:
                            values.append({"product_id": item["product_id"], "month": month, "sop": amount})
                        else:
                            changed.append({"month": month, "sop": amount, "cell": f"{get_column_letter(col['column'])}{number}"})
                if changed:
                    model_entries.append({"brand": item["brand"], "model": item["model"],
                                          "version": MODEL_PLAN_LABEL, "parent_generation": item.get("parent_generation"),
                                          "plans": changed})
            else:
                for column, expected in enumerate(original.get("labels", [])[:2], 1):
                    if expected is not None and value(row, column) != expected:
                        raise ValidationError("锁单、合计和说明行的名称不能修改，请只维护车型 SOP 月计划。")
                if original.get("metric") == "锁单数" and original["kind"] in {"product", "model_total"}:
                    for col in months:
                        raw = _input_quantity(value(row, col["column"]), sheet, number, col["column"], "锁单参考", col["month"])
                        if raw != original.get("values", {}).get(col["month"]):
                            raise ValidationError(f"第 {number} 行是锁单参考数，不能修改；请只修改 SOP 计划行。")
                    if any(value(row, col["column"]) is not None for col in manifest["columns"] if col["kind"] == "year_total"):
                        raise ValidationError("锁单参考行不能填写年度 SOP 计划量。")
            continue
        if pid is not None or kind not in (None, "model_total"):
            raise ValidationError(f"第 {number} 行缺少可信车型标识，请插入空白行新增车型。")
        labels = {field: text_field(label(number, row, column), title, 200)
                  for field, title, column in (("brand", "品牌", 1), ("model", "车型", 2))}
        if any((number, c) in formulas or merged.get((number, c), (None, False))[1] for c in (1, 2)):
            raise ValidationError("新增车型的品牌和车型名称不能填写公式。")
        if not any(labels.values()) and not any(value(row, col["column"]) is not None for col in months):
            continue
        if not all(labels.values()):
            raise ValidationError(f"第 {number} 行请填写品牌和带年款的车型，版本无需填写。")
        if not re.search(r"20\d{2}款", labels["model"]):
            raise ValidationError(f"第 {number} 行车型请保留年款，例如 新车型 2027款。")
        labels["version"] = MODEL_PLAN_LABEL
        name = catalog_name_key(labels)
        if name in existing_names or name in new_names:
            raise ValidationError("同一品牌和车型年款不能重复新增，请在已有车型行填写计划量。")
        new_names.add(name)
        plans = []
        for col in months:
            month = col["month"]
            amount = _input_quantity(value(row, col["column"]), sheet, number, col["column"],
                                     f"{labels['brand']} {labels['model']} 车型总量", month)
            if month not in allowed_new:
                if amount is not None:
                    raise ValidationError(f"新增行的 {month} 尚未开放，不能填写计划。")
            else:
                plans.append({"month": month, "sop": amount, "baseline_sop": None})
        entries.append({**labels, "plans": plans})
    if set(originals) - seen:
        raise ValidationError("车型汇总表暂不支持删除已有行；可清空计划量，或在网页维护中停用车型后重新导出。")
    return {"values": values, "entries": entries, "model_entries": model_entries,
            "catalog_changes": [], "catalog_values": {}, "removed_ids": []}
