import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sop_data
import sop_source_rules
import server
from sop_paths import source_directory, runtime_data_directory, plan_workbook_path
from sop_refresh import SalesRefresh
from sop_source_rules import load_merge_rules


class DeploymentPathsTest(unittest.TestCase):
    def test_relative_environment_paths_use_project_root_from_another_cwd(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            parent = Path(folder).resolve()
            root = parent / 'SOP看板'
            root.mkdir()
            elsewhere = parent / 'different-cwd'
            elsewhere.mkdir()
            rules = parent / 'rules.py'
            rules.write_text('MERGE_GROUPS = {}', encoding='utf-8')
            previous = Path.cwd()
            try:
                os.chdir(elsewhere)
                with patch.dict(os.environ, {'SHARED_DATA_ROOT': '../共享数据'}):
                    self.assertEqual(source_directory(root), parent / '共享数据' / '订单汇总')
                    self.assertEqual(plan_workbook_path(root), parent / '共享数据' / 'SOP计划' / 'SOP计划维护.xlsx')
                with patch.dict(os.environ, {'SOP_SOURCE_DIR': '../sales', 'SOP_PLAN_DIR': '../plans',
                                            'SOP_DATA_DIR': './state', 'SOP_SHARED_CONFIG': '../rules.py'}), \
                     patch.object(sop_source_rules, '__file__', str(root / 'sop_source_rules.py')):
                    self.assertEqual(source_directory(root), parent / 'sales')
                    self.assertEqual(plan_workbook_path(root), parent / 'plans' / 'SOP计划维护.xlsx')
                    self.assertEqual(runtime_data_directory(root), root / 'state')
                    self.assertEqual(load_merge_rules()['config_path'], str(rules))
            finally:
                os.chdir(previous)

    def test_server_and_import_defaults_honor_data_environment_with_explicit_precedence(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder).resolve() / 'SOP看板'
            root.mkdir()
            with patch.object(server, 'ROOT', root), patch.object(sop_data, 'ROOT', root), \
                 patch.dict(os.environ, {'SOP_DATA_DIR': './state'}):
                options, gateway = server.runtime_options([])
                self.assertIsNone(gateway)
                self.assertEqual(options.data_dir, root / 'state')
                explicit = root / 'explicit-data'
                options, _ = server.runtime_options(['--data-dir', str(explicit)])
                self.assertEqual(options.data_dir, explicit)
                self.assertEqual(runtime_data_directory(root), explicit)
                rules = root / 'rules.py'
                rules.write_text('MERGE_GROUPS = {}', encoding='utf-8')
                config = root / 'gateway.json'
                deployed = root / 'gateway-data'
                plan = root / 'gateway-plan.xlsx'
                config.write_text(json.dumps({'base_path': '/sop', 'public_origin': 'https://example.test',
                    'proxy_secret_env': 'TEST_PROXY_KEY', 'allow_writes': True,
                    'data_dir': str(deployed), 'plan_file': str(plan), 'shared_config': str(rules)}), encoding='utf-8')
                os.environ['TEST_PROXY_KEY'] = 'fixture-key-' * 4
                options, _ = server.runtime_options(['--data-dir', str(explicit), '--gateway-config', str(config)])
                self.assertEqual(options.data_dir, deployed)
                self.assertEqual(options.plan_file, plan)
                self.assertEqual(runtime_data_directory(root), deployed)
                result = {'products': [], 'sales': [], 'meta': {'data_through': None, 'metric_coverage': {}, 'quality': []}}
                with patch.object(sop_data, 'refresh', return_value=result) as refresh, \
                     patch('sys.argv', ['sop_data.py']), patch('builtins.print'):
                    sop_data.main()
                    self.assertEqual(refresh.call_args.args[1], deployed / 'sales.json')
                with patch.object(sop_data, 'refresh', return_value=result) as refresh, \
                     patch('sys.argv', ['sop_data.py', '--output', str(explicit / 'custom.json')]), patch('builtins.print'):
                    sop_data.main()
                    self.assertEqual(refresh.call_args.args[1], explicit / 'custom.json')
            self.assertFalse((root / 'state').exists())
            self.assertFalse(explicit.exists())
            self.assertFalse(deployed.exists())

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
