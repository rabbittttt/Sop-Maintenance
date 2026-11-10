"""Exercise reconciliation over the same gateway used by the workbench."""
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import openpyxl

from server import make_handler
from sop.gateway import GatewayConfig
from sop.store import SOPStore, TZ
from tests.test_portal_gateway import KEY, ORIGIN, PRINCIPAL, request


class ReconcileHTTPTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.orders = self.root / "订单汇总"
        self.orders.mkdir()
        self.docs = self.root / "对账资料"
        self.docs.mkdir()
        self.rules = self.root / "shared_config.py"
        self.rules.write_text("MERGE_GROUPS = {}", encoding="utf-8")
        self.source = self.root / "sales.json"
        self.source.write_text(json.dumps({
            "products": [{"id": "max", "brand": "智界", "model": "R7", "generation": "智界 R7 2027款", "version": "Max"}],
            "sales": [{"product_id": "max", "month": "2026-11", "metric": "locked_orders", "value": 900}],
            "model_sales": [{"brand": "智界", "model": "R7", "generation": "智界 R7 2027款", "month": "2026-11",
                             "metric": "locked_orders", "value": 1500}],
        }), encoding="utf-8")
        self.clock = lambda: datetime(2026, 11, 10, 9, tzinfo=TZ)
        environment = patch.dict("os.environ", {"SOP_SHARED_CONFIG": str(self.rules), "SOP_SOURCE_DIR": str(self.orders),
                                                "SOP_RECONCILE_DIR": str(self.docs)})
        environment.start()
        self.addCleanup(environment.stop)

    @contextmanager
    def running(self):
        store = SOPStore(self.root / "test.sqlite3", self.source, self.clock)
        http = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        http.RequestHandlerClass = make_handler(store, Path(__file__).resolve().parents[1] / "web", http.server_port,
                                                gateway=GatewayConfig("/sop", ORIGIN, KEY, True))
        thread = threading.Thread(target=lambda: http.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        try:
            yield http, store
        finally:
            http.shutdown()
            thread.join(timeout=5)
            http.server_close()
            store.close()

    def api(self, http, path="", body=None, expected=200):
        code, _, data = request(http, "/sop/api/reconcile" + path,
                                method="GET" if body is None else "POST", body=body)
        self.assertEqual(code, expected, data[:500])
        return json.loads(data)

    @staticmethod
    def record(metric, amount):
        return {"source_ref": "供货!D2", "original_text": "=1+1", "model": "R7 2027款", "period": "2026-11",
                "granularity": "month", "metric": metric, "quantity": amount, "scope": "total"}

    def test_round_reuses_sop_shows_conflict_exports_and_persists(self):
        with self.running() as (http, store):
            def save_plan(quantity, revision):
                store.save_plans({"owner": "plan", "changes": [{"product_id": "max", "month": "2026-11",
                                                                "sop": quantity, "forecast": None, "revision": revision}]})

            save_plan(3000, 0)
            snapshot = store.manual_snapshot("确认版")["snapshot"]["id"]
            round_id = self.api(http, body={"name": "月度核对", "owner": "forged", "start_month": "2026-11",
                                            "end_month": "2026-11", "snapshot_id": str(snapshot)})["id"]
            path = "/" + round_id
            round_ = self.api(http, path)
            self.assertEqual(round_["owner"], PRINCIPAL)
            target = next(target["id"] for target in round_["targets"] if target["level"] == "model")

            def model_group(detail):
                return next(group for group in detail["result"]["groups"] if group["target_id"] == target)

            self.assertEqual(model_group(round_)["values"]["locked_orders"], 1500)
            save_plan(3200, 1)
            self.assertEqual(model_group(self.api(http, path))["values"]["sop_plan"], 3000)
            xlsx = self.docs / "供应.xlsx"
            book = openpyxl.Workbook()
            book.active.append(["车型", "2026-11", "供应量"])
            book.active.append(["R7 2027款", "2026-11", 2600])
            book.save(xlsx)
            shared = self.api(http, "/shared")["files"][0]
            import_body = {"revision": round_["revision"], "source": {"name": "供应.xlsx", "type": "excel", "shared_id": shared["id"]},
                           "records": [{**self.record("supply_plan", 2600), "status": "confirmed"}]}
            round_ = self.api(http, path + "/import", import_body)
            record = round_["records"][0]
            self.assertEqual((record["status"], record["target_id"]), ("pending", target))
            self.assertIsNone(model_group(round_)["supply_gap"])
            self.assertEqual(self.api(http, path + "/import", {**import_body, "revision": round_["revision"]})["added_records"], 0)
            self.api(http, path + "/records", {"revision": 1, "changes": [{"id": record["id"], "status": "confirmed"}]}, 409)
            round_ = self.api(http, path + "/records", {"revision": round_["revision"],
                                                         "changes": [{"id": record["id"], "status": "confirmed"}]})
            self.assertEqual(model_group(round_)["supply_gap"], 400)
            self.assertEqual(request(http, "/sop/api/reconcile" + path + "/sources/" + record["source_id"] + "/download")[2], xlsx.read_bytes())
            round_ = self.api(http, path + "/import", {"revision": round_["revision"],
                "source": {"name": "计划群消息", "type": "text", "raw_text": "11月SOP计划 2800 辆"},
                "records": [self.record("sop_plan", 2800)]})
            competing = round_["records"][-1]
            round_ = self.api(http, path + "/records", {"revision": round_["revision"],
                                                         "changes": [{"id": competing["id"], "status": "confirmed"}]})
            self.assertEqual(model_group(round_)["baseline_values"]["sop_plan"], 3000)
            self.assertIsNone(model_group(round_)["supply_gap"])
            self.assertTrue(any(issue["type"] == "source_conflict" for issue in round_["result"]["issues"]))
            round_ = self.api(http, path + "/records", {"revision": round_["revision"],
                                                         "changes": [{"id": competing["id"], "status": "excluded"}]})
            code, _, blob = request(http, "/sop/api/reconcile" + path + "/export")
            self.assertEqual(code, 200)
            with BytesIO(blob) as stream:
                exported = openpyxl.load_workbook(stream)
                self.assertEqual(exported.sheetnames, ["对账结果", "异常", "资料明细", "对账基准"])
                evidence = [cell for row in exported["资料明细"] for cell in row if cell.value == "=1+1"]
                self.assertTrue(evidence)
                self.assertTrue(all(cell.data_type == "s" for cell in evidence))
                exported.close()
            self.assertIn("400", request(http, "/sop/api/reconcile" + path + "/summary")[2].decode("utf-8-sig"))
            round_ = self.api(http, path + "/baseline", {"revision": round_["revision"], "snapshot_id": ""})
            self.assertEqual(model_group(round_)["supply_gap"], 600)
            self.assertEqual(round_["baseline_version"], 2)
            # A previously selected shared version must not resolve to updated bytes.
            xlsx.write_bytes(b"changed")
            self.api(http, path + "/import", {**import_body, "revision": round_["revision"]}, 400)

        with self.running() as (http, store):
            self.assertEqual(model_group(self.api(http, path))["supply_gap"], 600)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM reconcile_baselines WHERE round_id=?", (round_id,)).fetchone()[0], 2)
