import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sop_data
from sop_paths import source_directory, runtime_data_directory, plan_workbook_path
from sop_refresh import SalesRefresh
from sop_source_rules import load_merge_rules


class DeploymentPathsTest(unittest.TestCase):
    def test_shared_inputs_and_plan_leave_runtime_data_in_the_project(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder).resolve() / 'SOP看板'
            shared = Path(folder).resolve() / '共享数据'
            self.assertEqual(source_directory(root), root / 'input_file')
            self.assertEqual(plan_workbook_path(root), root / 'input_file' / 'SOP计划维护.xlsx')
            shared.mkdir()
            # A missing category must not silently select stale project inputs.
            self.assertEqual(source_directory(root), shared / '订单汇总')
            self.assertEqual(plan_workbook_path(root), shared / 'SOP计划' / 'SOP计划维护.xlsx')
            self.assertEqual(runtime_data_directory(root), root / 'data')
            configured = Path(folder) / 'configured-shared'
            with patch.dict(os.environ, {'SHARED_DATA_ROOT': str(configured)}):
                self.assertEqual(source_directory(root), configured / '订单汇总')
                self.assertEqual(plan_workbook_path(root), configured / 'SOP计划' / 'SOP计划维护.xlsx')
                with patch.dict(os.environ, {'SOP_SOURCE_DIR': str(root / 'explicit-sales')}):
                    self.assertEqual(source_directory(root), root / 'explicit-sales')
                    self.assertEqual(plan_workbook_path(root), configured / 'SOP计划' / 'SOP计划维护.xlsx')
                with patch.dict(os.environ, {'SOP_PLAN_DIR': str(root / 'explicit-plans')}):
                    self.assertEqual(plan_workbook_path(root), root / 'explicit-plans' / 'SOP计划维护.xlsx')

    def test_sources_settings_and_watcher_share_external_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, data = root / 'external-input', root / 'external-data'
            source.mkdir(); data.mkdir()
            (source / 'source.xlsx').write_bytes(b'not-read')
            (data / 'source_settings.json').write_text(json.dumps({'version_lock_basis': 'model_lock_row'}))
            with patch.dict(os.environ, {'SOP_SOURCE_DIR': str(source), 'SOP_DATA_DIR': str(data)}):
                self.assertEqual(source_directory(root / 'service'), source)
                self.assertEqual(runtime_data_directory(root / 'service'), data)
                self.assertEqual(sop_data._resolve_lock_basis(), 'model_lock_row')
                watcher = SalesRefresh(None, root=root / 'service')
                names = {item[0] for item in watcher.fingerprint()}
                self.assertIn(str(source / 'source.xlsx'), names)
                self.assertIn(str(data / 'source_settings.json'), names)
                self.assertFalse(any('service' in name for name in names))

    def test_shared_rules_are_read_from_explicit_deployment_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            rules = Path(folder) / 'shared.py'
            rules.write_text('MERGE_GROUPS = {}', encoding='utf-8')
            with patch.dict(os.environ, {'SOP_SHARED_CONFIG': str(rules)}):
                self.assertEqual(load_merge_rules()['groups'], {})
                self.assertEqual(load_merge_rules()['config_path'], str(rules))

    def test_explicit_source_argument_keeps_precedence_over_deployment_environment(self):
        with patch.dict(os.environ, {'SOP_SOURCE_DIR': '/deployment/input'}), \
             patch.object(sop_data, '_extract_version_reference', return_value={}) as source, \
             patch.object(sop_data, '_model_monthly_locks', return_value=[]), \
             patch.object(sop_data, '_build_lock_data', return_value={'meta': {}}):
            sop_data.extract('/explicit/input', version_lock_basis='unconfirmed')
            source.assert_called_once_with(Path('/explicit/input'), as_of=None)
