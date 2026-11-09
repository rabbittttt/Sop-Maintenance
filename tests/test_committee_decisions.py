"""Scoped committee confirmations freeze the plan saved by each grid batch."""
import copy
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sop.store import ConflictError, SOPStore, TZ, ValidationError


class CommitteeDecisionsTest(unittest.TestCase):
    target = "2026-12"
    next_target = "2027-01"
    previous = "2026-10"
    current = "2026-11"
    older_r7 = "智界|R7 2026款"
    newer_r7 = "智界|R7 2027款"
    s7 = "智界|S7 2026款"
    s9 = "享界|S9 2026款"
    group = "问界 合并 2026款汇总"
    parent_model = "问界|合并 2026款"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sop-committee-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules = self.root / "shared_config.py"
        self.rules.write_text(
            "MERGE_GROUPS = " + repr({self.group: ["问界 A 2026款", "问界 B 2026款"]}) + "\n",
            encoding="utf-8",
        )
        environment = patch.dict(os.environ, {"SOP_SHARED_CONFIG": str(self.rules)})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = self.root / "sales.json"
        products = [
            ("r7-pro", "智界", "R7", 2026, "Pro"),
            ("r7-max", "智界", "R7", 2026, "Max"),
            ("r7-new", "智界", "R7", 2027, "Max"),
            ("s7", "智界", "S7", 2026, "Max"),
            ("s9", "享界", "S9", 2026, "Max"),
            ("member-a", "问界", "A", 2026, "Max"),
            ("member-b", "问界", "B", 2026, "Max"),
        ]
        self.source.write_text(json.dumps({"meta": {}, "sales": [], "products": [
            {"id": pid, "brand": brand, "model": model, "generation": f"{brand} {model} {year}款", "version": version}
            for pid, brand, model, year, version in products
        ]}, ensure_ascii=False), encoding="utf-8")
        self.now = datetime(2026, 11, 9, 12, tzinfo=TZ)
        self.db_path = self.root / "test.sqlite3"
        self.store = SOPStore(self.db_path, self.source, lambda: self.now)
        self.addCleanup(lambda: self.store.close())
        quantities = [("r7-pro", self.target, 80), ("r7-max", self.target, 20),
                      ("r7-new", self.target, 40), ("s7", self.target, 0),
                      ("s9", self.target, 75), ("r7-pro", self.next_target, 110),
                      ("member-a", self.target, 12), ("member-b", self.target, 18)]
        self.store.save_plans({"owner": "原维护人", "changes": [
            {"product_id": pid, "month": month, "sop": value, "forecast": None, "revision": 0}
            for pid, month, value in quantities
        ]})

    def body(self, **fields):
        view = self.store.generation_view()
        return {"request_id": str(uuid.uuid4()), "owner": "计委记录人", "generation_mode": view["mode"],
                "generation_rule_signature": view["rule_signature"], "changes": [], "catalog_changes": [],
                "entries": [], **fields}

    def confirm(self, level=1, period=None, scope="all", *, brands=None, models=None,
                start_month=None, end_month=None, **fields):
        committee = {"level": level, "period": period or self.previous, "scope": scope,
                     "start_month": start_month or self.target, "end_month": end_month or self.next_target,
                     "base_revision": self.store.state()["committee"]["base_revision"]}
        if brands is not None:
            committee["brands"] = brands
        if models is not None:
            committee["model_keys"] = models
        return self.body(committee=committee, **fields)

    def edit(self, pid, sop, month=None):
        month = month or self.target
        row = self.store.db.execute("SELECT revision FROM plans WHERE product_id=? AND month=?", (pid, month)).fetchone()
        return {"product_id": pid, "month": month, "sop": sop, "revision": row["revision"] if row else 0}

    def amounts(self):
        return {(row["level"], row["model_key"], row["month"]): row["sop"]
                for row in self.store.state()["committee"]["amounts"]}

    def history(self):
        return self.store.committee_decisions()["items"]

    def saved_plan(self, pid):
        return self.store.db.execute("SELECT sop FROM plans WHERE product_id=? AND month=?", (pid, self.target)).fetchone()["sop"]

    def test_latest_scope_only_replaces_its_models_in_the_same_period_and_level(self):
        state = self.store.state()["committee"]
        self.assertEqual((state["current_period"], state["previous_period"]), (self.current, self.previous))
        self.assertEqual(state["amounts"], [])
        first = self.store.save_grid_batch(self.confirm(scope="brands", brands=["智界"]))
        self.assertEqual(first["saved_count"], 0)
        self.assertEqual((first["committee"]["scope"], first["committee"]["brands"]), ("brands", ["智界"]))
        self.assertEqual(first["committee"]["model_count"], 3)
        self.store.save_grid_batch(self.confirm(level=2, changes=[self.edit("r7-pro", 180)]))
        third = self.store.save_grid_batch(self.confirm(level=3, scope="models", models=[self.older_r7, self.s9],
            changes=[self.edit("r7-new", 60)]))
        self.assertEqual(third["saved_count"], 1)
        self.assertEqual(self.saved_plan("r7-new"), 60)
        self.store.save_grid_batch(self.confirm(scope="models", models=[self.newer_r7], changes=[self.edit("r7-new", 50)]))
        self.store.save_grid_batch(self.confirm(period="2026-09", scope="models", models=[self.older_r7],
                                               changes=[self.edit("r7-pro", 480)]))
        self.store.save_grid_batch(self.confirm(period=self.current, scope="brands", brands=["智界", "享界"],
                                               changes=[self.edit("r7-pro", 680)]))

        amounts = self.amounts()
        for level, model, month, value in (
            (1, self.older_r7, self.target, 100), (1, self.older_r7, self.next_target, 110),
            (1, self.newer_r7, self.target, 50), (1, self.s7, self.target, 0),
            (2, self.older_r7, self.target, 200), (2, self.newer_r7, self.target, 40),
            (2, self.s9, self.target, 75), (3, self.older_r7, self.target, 200), (3, self.s9, self.target, 75),
        ):
            with self.subTest(level=level, model=model, month=month):
                self.assertEqual(amounts[(level, model, month)], value)
        self.assertNotIn((1, self.s9, self.target), amounts)
        self.assertNotIn((3, self.newer_r7, self.target), amounts)
        self.assertEqual(len(self.history()), 6)
        detail = self.store.get_committee_decision(first["committee"]["id"])
        self.assertEqual(detail["decision"], first["committee"])
        self.assertEqual(detail["decision"]["owner"], "计委记录人")
        self.assertEqual({p["brand"] for p in detail["products"]}, {"智界"})
        self.assertEqual(next(row["sop"] for row in detail["plans"]
                              if (row["product_id"], row["month"]) == ("r7-pro", self.target)), 80)
        self.assertEqual(next(row["sop"] for row in detail["amounts"]
                              if (row["model_key"], row["month"]) == (self.newer_r7, self.target)), 40)

    def test_scoped_blank_masks_a_previous_value_and_zero_total_overrides_versions(self):
        self.store.save_grid_batch(self.confirm(scope="brands", brands=["智界"]))
        confirmed = self.store.save_grid_batch(self.confirm(scope="models", models=[self.older_r7, self.s7],
            changes=[self.edit("s7", None)], entries=[{
                "client_id": "model-total", "brand": "智界", "model": "R7 2026款", "plan_level": "model",
                "plans": [{"month": self.target, "sop": 0}],
            }]))
        model_id = confirmed["entry_ids"]["model-total"]
        amounts = self.amounts()
        self.assertEqual(amounts[(1, self.older_r7, self.target)], 0)
        self.assertEqual(amounts[(1, self.older_r7, self.next_target)], 110)
        self.assertIsNone(amounts.get((1, self.s7, self.target)))
        self.assertEqual(amounts[(1, self.newer_r7, self.target)], 40)
        self.assertEqual(self.saved_plan("r7-pro"), 80)
        self.store.save_grid_batch(self.confirm(scope="models", models=[self.older_r7],
                                               changes=[self.edit(model_id, None)]))
        self.assertEqual(self.amounts()[(1, self.older_r7, self.target)], 100)

    def test_summary_parent_and_single_generation_have_independent_confirmations(self):
        state = self.store.state()
        parent = next(p for p in state["products"] if p.get("summary_generation") == self.group and not p.get("read_only"))
        child_key = json.dumps(["member", self.group, "问界", "A 2026款"], ensure_ascii=False, separators=(",", ":"))
        first = self.store.save_grid_batch(self.confirm(scope="brands", brands=["问界"]))
        self.assertEqual(self.amounts()[(1, self.parent_model, self.target)], 30)
        self.assertEqual(self.amounts()[(1, child_key, self.target)], 12)
        self.store.save_grid_batch(self.confirm(scope="models", models=[self.parent_model], changes=[self.edit(parent["id"], 70)]))
        self.store.save_grid_batch(self.confirm(scope="models", models=[child_key], changes=[self.edit("member-a", 19)]))
        self.assertEqual(self.amounts()[(1, self.parent_model, self.target)], 70)
        self.assertEqual(self.amounts()[(1, child_key, self.target)], 19)
        original = self.store.get_committee_decision(first["committee"]["id"])["amounts"]
        self.assertEqual(next(row["sop"] for row in original
                              if (row["model_key"], row["month"]) == (self.parent_model, self.target)), 30)

    def test_committee_only_save_replays_after_restart_without_reconfirming(self):
        request = self.confirm(scope="models", models=[self.older_r7])
        saved = self.store.save_grid_batch(request)
        self.assertEqual(saved["saved_count"], 0)
        self.store.save_grid_batch(self.body(changes=[self.edit("r7-pro", 250)]))
        self.store.close()
        self.store = SOPStore(self.db_path, self.source, lambda: self.now)
        self.assertEqual(self.store.save_grid_batch(copy.deepcopy(request)), saved)
        self.assertEqual(self.history(), [saved["committee"]])
        self.assertEqual(self.amounts()[(1, self.older_r7, self.target)], 100)
        self.assertEqual(self.saved_plan("r7-pro"), 250)
        request["committee"]["level"] = 2
        with self.assertRaises(ValidationError):
            self.store.save_grid_batch(request)
        self.assertEqual(len(self.history()), 1)

    def test_conflicts_and_invalid_scope_do_not_save_other_cells_or_confirmations(self):
        stale = self.confirm(changes=[self.edit("s9", 76)])
        self.store.save_grid_batch(self.body(changes=[self.edit("r7-pro", 81)]))
        with self.assertRaises(ConflictError):
            self.store.save_grid_batch(stale)
        cell_stale = self.confirm(changes=[{**self.edit("s9", 76), "revision": 0}])
        with self.assertRaises(ConflictError):
            self.store.save_grid_batch(cell_stale)
        invalid = self.confirm(scope="brands", brands=["不存在"], changes=[self.edit("s9", 76)])
        with self.assertRaises(ValidationError):
            self.store.save_grid_batch(invalid)
        self.assertEqual(self.saved_plan("s9"), 75)
        self.assertEqual(self.history(), [])
        self.assertEqual(self.amounts(), {})
        for request in (stale, cell_stale, invalid):
            self.assertIsNone(self.store.db.execute("SELECT 1 FROM grid_batch_requests WHERE request_id=?",
                                                   (request["request_id"],)).fetchone())

    def test_partial_month_scope_replaces_only_covered_amounts_and_freezes_only_its_plans(self):
        broad = self.store.save_grid_batch(self.confirm(scope="brands", brands=["智界"]))
        revised = self.store.save_grid_batch(self.confirm(
            scope="models", models=[self.older_r7, self.s7], end_month=self.target,
            changes=[self.edit("r7-pro", 90), self.edit("r7-pro", 120, self.next_target), self.edit("s7", None)],
        ))
        amounts = self.amounts()
        self.assertEqual(amounts[(1, self.older_r7, self.target)], 110)
        self.assertEqual(amounts[(1, self.older_r7, self.next_target)], 110)
        self.assertEqual(amounts[(1, self.newer_r7, self.target)], 40)
        self.assertIsNone(amounts[(1, self.s7, self.target)])
        self.assertEqual(self.store.db.execute("SELECT sop FROM plans WHERE product_id=? AND month=?",
                                              ("r7-pro", self.next_target)).fetchone()["sop"], 120)
        first, second = broad["committee"], revised["committee"]
        self.assertEqual((first["start_month"], first["end_month"]), (self.target, self.next_target))
        self.assertEqual((second["start_month"], second["end_month"]), (self.target, self.target))
        self.assertEqual(self.history(), [second, first])
        detail = self.store.get_committee_decision(second["id"])
        self.assertEqual(detail["decision"], second)
        self.assertEqual({row["month"] for row in detail["plans"]}, {self.target})
        self.assertEqual({row["month"] for row in detail["amounts"]}, {self.target})

    def test_open_calendar_is_independent_of_the_rolling_view_and_validated_atomically(self):
        months = self.store.state()["committee"]["plan_months"]
        self.assertEqual((months[0], months[-1]), ("2026-01", "2027-12"))
        for bounds in (
            {"start_month": None}, {"end_month": ""}, {"start_month": "2026-1"},
            {"start_month": "2027-02"}, {"start_month": "2025-12"}, {"end_month": "2028-01"},
        ):
            with self.subTest(bounds=bounds):
                request = self.confirm(changes=[self.edit("r7-pro", 90)])
                request["committee"].update(bounds)
                with self.assertRaises(ValidationError):
                    self.store.save_grid_batch(request)
        self.assertEqual(self.saved_plan("r7-pro"), 80)
        self.assertEqual(self.history(), [])
        result = self.store.save_grid_batch(self.confirm(
            level=2, scope="models", models=[self.older_r7], start_month="2027-12", end_month="2027-12",
            changes=[self.edit("r7-pro", 321, "2027-12")],
        ))
        self.assertEqual(result["committee"]["end_month"], "2027-12")
        self.assertEqual(self.amounts(), {(2, self.older_r7, "2027-12"): 321})

    def test_history_preserves_the_months_of_existing_decisions_without_bounds_metadata(self):
        cursor = self.store.db.execute("""INSERT INTO committee_decisions
            (period, level, scope, brands, model_keys, model_count, owner, recorded_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            self.previous, 1, "models", "[]", json.dumps([self.older_r7]), 1, "旧记录", self.now.isoformat(),
            json.dumps({"products": [], "plans": []})))
        decision_id = cursor.lastrowid
        self.store.db.executemany("""INSERT INTO committee_amounts
            (decision_id, model_key, brand, model, month, sop) VALUES (?, ?, ?, ?, ?, ?)""", [
            (decision_id, self.older_r7, "智界", "R7 2026款", self.target, 95),
            (decision_id, self.older_r7, "智界", "R7 2026款", self.next_target, 105),
        ])
        decision = self.history()[0]
        self.assertEqual((decision["start_month"], decision["end_month"]), (self.target, self.next_target))
        self.assertEqual(self.store.get_committee_decision(decision_id)["decision"], decision)
        self.store.save_grid_batch(self.confirm(scope="models", models=[self.older_r7], end_month=self.target))
        self.assertEqual(self.amounts()[(1, self.older_r7, self.target)], 100)
        self.assertEqual(self.amounts()[(1, self.older_r7, self.next_target)], 105)

    def test_committee_can_confirm_unchanged_amounts_in_a_closed_month(self):
        self.now = datetime(2027, 1, 1, tzinfo=TZ)
        old_plan = self.saved_plan("r7-pro")
        result = self.store.save_grid_batch(self.confirm(
            period=self.target, scope="models", models=[self.older_r7], end_month=self.target,
        ))
        self.assertEqual(result["saved_count"], 0)
        self.assertEqual(self.saved_plan("r7-pro"), old_plan)
        self.assertEqual(self.amounts(), {(1, self.older_r7, self.target): 100})

    def test_receipt_failure_rolls_back_confirmation_and_plan_in_the_same_transaction(self):
        request = self.confirm(changes=[self.edit("r7-pro", 90)])
        self.store.db.execute("""CREATE TEMP TRIGGER reject_committee_receipt BEFORE INSERT ON grid_batch_requests
            BEGIN SELECT RAISE(ABORT, 'simulated receipt failure'); END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "simulated receipt failure"):
            self.store.save_grid_batch(request)
        self.assertEqual(self.saved_plan("r7-pro"), 80)
        self.assertEqual(self.history(), [])
        self.assertEqual(self.amounts(), {})
        self.store.db.execute("DROP TRIGGER reject_committee_receipt")
        result = self.store.save_grid_batch(request)
        self.assertEqual(self.saved_plan("r7-pro"), 90)
        self.assertEqual(self.amounts()[(1, self.older_r7, self.target)], 110)
        self.assertEqual(self.history(), [result["committee"]])
