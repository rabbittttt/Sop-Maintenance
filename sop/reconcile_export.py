"""Export a consistent revision of a reconciliation round."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


STATUS_LABELS = {"pending": "待确认", "confirmed": "已确认", "excluded": "已排除"}
GRAIN_LABELS = {"month": "月", "week": "周", "day": "日"}


def _append(sheet, values):
    sheet.append([ILLEGAL_CHARACTERS_RE.sub("", value).replace("\ufffe", "").replace("\uffff", "")
                  if isinstance(value, str) else value for value in values])
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"
        elif type(cell.value) is int:
            cell.number_format = "0"


def _table(workbook, name, headers, rows, widths):
    sheet = workbook.create_sheet(name)
    _append(sheet, headers)
    for row in rows:
        _append(sheet, row)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="0069AD")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 27
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def workbook_bytes(detail):
    workbook = Workbook()
    workbook.remove(workbook.active)
    result, metrics = detail["result"], detail["metrics"]
    sources = {item["id"]: item["name"] for item in detail["sources"]}
    targets = {item["id"]: item["label"] for item in detail["targets"]}
    groups = {item["key"]: item for item in result["groups"]}
    records = {item["id"]: item for item in detail["records"]}
    sources["baseline"] = "SOP基准"
    _table(workbook, "对账结果", ["车型", "期间", "粒度", "库区", *metrics.values(), "SOP基准量", "锁单基准量",
                               "供应缺口（辆）", "库存差额（辆）", "部分基准"],
           ((group["model"], group["period"], GRAIN_LABELS[group["granularity"]], group["location"],
             *(group["values"].get(key) for key in metrics), group["baseline_values"].get("sop_plan"),
             group["baseline_values"].get("locked_orders"), group["supply_gap"], group["balance"]["difference"],
             "、".join(metrics[key] for key in group["partial_baseline_metrics"])) for group in groups.values()),
           [34, 16, 10, 20, *([15] * len(metrics)), 16, 16, 20, 20, 24])
    _table(workbook, "异常", ["类型", "车型", "期间", "说明", "来源位置", "记录编号"],
           ((issue["title"], groups.get(issue["group_key"], {}).get("model"), groups.get(issue["group_key"], {}).get("period"),
             issue["detail"], "；".join(f"{sources.get(records[rid]['source_id'], '')} / {records[rid]['source_ref']}"
                                     for rid in issue["record_ids"] if rid in records), "；".join(issue["record_ids"]))
            for issue in result["issues"]), [34, 34, 16, 52, 56, 46])
    _table(workbook, "资料明细", ["状态", "车型", "原文车型", "原文版本", "期间", "粒度", "指标", "数量", "库区", "批次",
                               "范围", "来源", "来源位置", "原文", "记录编号"],
           ((STATUS_LABELS[row["status"]], targets.get(row["target_id"]), row["model"], row.get("variant"),
             row["period"], GRAIN_LABELS.get(row["granularity"]), metrics.get(row["metric"]), row["quantity"],
             row["location"], row["batch"], {"total": "合计", "detail": "明细"}.get(row["scope"]), sources.get(row["source_id"]),
             row["source_ref"], row["original_text"], row["id"]) for row in detail["records"]),
           [12, 34, 24, 18, 16, 10, 18, 14, 18, 18, 12, 28, 40, 56, 40])
    _table(workbook, "对账基准", ["轮次", "开始月份", "结束月份", "基准时间", "快照编号", "基准版本", "记录修订", "创建人"],
           [tuple(detail[key] for key in ("name", "start_month", "end_month", "baseline_at", "snapshot_id",
                                          "baseline_version", "revision", "owner"))], [32, 16, 16, 30, 14, 14, 14, 26])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def summary_bytes(detail):
    result = detail["result"]
    groups = {group["key"]: group for group in result["groups"]}
    records = {record["id"]: record for record in detail["records"]}
    sources = {source["id"]: source["name"] for source in detail["sources"]}
    sources["baseline"] = "SOP基准"
    lines = [detail["name"], f"期间 {detail['start_month']} 至 {detail['end_month']}",
             f"SOP基准 {detail['baseline_at']}（版本 {detail['baseline_version']}） · 记录修订 {detail['revision']}",
             f"待确认 {result['stats']['pending_records']} 条 · 异常 {len(result['issues'])} 项", ""]
    for issue in result["issues"]:
        group = groups.get(issue["group_key"], {})
        lines.append(" · ".join(filter(None, (group.get("model"), group.get("period"), issue["title"], issue["detail"]))))
        for record_id in issue["record_ids"]:
            record = records.get(record_id)
            if record:
                lines.append(f"  {sources.get(record['source_id'], '')} / {record['source_ref']}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8-sig")
