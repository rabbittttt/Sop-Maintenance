"""Import filled model totals from ordinary monthly tables without export IDs."""
from __future__ import annotations

import json

from .plan_scope import MODEL_PLAN_LABEL, is_model_plan, plan_total
from .source_rules import normalize_generation
from .store import TZ, ValidationError, catalog_name_key, horizon


def _name(product):
    return catalog_name_key({"brand": normalize_generation(product.get("brand")),
                             "model": normalize_generation(product.get("model")),
                             "version": MODEL_PLAN_LABEL})


def import_model_table(store, entries, *, choice, actor, fingerprint, before_commit=None):
    """Merge explicit values only; an unmarked workbook supplies no old baseline.

    The caller holds the store lock. Confirmation is bound to this file and the
    values, catalog and rules seen in the preceding conflict response.
    """
    rules = store._generation_rules()
    summaries = {normalize_generation(name) for name in rules["groups"]}
    sales = store.read_sales()
    roots = list(sales["products"])
    root_ids = {p["id"] for p in roots}
    roots.extend(p for p in store.read_sales(include_excluded=True)["products"]
                 if normalize_generation(p.get("summary_generation")) in summaries and p["id"] not in root_ids)
    members = sales.get("generation_members", {}).get("products", [])
    groups, child_groups = {}, {}
    for p in roots:
        groups.setdefault(_name(p), []).append(p)
    for p in members:
        child_groups.setdefault(_name(p), {}).setdefault(p.get("parent_generation"), []).append(p)
    current = store.clock().astimezone(TZ).strftime("%Y-%m")
    end = horizon(current, store.settings()["bridge_m6"])
    changes, new_entries, conflicts, targets, seen = [], [], [], [], set()
    historical_plans = None
    for entry in entries:
        name = _name(entry)
        if name in seen:
            raise ValidationError("同一品牌和车型年款重复，请合并为一行后导入。")
        seen.add(name)
        group = groups.get(name)
        if not group:
            candidates = child_groups.get(name, {})
            if len(candidates) > 1:
                raise ValidationError("车型名称对应多个汇总范围，请使用带导出标识的原表导入。")
            group = next(iter(candidates.values()), [])
        anchors = [p for p in group if is_model_plan(p) and not p.get("read_only")]
        if len(anchors) > 1:
            raise ValidationError("车型名称对应多个总量记录，请使用带导出标识的原表导入。")
        product = anchors[0] if anchors else None
        read_only_group = bool(group) and all(p.get("read_only") for p in group)
        labels = {"brand": group[0]["brand"] if group else normalize_generation(entry["brand"]),
                  "model": group[0]["model"] if group else normalize_generation(entry["model"]),
                  "version": MODEL_PLAN_LABEL}
        plans = []
        for plan in entry["plans"]:
            month, value = plan["month"], plan["sop"]
            if month > end:
                raise ValidationError(f"{month}已超出当前开放计划范围。")
            if group and month >= current and not any(p.get("active", True) and not p.get("read_only") for p in group):
                raise ValidationError("该车型已停用，请先在网页维护中启用后填写当前或未来计划。")
            row = store.db.execute("SELECT * FROM plans WHERE product_id=? AND month=?", (product["id"], month)).fetchone() if product else None
            previous = dict(row) if row else {}
            database = previous.get("sop")
            if month < current:
                # An unchanged reference total must not become an explicit override.
                historical_value = database
                if historical_value is None:
                    if historical_plans is None:
                        historical_plans = {(row["product_id"], row["month"]): row for row in (
                            *store.view_plans(sales), *sales.get("generation_members", {}).get("plans", []))}
                    historical_value = plan_total(group, month, historical_plans)
                if value != historical_value:
                    raise ValidationError(f"{plan['cell']}（{month}）已是历史月份，SOP 计划量不可修改。")
                continue
            if read_only_group:
                raise ValidationError("该车型目前只有自动汇总参考行，请刷新看板后使用车型计划表。")
            targets.append({"name": name, "month": month, "product_id": product["id"] if product else None,
                            "revision": previous.get("revision", 0), "sop": database,
                            "catalog": [(p["id"], p.get("revision", 0), p.get("active", True)) for p in group]})
            if product:
                if database == value:
                    continue
                conflict = None
                if database is not None:
                    conflict = {"product_id": product["id"], "month": month, "baseline": None,
                                "baseline_unknown": True, "workbook": value, "database": database,
                                "current": previous, "cell": plan["cell"], **labels}
                    conflicts.append(conflict)
                changes.append({"product_id": product["id"], "month": month, "sop": value,
                                "forecast": previous.get("forecast"), "revision": previous.get("revision", 0),
                                "notes": previous.get("notes", "")})
            else:
                plans.append({"month": month, "sop": value, "baseline_sop": None})
        if plans:
            new_entries.append({**labels, "plans": plans})
    snapshot = json.dumps({"rules": sales.get("generation_view", {}).get("rule_signature"),
                           "targets": targets}, ensure_ascii=False, sort_keys=True)
    cached = store.db.execute("SELECT snapshot FROM plan_model_import_previews WHERE fingerprint=?", (fingerprint,)).fetchone()
    if conflicts:
        if choice is None or cached is None or cached["snapshot"] != snapshot:
            store.db.execute("INSERT OR REPLACE INTO plan_model_import_previews VALUES (?,?)", (fingerprint, snapshot))
            return {"ok": False, "status": "conflict", "source": "model_table", "conflicts": conflicts,
                    "imported": 0, "created": 0}
        if choice == "db":
            blocked = {(c["product_id"], c["month"]) for c in conflicts}
            changes = [c for c in changes if (c["product_id"], c["month"]) not in blocked]
    elif choice is not None and cached is not None and cached["snapshot"] != snapshot and (changes or new_entries):
        # The preview can cease to conflict when somebody clears a total. Require
        # a fresh read instead of treating that as permission to overwrite it.
        store.db.execute("DELETE FROM plan_model_import_previews WHERE fingerprint=?", (fingerprint,))
        raise ValidationError("核对期间计划或车型规则已有变化，请重新导入并核对当前值。")
    result = store.import_workbook_plans({"changes": changes, "entries": new_entries,
        "catalog_changes": [], "owner": actor or "Excel导入", "reason": "车型月度计划表导入",
        "notes": "普通车型表仅导入已填写计划，空白和缺行保留现有数据。"}, before_commit=before_commit)
    store.db.execute("DELETE FROM plan_model_import_previews WHERE fingerprint=?", (fingerprint,))
    return {"ok": True, "status": "imported", "source": "model_table", "imported": result["saved_count"],
            "created": result["created_count"], "catalog_updated": 0, "deactivated": 0, "conflicts": conflicts}
