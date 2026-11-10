"""Unified summaries keep editable overrides distinct from member plan baselines."""
import json
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sop.plan_scope import MODEL_PLAN_LABEL, is_model_plan, plan_total
from sop.reconcile_baseline import capture_baseline
from sop.store import ConflictError, SOPStore, TZ, ValidationError


class SummaryHierarchyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-summary-hierarchy-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules_file = self.root / "shared_config.py"
        self.group = "智界 R7 2026款汇总"
        self.groups = {self.group: ["智界 R7 2025款", "智界 R7 2026款"]}
        self.write_rules()
        rule_patch = patch("sop.source_rules.DEFAULT_CONFIG_PATH", self.rules_file)
        rule_patch.start(); self.addCleanup(rule_patch.stop)
        self.source = self.root / "sales.json"
        self.source.write_text(json.dumps({"meta": {}, "products": [
            {"id": "old", "brand": "智界", "model": "R7", "version": "Max", "generation": "智界 R7 2025款"},
            {"id": "new", "brand": "智界", "model": "R7", "version": "Max", "generation": "智界 R7 2026款"},
            {"id": "raw-summary", "brand": "智界", "model": "R7", "version": "Max", "generation": self.group}],
            "sales": [{"product_id": pid, "month": "2026-10", "metric": "locked_orders", "value": value}
                      for pid, value in [("old", 7), ("new", 11), ("raw-summary", 999)]],
            "model_sales": [{"brand": "智界", "model": "R7", "generation": generation,
                             "month": "2026-10", "metric": "locked_orders", "value": value}
                            for generation, value in [("智界 R7 2025款", 10), ("智界 R7 2026款", 20), (self.group, 999)]]}), encoding="utf-8")
        self.current = datetime(2026, 10, 28, 12, tzinfo=TZ)
        self.store = SOPStore(self.root / "test.sqlite3", self.source, lambda: self.current)
        self.addCleanup(lambda: self.store.close())

    def write_rules(self):
        self.rules_file.write_text("MERGE_GROUPS = " + repr(self.groups), encoding="utf-8")

    def parent(self, state=None):
        state = state or self.store.state()
        return next(p for p in state["products"] if p.get("summary_generation") == self.group and not p.get("read_only"))

    def save(self, pid, value, month="2026-10"):
        prior = self.store.db.execute("SELECT revision,forecast FROM plans WHERE product_id=? AND month=?", (pid, month)).fetchone()
        return self.store.save_plans({"owner": "test", "changes": [{"product_id": pid, "month": month,
            "sop": value, "forecast": prior["forecast"] if prior else None, "revision": prior["revision"] if prior else 0}]})

    def body(self, **changes):
        view = self.store.generation_view()
        return {"request_id": str(uuid.uuid4()), "owner": "test", "generation_mode": view["mode"],
                "generation_rule_signature": view["rule_signature"], "changes": [], "entries": [], "catalog_changes": [], **changes}

    def total(self, state=None, month="2026-10"):
        state = state or self.store.state()
        return plan_total(state["products"], month, {(r["product_id"], r["month"]): r for r in state["plans"]})

    def test_default_has_one_summary_layer_and_separate_editable_members(self):
        state = self.store.state()
        self.assertEqual(state["generation_view"]["mode"], "summary")
        self.assertTrue(state["generation_view"]["hierarchy"])
        self.assertEqual({p["id"] for p in state["generation_members"]["products"]}, {"old", "new"})
        self.assertNotIn("old", {p["id"] for p in state["products"]})
        self.assertEqual({p["parent_generation"] for p in state["generation_members"]["products"]}, {self.group})
        self.assertEqual(sum(r["value"] for r in state["sales"]), 18)
        self.assertEqual(sum(r["value"] for r in state["model_sales"]), 30)
        self.assertFalse(self.parent(state)["read_only"])
        self.assertTrue(self.parent(state)["identity_read_only"])

    def test_parent_override_zero_and_clear_preserve_members(self):
        self.save("old", 10); self.save("new", 20)
        parent = self.parent()
        row = next(r for r in self.store.state()["plans"] if r["product_id"] == parent["id"])
        self.assertIsNone(row["sop"]); self.assertEqual(row["revision"], 0)
        self.assertEqual(row["auto_sop"], 30)
        for value, expected in [(80, 80), (0, 0), (None, 30)]:
            self.save(parent["id"], value)
            self.assertEqual(self.total(), expected)
            actual = next(r for r in self.store.state()["plans"] if r["product_id"] == parent["id"])
            self.assertEqual(actual["sop"], value)
            self.assertEqual(actual["auto_sop"], 30)
        self.assertEqual({r["product_id"]: r["sop"] for r in self.store.state()["generation_members"]["plans"]}, {"old": 10, "new": 20})

    def test_reconciliation_captures_parent_override_and_separate_members(self):
        self.save("old", 10)
        self.save("new", 20)
        parent = self.parent()["id"]
        self.save(parent, 80)
        for snapshot_id in (None, self.store.manual_snapshot("核对")["snapshot"]["id"]):
            baseline = capture_baseline(self.store, "2026-10", "2026-10", snapshot_id)
            amounts = {(row["target_id"], row["metric"]): row["quantity"] for row in baseline["records"]}
            self.assertEqual(amounts[parent, "sop_plan"], 80)
            self.assertEqual(amounts[parent, "locked_orders"], 30)
            self.assertEqual(amounts["old", "sop_plan"], 10)
            self.assertEqual(amounts["new", "sop_plan"], 20)
            self.assertEqual(amounts["old", "locked_orders"], 7)
            self.assertEqual(amounts["new", "locked_orders"], 11)

    def test_member_model_total_has_priority_even_when_its_name_matches_parent(self):
        self.save("old", 10); self.save("new", 20)
        created = self.store.save_grid_batch(self.body(entries=[{"client_id": "member-total", "brand": "智界",
            "model": "R7 2026款", "plan_level": "model", "parent_generation": self.group,
            "plans": [{"month": "2026-10", "sop": 50}]}]))
        pid = created["entry_ids"]["member-total"]
        self.assertNotEqual(pid, self.parent()["id"])
        self.assertEqual(self.total(), 60)
        state = self.store.state()
        combined = state["products"] + state["generation_members"]["products"]
        index = {(r["product_id"], r["month"]): r for r in state["plans"] + state["generation_members"]["plans"]}
        self.assertEqual(plan_total(combined, "2026-10", index), 60)
        self.save(self.parent()["id"], 90)
        state = self.store.state()
        index = {(r["product_id"], r["month"]): r for r in state["plans"] + state["generation_members"]["plans"]}
        self.assertEqual(plan_total(state["products"] + state["generation_members"]["products"], "2026-10", index), 90)

    def test_summary_only_can_be_maintained_without_member_catalog_or_plans(self):
        self.source.write_text('{"meta": {}, "products": [], "sales": []}', encoding="utf-8")
        # Use a separate empty database so cached historic members do not remain.
        other = SOPStore(self.root / "empty.sqlite3", self.source, lambda: self.current)
        try:
            state = other.state()
            parent = next(p for p in state["products"] if p.get("summary_generation"))
            self.assertEqual(state["generation_members"]["products"], [])
            other.save_plans({"owner": "only-parent", "changes": [{"product_id": parent["id"], "month": "2026-10", "sop": 123, "forecast": None, "revision": 0}]})
            state = other.state()
            self.assertEqual(plan_total(state["products"], "2026-10", {(r["product_id"], r["month"]): r for r in state["plans"]}), 123)
            self.assertFalse(any("缺少单代际目录" in str(w) for w in state["generation_view"]["warnings"]))
        finally:
            other.close()

    def test_group_change_is_live_and_preserves_old_parent_and_member_history(self):
        old_parent = self.parent()["id"]
        self.save(old_parent, 80); self.save("old", 10)
        self.group = "智界 New 2027款汇总"
        self.groups = {self.group: ["智界 R7 2026款"]}
        self.write_rules()
        state = self.store.state()
        self.assertNotEqual(self.parent(state)["id"], old_parent)
        self.assertNotIn(old_parent, {p["id"] for p in state["products"]})
        self.assertIn("old", {p["id"] for p in state["products"]})
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id=?", (old_parent,)).fetchone()[0], 80)
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id='old'").fetchone()[0], 10)

    def test_summary_label_format_change_keeps_stable_parent_and_its_plan(self):
        parent = self.parent()
        self.save(parent["id"], 75)
        self.group = "智界   R7 2026款汇总"
        self.groups = {self.group: ["智界 R7 2025款", "智界 R7 2026款"]}
        self.write_rules()
        updated = self.parent()
        self.assertEqual(updated["id"], parent["id"])
        self.assertEqual(updated["summary_generation"], self.group)
        self.assertGreater(updated["revision"], parent["revision"])
        self.assertEqual(self.total(), 75)

    def test_grid_parent_edit_checks_revision_and_forbids_identity_changes(self):
        parent = self.parent()
        request = self.body(changes=[{"product_id": parent["id"], "month": "2026-10", "sop": 77, "revision": 0}])
        self.assertEqual(self.store.save_grid_batch(request)["saved_count"], 1)
        self.assertEqual(self.store.save_grid_batch(request)["saved_count"], 1)
        self.assertEqual(self.total(), 77)
        with self.assertRaises(ConflictError):
            self.store.save_grid_batch(self.body(changes=[{"product_id": parent["id"], "month": "2026-10", "sop": 99, "revision": 0}]))
        with self.assertRaisesRegex(ValidationError, "共享规则"):
            self.store.save_grid_batch(self.body(catalog_changes=[{"id": parent["id"], "revision": parent["revision"], "model": "改名"}]))

    def test_matching_uses_frozen_parent_override_without_double_counting_members(self):
        parent = self.parent()
        self.save("old", 10, "2026-11"); self.save("new", 20, "2026-11")
        self.save(parent["id"], 80, "2026-11")
        self.current = datetime(2026, 11, 3, 12, tzinfo=TZ)
        self.store.ensure_daily_snapshots()
        self.save(parent["id"], 100, "2026-11")
        self.current = datetime(2026, 12, 1, 12, tzinfo=TZ)
        self.store.save_shipments({"owner": "actual", "changes": [
            {"product_id": pid, "month": "2026-11", "quantity": value, "status": "settled", "source": "SP", "batch": "", "revision": 0}
            for pid, value in [(parent["id"], 60), ("old", 5), ("new", 7)]]})
        result = self.store.matching("2026-11", model=parent["model"])
        self.assertEqual(result["actual"], 60)
        self.assertEqual(result["rows"][-1]["plan"], 80)
        self.assertEqual(result["rows"][-1]["match"], 75)

    def test_automatic_parent_snapshots_freeze_effective_value_and_rule_members(self):
        self.save("old", 10, "2026-11"); self.save("new", 20, "2026-11")
        parent = self.parent()
        manual = self.store.manual_snapshot("automatic parent")
        frozen = next(row for row in manual["plans"] if row["product_id"] == parent["id"] and row["month"] == "2026-11")
        self.assertIsNone(frozen["input_sop"])
        self.assertEqual(frozen["sop"], 30)
        self.assertTrue(frozen["derived"])
        self.current = datetime(2026, 11, 3, 12, tzinfo=TZ)
        self.store.ensure_daily_snapshots()
        self.save("old", 100, "2026-11"); self.save("new", 200, "2026-11")
        self.groups = {self.group: ["智界 R7 2026款"]}
        self.write_rules(); self.store.state()
        original = self.store.get_snapshot(manual["snapshot"]["id"])
        self.assertEqual(next(row["sop"] for row in original["plans"] if row["product_id"] == parent["id"] and row["month"] == "2026-11"), 30)
        self.assertEqual(original["generation_rules"]["groups"][self.group], ["智界 R7 2025款", "智界 R7 2026款"])
        self.current = datetime(2026, 12, 1, 12, tzinfo=TZ)
        self.store.save_shipments({"owner": "actual", "changes": [{"product_id": parent["id"], "month": "2026-11", "quantity": 30, "status": "settled", "source": "SP", "batch": "", "revision": 0}]})
        result = self.store.matching("2026-11", model=parent["model"])
        self.assertEqual(result["rows"][-1]["plan"], 30)
        self.assertEqual(result["rows"][-1]["match"], 100)
        self.assertIsNone(self.store.db.execute("SELECT sop FROM plans WHERE product_id=? AND month='2026-11'", (parent["id"],)).fetchone())

    def test_default_migration_preserves_existing_single_plans_and_legacy_detail(self):
        self.save("old", 10)
        self.store.update_settings({"generation_mode": "detail"})
        self.assertEqual({p["id"] for p in self.store.state()["products"]}, {"old", "new"})
        self.store.db.execute("DELETE FROM settings WHERE key='summary_hierarchy_version'")
        self.store.close()
        self.store = SOPStore(self.root / "test.sqlite3", self.source, lambda: self.current)
        self.assertEqual(self.store.generation_view()["mode"], "summary")
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id='old'").fetchone()[0], 10)


if __name__ == "__main__":
    unittest.main()
