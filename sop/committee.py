"""Scoped, immutable committee decisions captured inside a planning save."""

import json

from .plan_scope import plan_total
from .store import ConflictError, TZ, ValidationError, month_add, month_range, text_field, validate_month


def create_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS committee_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL, level INTEGER NOT NULL, scope TEXT NOT NULL,
            brands TEXT NOT NULL, model_keys TEXT NOT NULL, model_count INTEGER NOT NULL,
            owner TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS committee_period ON committee_decisions(period, id);
        CREATE TABLE IF NOT EXISTS committee_amounts (
            decision_id INTEGER NOT NULL REFERENCES committee_decisions(id),
            model_key TEXT NOT NULL, brand TEXT NOT NULL, model TEXT NOT NULL,
            month TEXT NOT NULL, sop INTEGER,
            PRIMARY KEY (decision_id, model_key, month));
        CREATE TRIGGER IF NOT EXISTS committee_decisions_no_update BEFORE UPDATE ON committee_decisions
          BEGIN SELECT RAISE(ABORT, 'Committee decisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS committee_decisions_no_delete BEFORE DELETE ON committee_decisions
          BEGIN SELECT RAISE(ABORT, 'Committee decisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS committee_amounts_no_update BEFORE UPDATE ON committee_amounts
          BEGIN SELECT RAISE(ABORT, 'Committee quantities are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS committee_amounts_no_delete BEFORE DELETE ON committee_amounts
          BEGIN SELECT RAISE(ABORT, 'Committee quantities are immutable'); END;
    """)


def revision(store):
    """An optimistic base for freezing all selected quantities, including untouched cells."""
    values = store.db.execute("""SELECT
        (SELECT COALESCE(MAX(id), 0) FROM events WHERE entity='plan'),
        (SELECT COALESCE(MAX(id), 0) FROM catalog_versions),
        (SELECT COALESCE(MAX(id), 0) FROM settings_versions)""").fetchone()
    return ":".join([store.clock().astimezone(TZ).strftime("%Y-%m"), *map(str, values)])


def plan_months(current_month, plan_end, sales, plans):
    """Use the editable planning calendar, independent of the displayed period."""
    members = sales.get("generation_members", {})
    recorded = (row["month"] for rows in (plans, members.get("plans", []), sales.get("sales", []),
               sales.get("model_sales", []), members.get("sales", []), members.get("model_sales", [])) for row in rows)
    first_month = min(month_add(current_month, -3), min(recorded, default=current_month))
    return month_range(first_month[:4] + "-01", plan_end)


def validate_request(value, store, sales, current_month, plan_end):
    fields = {"level", "period", "scope", "brands", "model_keys", "base_revision", "start_month", "end_month"}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValidationError("计委决策格式无效。")
    level = value.get("level")
    if type(level) is not int or level not in (1, 2, 3):
        raise ValidationError("请选择一级、二级或三级计委。")
    period = validate_month(value.get("period"))
    if period > current_month:
        raise ValidationError("计委决策期次不能晚于当前期次。")
    if not value.get("start_month") or not value.get("end_month"):
        raise ValidationError("请选择计委决策的计划起止月份。")
    start_month = validate_month(value["start_month"])
    end_month = validate_month(value["end_month"])
    if start_month > end_month:
        raise ValidationError("计委决策的计划开始月不能晚于结束月。")
    scope = value.get("scope")
    if scope not in ("all", "brands", "models"):
        raise ValidationError("请选择计委决策范围。")

    def selections(field, label):
        values = value.get(field, [])
        if not isinstance(values, list) or len(values) > 2000:
            raise ValidationError(f"{label}选择无效或过多。")
        cleaned = [text_field(item, label, 1000) for item in values]
        if not all(cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValidationError(f"{label}选择为空或重复。")
        return cleaned

    brands, model_keys = selections("brands", "决策品牌"), selections("model_keys", "决策车型")
    if bool(brands) != (scope == "brands") or bool(model_keys) != (scope == "models"):
        raise ValidationError("所选品牌或车型与计委决策范围不一致。")
    base = value.get("base_revision")
    if not isinstance(base, str) or not base or len(base) > 100:
        raise ValidationError("计委决策缺少计划基准，请刷新后重试。")
    if base != revision(store):
        raise ConflictError([{"kind": "committee", "reason": "plans_changed",
                              "message": "计委决策的计划或车型目录已更新，请刷新后核对决策范围和数量。"}])
    months = plan_months(current_month, plan_end, sales, store.view_plans(sales))
    if start_month not in months or end_month not in months:
        raise ValidationError(f"计委决策月份须在开放计划范围 {months[0]} 至 {months[-1]} 内。")
    return {"level": level, "period": period, "scope": scope, "brands": brands, "model_keys": model_keys,
            "start_month": start_month, "end_month": end_month}


def _model_groups(sales):
    groups = {}
    products = sales["products"] + sales.get("generation_members", {}).get("products", [])
    for product in products:
        if product.get("read_only"):
            continue
        parent = product.get("parent_generation")
        brand, model = product["brand"], product["model"]
        key = json.dumps(["member", parent, brand, model], ensure_ascii=False, separators=(",", ":")) if parent else f"{brand}|{model}"
        groups.setdefault(key, []).append(product)
    return groups


def _selected_groups(request, before, after):
    after = {key: products for key, products in after.items()
             if any(product.get("active") is not False for product in products)}
    scope = request["scope"]
    if scope == "all":
        selected = set(after)
    elif scope == "brands":
        brands = set(request["brands"])
        if brands - {products[0]["brand"] for products in after.values()}:
            raise ValidationError("所选计委品牌已不在车型目录中。")
        selected = {key for key, products in after.items() if products[0]["brand"] in brands}
    else:
        final_keys = {product["id"]: key for key, products in after.items() for product in products}
        selected = set()
        for key in request["model_keys"]:
            old_ids = {p["id"] for p in before.get(key, [])}
            mapped = {final_keys[pid] for pid in old_ids if pid in final_keys}
            if len(mapped) == 1:
                selected.update(mapped)
            elif not old_ids and key in after:
                selected.add(key)
            else:
                raise ValidationError("所选计委车型已变化，请核对决策范围。")
    if not selected:
        raise ValidationError("计委决策范围内没有车型。")
    return {key: products for key, products in after.items() if key in selected}


def capture(store, request, before, sales, owner, recorded_at):
    """Freeze effective monthly totals and the raw model/version inputs used by them."""
    groups = _model_groups(sales)
    selected = _selected_groups(request, _model_groups(before), groups)
    all_products = {p["id"]: p for products in groups.values() for p in products}
    contributing = {}
    for key, products in selected.items():
        linked_ids = {pid for p in products for pid in p.get("summary_member_product_ids", [])}
        own_ids = {p["id"] for p in products}
        contributing[key] = [*products, *(all_products[pid] for pid in sorted(linked_ids - own_ids) if pid in all_products)]
    frozen_products = {p["id"]: p for products in contributing.values() for p in products}

    start_month, end_month = request["start_month"], request["end_month"]
    raw_plans = store.db.execute("SELECT * FROM plans WHERE month BETWEEN ? AND ? ORDER BY product_id, month",
                                 (start_month, end_month))
    plans = [dict(row) for row in raw_plans if row["product_id"] in frozen_products]
    months = month_range(start_month, end_month)
    plan_index = {(row["product_id"], row["month"]): row for row in plans}
    model_keys = list(selected)
    metadata = {**request, "model_keys": model_keys, "model_count": len(model_keys),
                "owner": owner, "recorded_at": recorded_at}
    payload = {"products": list(frozen_products.values()), "plans": plans}
    cursor = store.db.execute("""INSERT INTO committee_decisions
        (period, level, scope, brands, model_keys, model_count, owner, recorded_at, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
        request["period"], request["level"], request["scope"],
        json.dumps(request["brands"], ensure_ascii=False), json.dumps(model_keys, ensure_ascii=False),
        len(model_keys), owner, recorded_at, json.dumps(payload, ensure_ascii=False)))
    decision_id = cursor.lastrowid
    store.db.executemany("""INSERT INTO committee_amounts
        (decision_id, model_key, brand, model, month, sop) VALUES (?, ?, ?, ?, ?, ?)""",
        ((decision_id, key, products[0]["brand"], products[0]["model"], month,
          plan_total(contributing[key], month, plan_index))
         for key, products in selected.items() for month in months))
    return {"id": decision_id, **metadata}


def state(store, current_period, base_revision, months):
    previous_period = month_add(current_period, -1)
    # A scoped blank is an explicit replacement. Resolve latest before returning the amount.
    rows = store.db.execute("""
        SELECT level, model_key, brand, model, month, sop FROM (
          SELECT d.level, a.model_key, a.brand, a.model, a.month, a.sop,
            ROW_NUMBER() OVER (PARTITION BY d.level, a.model_key, a.month ORDER BY d.id DESC) AS rn
          FROM committee_decisions AS d JOIN committee_amounts AS a ON a.decision_id=d.id
          WHERE d.period=?
        ) WHERE rn=1 ORDER BY level, model_key, month
        """, (previous_period,))
    return {"current_period": current_period, "previous_period": previous_period,
            "base_revision": base_revision, "plan_months": months, "amounts": [dict(row) for row in rows]}


def _metadata(row):
    result = dict(row)
    result["brands"] = json.loads(result["brands"])
    result["model_keys"] = json.loads(result["model_keys"])
    return result


def list_decisions(store):
    # Stored amount rows describe the exact coverage of old and new decisions.
    rows = store.db.execute("""SELECT d.id, d.period, d.level, d.scope, d.brands, d.model_keys, d.model_count,
        d.owner, d.recorded_at, MIN(a.month) AS start_month, MAX(a.month) AS end_month
        FROM committee_decisions AS d LEFT JOIN committee_amounts AS a ON a.decision_id=d.id
        GROUP BY d.id ORDER BY d.id DESC""")
    return {"items": [_metadata(row) for row in rows]}


def get_decision(store, decision_id):
    try:
        decision_id = int(decision_id)
    except (TypeError, ValueError):
        raise ValidationError("计委决策编号无效。") from None
    if not 0 < decision_id <= 2**63 - 1:
        raise ValidationError("计委决策编号无效。")
    row = store.db.execute("SELECT * FROM committee_decisions WHERE id=?", (decision_id,)).fetchone()
    if not row:
        raise ValidationError("计委决策不存在。")
    metadata = _metadata(row)
    payload = json.loads(metadata.pop("payload"))
    amounts = [dict(item) for item in store.db.execute("""SELECT model_key, brand, model, month, sop FROM committee_amounts
        WHERE decision_id=? ORDER BY model_key, month""", (decision_id,))]
    months = {item["month"] for item in amounts}
    metadata.update(start_month=min(months, default=None), end_month=max(months, default=None))
    return {"decision": metadata, "amounts": amounts, **payload}
