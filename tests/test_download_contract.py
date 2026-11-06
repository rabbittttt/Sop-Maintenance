"""Every successful HTTP download is registered and can be saved and imported."""
import base64
import http.client
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import openpyxl

from server import make_handler
from sop.export import PlanExport
from sop.plan_scope import is_model_plan
from sop.workbook import generation_model_groups
from sop.xlsx_export import write_export_workbook
from tests import test_model_export_roundtrip as model_fixtures


class DownloadContractTest(unittest.TestCase):
    def setUp(self):
        # Compose the fixture instance; do not expose an imported TestCase class
        # at module scope, which unittest would collect a second time.
        self.fixture = model_fixtures.ModelExportRoundtripTest(
            "test_noop_does_not_create_overrides_or_undo_new_web_values_after_refresh")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.store = self.fixture.store
        self.source_before = self.fixture.source.read_bytes()
        self.exports = self.fixture.root / "disposable-downloads"
        self.exports.mkdir()
        self.exporter = PlanExport(writer=write_export_workbook, temp_root=self.exports)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.server.RequestHandlerClass = make_handler(
            self.store, self.fixture.root, self.server.server_port,
            plan_export=self.exporter, plan_roundtrip=self.fixture.service,
            source_refresh=object())  # Never open or refresh a real source directory.
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=.02), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.parent = next(p for p in self.store.state()["products"]
                           if p.get("summary_generation") and not p.get("read_only"))

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def export_count(self):
        with self.store.lock:
            return self.store.db.execute("SELECT count(*) FROM plan_download_exports").fetchone()[0]

    def source_plans(self):
        with self.store.lock:
            return [tuple(row) for row in self.store.db.execute(
                "SELECT * FROM plans WHERE product_id IN ('a','b') ORDER BY product_id,month")]

    def request(self, path, body, *, registered_before_body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        try:
            connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            if registered_before_body is not None:
                # The service must finish registration before even the download
                # headers arrive, not after the client has received the file.
                self.assertEqual(self.export_count(), registered_before_body)
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def payload(self, versions=False, locks=False):
        state = self.store.state()
        products = [p for p in state["products"] if p["id"] == self.parent["id"]]
        if versions:
            products += [p for p in state["generation_members"]["products"]
                         if p.get("parent_generation") == self.parent["summary_generation"]]
        months = ["2026-10", "2026-12"]
        metrics = ["锁单数", "SOP计划量"] if locks else ["SOP计划量"]
        rows, ids, parents = [], [], []
        for (brand, model, parent), members in generation_model_groups(products)[0]:
            for metric in metrics:
                rows.append(["车型汇总", brand, model, *([""] if versions else []), metric, None, None, ""])
                ids.append(None); parents.append(parent)
            if versions:
                for product in members:
                    for metric in metrics:
                        if metric == "锁单数" and is_model_plan(product):
                            continue
                        rows.append(["版本明细", brand, model, product["version"], metric, None, None, ""])
                        ids.append(product["id"]); parents.append(parent)
        return {"schema_version": 1, "current_month": "2026-10", "months": months,
                "include_versions": versions, "include_locks": locks, "scope": "isolated download contract",
                "generation_mode": state["generation_view"]["mode"],
                "generation_rule_signature": state["generation_view"]["rule_signature"],
                "headers": ["层级", "品牌", "车型", *(["版本"] if versions else []), "指标", *months, "数据说明"],
                "rows": rows, "annual_plans": [{"2026": None} for _ in rows],
                "row_product_ids": ids, "row_parent_generations": parents,
                "selected_product_ids": [p["id"] for p in products]}

    def upload(self, path):
        return self.request("/api/plans/import-xlsx", {"filename": path.name,
            "content_base64": base64.b64encode(path.read_bytes()).decode("ascii")})

    def test_all_content_options_download_registered_editable_originals_that_roundtrip(self):
        source_plans = self.source_plans()
        for index, (versions, locks) in enumerate(((False, False), (False, True), (True, False), (True, True)), 1):
            with self.subTest(include_versions=versions, include_locks=locks):
                before = self.export_count()
                status, headers, content = self.request("/api/plans/export-xlsx", self.payload(versions, locks),
                                                       registered_before_body=before + 1)
                self.assertEqual(status, 200, content[:500])
                self.assertIn("attachment", headers.get("Content-Disposition", ""))
                self.assertTrue(content.startswith(b"PK"))
                original = self.fixture.root / f"download-{versions}-{locks}.xlsx"
                original.write_bytes(content)
                book = openpyxl.load_workbook(original, data_only=False)
                try:
                    self.assertEqual(set(book.sheetnames), {"SOP计划", "_sop_import"})
                    self.assertIn(book["_sop_import"].sheet_state, {"hidden", "veryHidden"})
                    self.assertEqual(book["_sop_import"].cell(3, 2).value, versions)
                    sheet = book["SOP计划"]
                    self.assertFalse(sheet.protection.sheet)
                    if not versions:
                        self.assertNotIn("版本", [cell.value for cell in sheet[3]])
                    token = book["_sop_import"].cell(2, 2).value
                    with self.store.lock:
                        registered = self.store.db.execute(
                            "SELECT payload FROM plan_download_exports WHERE token=?", (token,)).fetchone()
                    self.assertIsNotNone(registered)
                    context = json.loads(registered["payload"])
                    self.assertEqual(context["include_versions"], versions)
                    manifest = context["manifest"]
                    if versions:
                        row = next(row for row in manifest["rows"]
                                   if row.get("product_id") == self.parent["id"]
                                   and row["kind"] == "product" and row["metric"] == "SOP计划量")
                    else:
                        key = next(key for key, item in manifest["model_inputs"].items()
                                   if item["product_id"] == self.parent["id"])
                        row = next(row for row in manifest["rows"] if row["key"] == key)
                    column = next(col["column"] for col in manifest["columns"] if col.get("month") == "2026-10")
                    sheet.cell(row["row"], column).value = 100 + index
                    # Save to the exact downloaded path, retaining the original
                    # hidden marker; no replacement template or version edits.
                    book.save(original)
                finally:
                    book.close()
                status, _, reply = self.upload(original)
                self.assertEqual(status, 200, reply)
                result = json.loads(reply)
                self.assertTrue(result["ok"])
                self.assertEqual((result["imported"], result["created"]), (1, 0))
                self.assertEqual(self.fixture.value(self.parent["id"]), 100 + index)
                self.assertEqual(self.source_plans(), source_plans)
                self.assertEqual((self.fixture.value("a"), self.fixture.value("b")), (10, 20))
                self.assertEqual(self.fixture.source.read_bytes(), self.source_before)
                self.assertEqual(list(self.exports.iterdir()), [])
                self.assertFalse((self.fixture.root / "SOP计划维护.xlsx").exists())

    def test_writer_without_roundtrip_marker_cannot_deliver_a_download(self):
        before, plans = self.export_count(), self.source_plans()
        calls = []
        def missing_marker(payload):
            calls.append(payload.get("roundtrip"))
            stripped = dict(payload); stripped.pop("roundtrip", None)
            write_export_workbook(stripped)
        with patch.object(self.exporter, "writer", side_effect=missing_marker):
            status, headers, content = self.request("/api/plans/export-xlsx", self.payload())
        self.assertEqual(status, 503, content)
        self.assertNotIn("Content-Disposition", headers)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertFalse(content.startswith(b"PK"))
        self.assertIn("未通过回导校验", json.loads(content)["error"])
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0])
        self.assertEqual(self.export_count(), before)
        self.assertEqual(self.source_plans(), plans)
        self.assertEqual(list(self.exports.iterdir()), [])

    def test_old_payload_missing_ids_is_rejected_before_render_or_registration(self):
        body = self.payload()
        for field in ("row_product_ids", "row_parent_generations", "selected_product_ids",
                      "generation_mode", "generation_rule_signature"):
            body.pop(field)
        before, plans = self.export_count(), self.source_plans()
        with patch.object(self.exporter, "writer", wraps=write_export_workbook) as writer:
            status, headers, content = self.request("/api/plans/export-xlsx", body)
            writer.assert_not_called()
        self.assertEqual(status, 400, content)
        self.assertIn("刷新", json.loads(content)["error"])
        self.assertNotIn("Content-Disposition", headers)
        self.assertFalse(content.startswith(b"PK"))
        self.assertEqual(self.export_count(), before)
        self.assertEqual(self.source_plans(), plans)
        self.assertEqual(list(self.exports.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
