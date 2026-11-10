"""Capture the SOP planning and lock-order scopes used in one reconciliation round."""
from collections import defaultdict
from hashlib import sha256
import json

from .brand import brand_order_key
from .plan_scope import effective_products, is_model_plan, plan_value
from .source_rules import normalize_generation, project_generation_hierarchy, project_generation_sources
from .store import month_range, stamp
from .workbook import generation_model_groups


def capture_baseline(store, start, end, snapshot_id=None):
    if snapshot_id is None:
        sales = store.read_sales()
        plans = store.view_plans(sales) + sales.get("generation_members", {}).get("plans", [])
        captured = stamp(store.clock())
    else:
        frozen = store.get_snapshot(snapshot_id)
        groups = frozen.get("generation_rules", {}).get("groups", {})
        rules = {"groups": groups}
        if groups:
            sales, _ = project_generation_hierarchy(frozen, rules)
        else:
            sales, _ = project_generation_sources(frozen, "detail", rules)
        plans = frozen["plans"]
        captured = frozen["snapshot"]["cutoff_at"]

    members = sales.get("generation_members", {})
    products = [p for p in [*sales["products"], *members.get("products", [])] if not p.get("read_only")]
    by_id = {p["id"]: p for p in products}
    plan_index = {(row["product_id"], row["month"]): row for row in plans}
    version_locks, model_locks = defaultdict(list), defaultdict(list)
    for row in [*sales.get("sales", []), *members.get("sales", [])]:
        if row.get("metric") == "locked_orders":
            version_locks[row["product_id"], row["month"]].append(row)
    for row in [*sales.get("model_sales", []), *members.get("model_sales", [])]:
        if row.get("metric") == "locked_orders":
            model_locks[normalize_generation(row.get("generation")), row["month"]].append(row)
    source_names = {source.get("id"): " / ".join(str(source[key]) for key in ("file", "sheet") if source.get(key))
                    for source in sales.get("sources", []) if isinstance(source, dict)}
    targets, records = [], []
    months = month_range(start, end)

    def add(target, month, metric, value, reference, partial=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10**12 or value != int(value):
            return
        records.append({"id": f"baseline:{target['id']}:{month}:{metric}", "source_id": "baseline",
                        "source_ref": reference, "original_text": "", "target_id": target["id"],
                        "model": target["label"], "period": month, "granularity": "month", "metric": metric,
                        "quantity": int(value), "location": "", "batch": "", "scope": "total", "status": "confirmed",
                        "baseline": True, "partial": partial})

    def add_lock(target, month, rows, evidence=None):
        rows = [row for row in rows if row.get("value") is not None]
        if not rows:
            return
        # Multiple rows are safe only when they are distinct source sheets in
        # the same normalized generation, as with BETA plus the standard row.
        sources = {row.get("source_id") for row in rows}
        if len(rows) > 1 and (len(sources) != len(rows) or None in sources):
            return
        reference = "；".join(" ".join(filter(None, (source_names.get(row.get("source_id")), row.get("source_cell"))))
                             for row in (evidence or rows) if row.get("value") is not None).strip("；")
        partial = any(row.get("known_generations", 0) < row.get("total_generations", 0) for row in rows)
        add(target, month, "locked_orders", sum(row["value"] for row in rows), reference or "SOP 锁单来源", partial)

    groups, _ = generation_model_groups(products)
    for (brand, model, parent), group in groups:
        summary = next((p for p in group if p.get("summary_generation") and is_model_plan(p)), None)
        identity = json.dumps([brand, model, parent], ensure_ascii=False, separators=(",", ":"))
        target_id = summary["id"] if summary else "model:" + sha256(identity.encode()).hexdigest()[:24]
        label = f"{brand} {model}".strip()
        base_label = label + (" · 汇总" if summary else "")
        model_target = {"id": target_id, "label": base_label, "level": "summary" if summary else "model",
                        "brand": brand, "aliases": [label, model]}
        targets.append(model_target)
        linked = [by_id[pid] for pid in (summary or {}).get("summary_member_product_ids", []) if pid in by_id]
        generation = normalize_generation(summary["summary_generation"] if summary else label)
        source_generations = {normalize_generation(g) for p in group
                              for g in (p.get("source_generations") or [p.get("generation")]) if g}
        if not summary and len(source_generations) == 1:
            generation = next(iter(source_generations))
        for month in months:
            contributing = group + linked
            chosen = effective_products(contributing, month, plan_index)
            chosen = [p for p in chosen if (not p.get("start_month") or p["start_month"] <= month)
                      and (not p.get("end_month") or month <= p["end_month"])
                      or plan_value(plan_index.get((p["id"], month), {})) is not None]
            known = [p for p in chosen if plan_value(plan_index.get((p["id"], month), {})) is not None]
            parent_row = plan_index.get((target_id, month), {})
            partial = bool(parent_row.get("partial") or (parent_row.get("sop") is None and parent_row.get("auto_partial")))
            value = sum(plan_value(plan_index[p["id"], month]) for p in known) if known else None
            add(model_target, month, "sop_plan", value, f"SOP计划 / {base_label} / {month}", partial or len(known) < len(chosen))
            if summary or len(source_generations) <= 1:
                evidence = [row for member in (summary or {}).get("member_generations", [])
                            for row in model_locks[normalize_generation(member), month]]
                add_lock(model_target, month, model_locks[generation, month], evidence)
        for product in group:
            if is_model_plan(product):
                continue
            version = product.get("version", "")
            target = {"id": product["id"], "label": f"{label} · {version}", "level": "version", "brand": brand,
                      "aliases": [f"{label} {version}", f"{model} {version}", product.get("name", "")]}
            targets.append(target)
            for month in months:
                row = plan_index.get((product["id"], month), {})
                add(target, month, "sop_plan", plan_value(row), f"SOP计划 / {target['label']} / {month}")
                add_lock(target, month, version_locks[product["id"], month])
    targets.sort(key=lambda t: (brand_order_key(t["brand"]), t["level"] == "version", t["label"], t["id"]))
    return {"captured_at": captured, "targets": targets, "records": records}
