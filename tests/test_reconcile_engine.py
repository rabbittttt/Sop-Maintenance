import json
import unittest

from sop.reconcile_engine import reconcile, validate_record


def record(id_, metric, quantity, **changes):
    return {
        "id": id_, "metric": metric, "quantity": quantity,
        "source_id": "source-a", "source_ref": id_,
        "target_id": "r7-2027", "model": "智界 R7 2027款", "location": "",
        "period": "2026-11", "granularity": "month", "scope": "total", "status": "confirmed",
        **changes,
    }


class ReconcileEngineTests(unittest.TestCase):
    def test_supply_gap_requires_confirmed_comparable_inputs(self):
        baseline = record("plan", "sop_plan", 3000, source_id="baseline", baseline=True)
        supply = record("supply", "supply_plan", 2600)
        pending = record("pending", "supply_plan", 2800, status="pending")
        locked = record("lock", "locked_orders", 3500, source_id="baseline", baseline=True)
        result = reconcile([baseline, supply, pending, locked])
        group, = result["groups"]
        issue, = result["issues"]
        self.assertEqual(group["supply_gap"], 400)
        self.assertEqual(group["values"]["locked_orders"], 3500)
        self.assertIsNone(group["values"]["sales_actual"])
        self.assertEqual(issue["record_ids"], ["plan", "supply"])
        self.assertEqual(issue["type"], "supply_gap")
        self.assertEqual(json.loads(issue["group_key"]), ["r7-2027", "", "2026-11", "month"])
        self.assertEqual(result["stats"]["pending_records"], 1)
        self.assertEqual(result["stats"]["baseline_records"], 2)

        for change in (
            {"scope": "detail", "batch": "X"},
            {"location": "华东"},
            {"target_id": "r7-2026"},
            {"period": "2026-11-02", "granularity": "week"},
        ):
            with self.subTest(change=change):
                groups = reconcile([baseline, {**supply, **change}])["groups"]
                self.assertTrue(all(group["supply_gap"] is None for group in groups))

    def test_reference_match_dispute_and_partial_coverage(self):
        baseline = record("plan", "sop_plan", 3000, source_id="baseline", baseline=True)
        external = record("external", "sop_plan", 3000)
        supply = record("supply", "supply_plan", 3000)
        result = reconcile([baseline, external, supply])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["groups"][0]["supply_gap"], 0)

        result = reconcile([baseline, {**external, "quantity": 2800}, supply])
        group, = result["groups"]
        self.assertEqual(result["issues"][0]["type"], "source_conflict")
        self.assertEqual(group["baseline_values"]["sop_plan"], 3000)
        self.assertIsNone(group["values"]["sop_plan"])
        self.assertIsNone(group["supply_gap"])

        result = reconcile([{**baseline, "partial": True}, supply])
        group, = result["groups"]
        self.assertEqual(group["values"]["sop_plan"], 3000)
        self.assertEqual(group["partial_baseline_metrics"], ["sop_plan"])
        self.assertIsNone(group["supply_gap"])

    def test_only_distinct_details_from_same_source_are_added(self):
        first = record("first", "supply_plan", 120, scope="detail", batch="A")
        second = record("second", "supply_plan", 80, scope="detail", batch="B")
        result = reconcile([first, second])
        self.assertEqual(result["groups"][0]["values"]["supply_plan"], 200)
        self.assertEqual(result["issues"], [])

        for change, expected in (({"scope": "total"}, "mixed_scope"),
                                 ({"source_id": "source-b"}, "source_conflict"),
                                 ({"source_ref": "first"}, "possible_duplicate")):
            with self.subTest(change=change):
                result = reconcile([first, {**second, **change}])
                self.assertIsNone(result["groups"][0]["values"]["supply_plan"])
                self.assertEqual(result["issues"][0]["type"], expected)

    def test_stock_balance_requires_actual_flows_and_explicit_adjustment(self):
        evidence = [
            record(metric, metric, quantity, location="全部仓库")
            for metric, quantity in (("opening_stock", 100), ("inbound_actual", 30),
                                     ("outbound_actual", 20), ("closing_stock", 105))
        ]
        result = reconcile(evidence)
        self.assertEqual(result["issues"], [])
        self.assertIsNone(result["groups"][0]["balance"]["difference"])
        result = reconcile(evidence + [record("adjustment", "adjustments", 0, location="全部仓库")])
        self.assertEqual(result["groups"][0]["balance"]["expected_closing"], 110)
        self.assertEqual(result["groups"][0]["balance"]["difference"], -5)
        self.assertEqual(result["issues"][0]["type"], "balance_mismatch")

    def test_period_overlap_and_invalid_confirmation(self):
        month = record("month", "sop_plan", 30)
        week = record("week", "sop_plan", 10, period="2026-11-02", granularity="week")
        invalid = record("invalid", "sop_plan", True, period="2026-11-03", granularity="week")
        self.assertEqual(len(validate_record(invalid)), 2)
        result = reconcile([month, week, invalid])
        self.assertEqual({issue["type"] for issue in result["issues"]}, {"overlapping_periods", "invalid_record"})
        self.assertEqual(result["stats"]["invalid_confirmed_records"], 1)
        self.assertEqual(len(result["groups"]), 2)
        conflicting_month = record("other-month", "sop_plan", 40, source_id="source-b")
        result = reconcile([month, week, conflicting_month])
        self.assertEqual({issue["type"] for issue in result["issues"]}, {"overlapping_periods", "source_conflict"})
