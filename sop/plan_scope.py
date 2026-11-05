"""Model totals and summary overrides share one ledger without double counting."""
from collections import defaultdict

from .source_rules import normalize_generation, product_display_model

MODEL_PLAN_LABEL = "车型总量（不分版本）"


def is_model_plan(product):
    return product.get("plan_level") == "model" or product.get("version") == MODEL_PLAN_LABEL


def plan_value(row, field="sop"):
    value = row.get(field)
    return row.get("auto_sop") if field == "sop" and value is None else value


def effective_products(products, month, plans, field="sop"):
    selected_products = list(products)
    removed = set()
    for parent in selected_products:
        if not parent.get("summary_generation") or parent.get("read_only"):
            continue
        row = plans.get((parent["id"], month), {})
        if field == "sop" and plan_value(row, field) is not None:
            removed.update(parent.get("summary_member_product_ids", []))
        elif parent.get("summary_member_product_ids"):
            removed.add(parent["id"])
    groups = defaultdict(list)
    for product in selected_products:
        if product["id"] in removed:
            continue
        scope = product.get("parent_generation") or ("summary:" + product["summary_generation"] if product.get("summary_generation") else "")
        groups[(scope, normalize_generation(product.get("brand")), product_display_model(product))].append(product)
    selected = []
    for members in groups.values():
        totals = [p for p in members if is_model_plan(p)
                  and plan_value(plans.get((p["id"], month), {}), field) is not None]
        selected.extend(totals if field == "sop" and totals else [p for p in members if not is_model_plan(p)])
    return selected


def plan_total(products, month, plans, field="sop"):
    values = [plan_value(plans.get((p["id"], month), {}), field)
              for p in effective_products(products, month, plans, field)]
    known = [value for value in values if value is not None]
    return sum(known) if known else None
