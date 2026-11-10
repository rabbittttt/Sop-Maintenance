"""Compare confirmed evidence within the same vehicle, place, and period."""

from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date
import json
import re


METRICS = {
    "sop_plan": "SOP计划量",
    "locked_orders": "交车锁单",
    "sales_actual": "实际销量",
    "sales_plan": "计划销量",
    "supply_plan": "供应计划",
    "transit": "在途车辆",
    "inbound_forecast": "预计入库",
    "inbound_actual": "实际入库",
    "outbound_actual": "实际出库",
    "opening_stock": "期初库存",
    "closing_stock": "期末库存",
    "adjustments": "库存调整",
}
BALANCE_INPUTS = ("opening_stock", "inbound_actual", "outbound_actual", "adjustments")
STOCK_METRICS = (*BALANCE_INPUTS, "closing_stock")
GRAINS = {"day": "日", "week": "周", "month": "月"}
STATUSES = ("pending", "confirmed", "excluded")
MAX_SAFE_INTEGER = 2**53 - 1


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _period_start(period, granularity):
    if granularity == "month" and re.fullmatch(r"[0-9]{4}-[0-9]{2}", period):
        period += "-01"
    elif granularity == "month" or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", period):
        return None
    try:
        return date.fromisoformat(period)
    except ValueError:
        return None


def validate_record(record):
    """Return field errors that prevent a record from being confirmed."""
    if not isinstance(record, dict):
        return ["记录格式无效"]
    errors = []
    for field, message in (
        ("id", "缺少记录标识"),
        ("source_id", "缺少来源标识"),
        ("source_ref", "缺少来源位置"),
        ("target_id", "请选择车型或版本"),
        ("model", "缺少车型名称"),
    ):
        if not _text(record.get(field)):
            errors.append(message)
    metric = _text(record.get("metric"))
    if metric not in METRICS:
        errors.append("指标未识别")
    quantity = record.get("quantity")
    if quantity is None or quantity == "":
        errors.append("数量缺失，不能按 0 处理")
    elif type(quantity) is not int or abs(quantity) > MAX_SAFE_INTEGER:
        errors.append("数量必须是安全范围内的整数")
    elif quantity < 0 and metric != "adjustments":
        errors.append("该指标数量不能为负数")

    granularity = _text(record.get("granularity"))
    if granularity not in GRAINS:
        errors.append("请选择日、周或月")
    else:
        start = _period_start(_text(record.get("period")), granularity)
        if start is None:
            errors.append("请输入有效期间（YYYY-MM）" if granularity == "month" else "请输入有效日期（YYYY-MM-DD）")
        elif granularity == "week" and start.weekday() != 0:
            errors.append("周期间须使用该周周一日期")
    if _text(record.get("scope")) not in ("total", "detail"):
        errors.append("请选择合计或明细")
    if record.get("status") not in STATUSES:
        errors.append("确认状态无效")
    if metric in STOCK_METRICS and not _text(record.get("location")):
        errors.append("库存及出入库指标须明确库区，可填写“全部仓库”")
    return errors


def _metric_value(rows, metric, add_issue):
    """Return a comparable value and its coverage, or flag ambiguity."""
    if not rows:
        return None, None
    scopes = {row["scope"] for row in rows}
    batches = {_text(row.get("batch")) for row in rows}
    sources = {row["source_id"] for row in rows}
    references = {row["source_ref"] for row in rows}
    all_distinct_details = (
        scopes == {"detail"} and "" not in batches and len(batches) == len(rows)
        and len(sources) == 1 and len(references) == len(rows)
    )
    if len(rows) == 1 or all_distinct_details:
        quantity = sum(row["quantity"] for row in rows)
        if abs(quantity) <= MAX_SAFE_INTEGER:
            coverage = None
            if not any(row.get("partial") is True for row in rows):
                if scopes == {"total"}:
                    coverage = ("total",)
                elif "" not in batches:
                    coverage = ("detail", frozenset(batches))
            return quantity, coverage
        add_issue("numeric_overflow", f"{METRICS[metric]}合计超出整数范围", "该指标未计入对账。", rows)
        return None, None

    label = METRICS[metric]
    same_quantity = len({row["quantity"] for row in rows}) == 1
    if (
        len(rows) == 2 and scopes == {"total"} and same_quantity and len(sources) == 2
        and sum(row.get("baseline") is True for row in rows) == 1
        and not any(row.get("partial") is True for row in rows)
    ):
        return rows[0]["quantity"], ("total",)
    if len(scopes) > 1:
        add_issue("mixed_scope", f"{label}同时包含合计与明细", "核对覆盖范围后保留一种口径。", rows)
    elif len(sources) > 1 and not same_quantity:
        add_issue("source_conflict", f"{label}跨来源数值冲突", "请确认采用的资料版本。", rows)
    elif same_quantity or len(references) < len(rows):
        add_issue("possible_duplicate", f"{label}可能重复", "请核对记录是否覆盖同一批车辆。", rows)
    else:
        add_issue("ambiguous_overlap", f"{label}记录范围不明确", "请核对总量、批次或来源后排除重叠记录。", rows)
    return None, None


def _same_coverage(coverage, metrics):
    first = coverage[metrics[0]]
    return first is not None and all(coverage[metric] == first for metric in metrics[1:])


def _period_interval(group):
    grain = group["granularity"]
    start = _period_start(group["period"], grain)
    length = monthrange(start.year, start.month)[1] if grain == "month" else 7 if grain == "week" else 1
    return start.toordinal(), start.toordinal() + length


def _period_issues(groups, records_by_id, add_issue):
    """Flag overlaps within a metric without treating different grains as sums."""
    by_metric = defaultdict(list)
    for group in groups:
        start, end = _period_interval(group)
        for metric, record_ids in group["record_ids"].items():
            if record_ids:
                by_metric[group["target_id"], group["location"], metric].append((start, end, group))
    for (_, _, metric), spans in by_metric.items():
        active = []
        for start, end, group in sorted(spans, key=lambda item: (item[0], item[1])):
            active = [(old_end, old) for old_end, old in active if old_end > start]
            for _, old in active:
                if old["granularity"] == group["granularity"]:
                    continue
                rows = [records_by_id[id_] for current in (old, group) for id_ in current["record_ids"][metric]]
                first = f'{old["period"]}（{GRAINS[old["granularity"]]}）'
                second = f'{group["period"]}（{GRAINS[group["granularity"]]}）'
                add_issue("overlapping_periods", f"{METRICS[metric]}期间重叠", f"{first}与{second}存在交叠。",
                          rows, group["key"], "warning")
            active.append((end, group))


def _balance(values, coverage, metrics, add_issue):
    result = {
        "expected_closing": None,
        "actual_closing": values["closing_stock"],
        "difference": None,
        "missing": [metric for metric in STOCK_METRICS if values[metric] is None],
    }
    if not _same_coverage(coverage, BALANCE_INPUTS):
        return result
    expected = values["opening_stock"] + values["inbound_actual"] - values["outbound_actual"] + values["adjustments"]
    if abs(expected) > MAX_SAFE_INTEGER:
        rows = [row for metric in BALANCE_INPUTS for row in metrics[metric]]
        add_issue("numeric_overflow", "计算期末库存超出整数范围", "请检查库存数量。", rows)
        return result
    result["expected_closing"] = expected
    if not _same_coverage(coverage, STOCK_METRICS):
        return result
    difference = values["closing_stock"] - expected
    if difference:
        rows = [row for metric in STOCK_METRICS for row in metrics[metric]]
        if abs(difference) > MAX_SAFE_INTEGER:
            add_issue("numeric_overflow", "库存差额超出整数范围", "请检查库存数量。", rows)
            return result
        add_issue("balance_mismatch", "期末库存存在差额",
                  f'记录期末 {values["closing_stock"]} 辆，计算期末 {expected} 辆，相差 {difference} 辆。', rows)
    result["difference"] = difference
    return result


def reconcile(records):
    """Calculate evidence groups and retain links to every supporting record."""
    issues = []

    def add_issue(kind, title, detail, rows, group_key=None, severity="error"):
        issues.append({
            "id": f"issue-{len(issues) + 1}", "type": kind, "severity": severity, "title": title,
            "detail": detail, "record_ids": [row["id"] for row in rows if _text(row.get("id"))],
            "group_key": group_key,
        })

    all_records = [record if isinstance(record, dict) else {} for record in records]
    statuses = Counter(_text(record.get("status")) for record in all_records)
    active = [record for record in all_records if record.get("status") != "excluded"]
    ids = Counter(_text(row.get("id")) for row in active if _text(row.get("id")))
    for id_, count in ids.items():
        if count > 1:
            add_issue("duplicate_id", "记录标识重复", "重复标识的记录未计入对账。", [{"id": id_}])

    grouped = defaultdict(list)
    records_by_id = {}
    invalid = 0
    for record in active:
        if record.get("status") != "confirmed":
            continue
        errors = validate_record(record)
        id_ = _text(record.get("id"))
        if errors or ids[id_] > 1:
            invalid += 1
            if errors:
                add_issue("invalid_record", "已确认记录存在缺项", "；".join(errors), [record])
            continue
        clean_record = {field: value.strip() if isinstance(value, str) else value for field, value in record.items()}
        identity = tuple(_text(record.get(field)) for field in ("target_id", "location", "period", "granularity"))
        grouped[identity].append(clean_record)
        records_by_id[id_] = clean_record

    groups = []
    for (target, location, period, grain), rows in sorted(grouped.items()):
        group_key = json.dumps([target, location, period, grain], ensure_ascii=False, separators=(",", ":"))

        def group_issue(kind, title, detail, evidence, severity="error"):
            add_issue(kind, title, detail, evidence, group_key, severity)

        metrics = {metric: [] for metric in METRICS}
        for row in rows:
            metrics[row["metric"]].append(row)
        baseline = [row for row in rows if row.get("baseline") is True]
        values = {}
        coverage = {}
        for metric, evidence in metrics.items():
            values[metric], coverage[metric] = _metric_value(evidence, metric, group_issue)

        gap = None
        if _same_coverage(coverage, ("sop_plan", "supply_plan")):
            gap = values["sop_plan"] - values["supply_plan"]
            if gap > 0:
                group_issue("supply_gap", "供应计划低于SOP计划",
                            f"同口径供应缺口 {gap} 辆。", metrics["sop_plan"] + metrics["supply_plan"], "warning")

        groups.append({
            "key": group_key, "target_id": target, "model": (baseline or rows)[0]["model"], "location": location,
            "period": period, "granularity": grain, "values": values, "supply_gap": gap,
            "baseline_values": {row["metric"]: row["quantity"] for row in baseline},
            "partial_baseline_metrics": [row["metric"] for row in baseline if row.get("partial") is True],
            "record_ids": {metric: [row["id"] for row in evidence] for metric, evidence in metrics.items()},
            "conflicts": [metric for metric, evidence in metrics.items() if evidence and values[metric] is None],
            "balance": _balance(values, coverage, metrics, group_issue),
        })

    _period_issues(groups, records_by_id, add_issue)
    return {"groups": groups, "issues": issues, "stats": {
        "total_records": len(all_records), "confirmed_records": statuses["confirmed"],
        "pending_records": statuses["pending"], "excluded_records": statuses["excluded"],
        "baseline_records": sum(row.get("baseline") is True for row in all_records),
        "invalid_confirmed_records": invalid, "group_count": len(groups), "issue_count": len(issues),
        "conflict_count": sum(len(group["conflicts"]) for group in groups),
    }}
