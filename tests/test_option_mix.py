"""The split source is the displayed near-28-day percentage, not a recomputation."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from sop.option_mix import OptionMixError, SOURCE_NAME, read_option_mix


class OptionMixTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules = self.root / "shared_config.py"
        self.rules.write_text("MERGE_GROUPS = {'享界 X4 2026款汇总': ['享界 S9 2026款', '享界 S9T 2026款'], "
                              "'尊界 X6M 2026款汇总': ['尊界 V680 2026款']}\n", encoding="utf-8")
        rule_patch = patch("sop.source_rules.DEFAULT_CONFIG_PATH", self.rules)
        rule_patch.start()
        self.addCleanup(rule_patch.stop)
        self.path = self.root / SOURCE_NAME

    @staticmethod
    def add_sheet(book, generation, share):
        sheet = book.create_sheet(generation + "by天")
        sheet.append(["指标", "统计类型", "分类", datetime(2026, 8, 17),
                      datetime(2026, 8, 18), "近28天", "总计"])
        sheet.append(["交车锁单", "数量", "数量", 5, 5, 10, 10])
        sheet.append(["动力", "占比", "增程", .2, .3, share, 1 - share])
        sheet.append([None, None, "纯电", .8, .7, 1 - share, share])
        sheet.append([None, "数量", "增程", 2, 2, 2, 2])
        sheet.append([None, None, "纯电", 8, 8, 8, 8])
        sheet.append(["版本", "占比", "Max", 0, 0, 0, 0])
        sheet.append(["地理区域", "占比", "华东", .5, .5, .5, .5])
        sheet.append(["新维度", "占比", "新品", .2, .2, .2, .2])
        for row in (3, 4, 7, 8, 9):
            sheet.cell(row, 6).number_format = "0.00%"

    def save(self, *generations):
        book = openpyxl.Workbook()
        book.remove(book.active)
        for generation in generations:
            self.add_sheet(book, *generation)
        book.save(self.path)

    def test_source_display_value_summary_alias_and_dimension_types(self):
        self.save(("享界 X4 2026款", .7), ("享界 S9 2026款", .2))
        result = read_option_mix(self.root)
        self.assertEqual(result["status"], "ready")
        self.assertEqual((result["latest_date"], result["covered_days"], result["window_days"]),
                         ("2026-08-18", 2, 28))
        self.assertEqual({row["generation"] for row in result["rows"]},
                         {"享界 X4 2026款汇总", "享界 S9 2026款"})
        row = next(row for row in result["rows"] if row["generation"] == "享界 X4 2026款汇总"
                   and row["dimension"] == "动力" and row["option"] == "增程")
        self.assertEqual(row["source_generation"], "享界 X4 2026款")
        self.assertEqual((row["share"], row["share_display"], row["source_cell"]), (.7, "70.00%", "F3"))
        self.assertNotIn("total_share", row)
        self.assertNotIn("source_choices", result)
        self.assertEqual((row["count"], row["sample_count"]), (2, 10))
        self.assertTrue(row["is_vehicle_option"])
        self.assertFalse(next(row for row in result["rows"] if row["dimension"] == "地理区域")["is_vehicle_option"])
        self.assertIsNone(next(row for row in result["rows"] if row["dimension"] == "新维度")["is_vehicle_option"])
        self.assertEqual(result["unclassified_dimensions"], ["新维度"])
        self.assertEqual(next(row for row in result["rows"] if row["dimension"] == "版本")["share"], 0)
        self.assertEqual(result["missing_summaries"], [{"generation": "尊界 X6M 2026款汇总",
            "error": "原始锁单选配表缺少该汇总代际自身的 by天 比例。"}])

    def test_exact_summary_sheet_wins_and_stale_source_is_obvious(self):
        self.save(("享界 X4 2026款", .7))
        original = read_option_mix(self.root)
        self.save(("享界 X4 2026款", .7), ("享界 X4 2026款汇总", .9))
        updated = read_option_mix(self.root)
        summary = [row for row in updated["rows"] if row["generation"] == "享界 X4 2026款汇总"
                   and row["dimension"] == "动力" and row["option"] == "增程"]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["share"], .9)
        self.assertEqual(summary[0]["source_generation"], "享界 X4 2026款汇总")
        self.assertNotEqual(original["rows"][0]["share"], summary[0]["share"])
        self.path.write_bytes(b"broken xlsx")
        stale = read_option_mix(self.root)
        self.assertEqual(stale["status"], "stale")
        self.assertIn("暂不可读取", stale["error"])
        self.assertEqual(next(row for row in stale["rows"] if row["dimension"] == "动力"
                              and row["option"] == "增程")["share"], .9)

    def test_missing_source_and_rule_change(self):
        with self.assertRaises(OptionMixError):
            read_option_mix(self.root)
        self.save(("享界 X4 2026款", .7))
        self.assertFalse(any(row["generation"] == "享界 X4 2026款" for row in read_option_mix(self.root)["rows"]))
        self.rules.write_text("MERGE_GROUPS = {'享界 OTHER 2026款汇总': ['享界 S9 2026款']}\n", encoding="utf-8")
        result = read_option_mix(self.root)
        self.assertTrue(any(row["generation"] == "享界 X4 2026款" for row in result["rows"]))
        self.assertEqual([item["generation"] for item in result["missing_summaries"]],
                         ["享界 OTHER 2026款汇总"])

    def test_invalid_and_blank_shares_do_not_turn_into_zero(self):
        self.save(("享界 X4 2026款", .7))
        book = openpyxl.load_workbook(self.path)
        book.worksheets[0]["F3"] = 1.2
        book.worksheets[0]["F7"] = None
        book.save(self.path)
        result = read_option_mix(self.root)
        power = next(row for row in result["rows"] if row["dimension"] == "动力" and row["option"] == "增程")
        version = next(row for row in result["rows"] if row["dimension"] == "版本")
        self.assertIsNone(power["share"])
        self.assertIsNone(version["share"])
        self.assertTrue(power["source_error"])
        self.assertTrue(version["source_error"])
        self.assertEqual({issue["source_cell"] for issue in result["issues"]}, {"F3", "F7"})


if __name__ == "__main__":
    unittest.main()
