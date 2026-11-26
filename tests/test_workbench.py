from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import parse_qs, urlsplit

from image_localizer.workbench import knowledge_document, snapshot, source_bytes
from image_localizer.workbench_usage import session_usage


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.now = datetime(2026, 11, 24, 7, tzinfo=timezone.utc)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_platform_done_and_delivery_confirmation_remain_independent(self):
        entries = {
            'recent': {'code': 'MOG-20261120-001', 'state': 'blocked', 'platform_status': 'done',
                       'expected_members': 7, 'verified_members': 2},
            'historical': {'code': 'MOG-20261119-002', 'state': 'dispatched', 'platform_status': 'in_progress'},
            'verified': {'code': 'MOG-20261123-003', 'state': 'verified_complete', 'platform_status': 'done',
                         'delivery_verified_by_controller': True},
        }
        self.write('work/fireplay-monitor/assignments.json', {'requirements': entries})
        rows = {row['code']: row for row in snapshot(self.root, self.now)['requirements']}
        waiting_verification = rows['MOG-20261120-001']
        self.assertEqual('done', waiting_verification['progress'])
        self.assertFalse(waiting_verification['delivery_verified'])
        self.assertFalse(waiting_verification['archived'])
        self.assertEqual((2, 7), (waiting_verification['verified'], waiting_verification['expected']))
        self.assertTrue(rows['MOG-20261119-002']['archived'])
        self.assertTrue(rows['MOG-20261123-003']['archived'])

    def test_source_preview_is_bound_to_current_requirement_and_bytes(self):
        code = 'MOG-20261124-007'
        directory = self.root / 'state/fireplay' / code
        image = directory / 'sources' / 'source.jpg'
        image.parent.mkdir(parents=True)
        data = b'fixture source bytes'
        image.write_bytes(data)
        fingerprint = hashlib.sha256(data).hexdigest()
        record = {'code': code, 'requirement_id': 'fixture-id', 'content_version': 'version-one',
                  'sources': [{'name': 'source.jpg'}], 'downloads': {
                      '1': {'local_path': str(image), 'sha256': fingerprint, 'content_version': 'version-one'},
                  }}
        self.write(directory / 'request.json', record)
        source = snapshot(self.root, self.now)['requirements'][0]['sources'][0]
        code_query, index, version = (parse_qs(urlsplit(source['url']).query)[key][0] for key in ('code', 'index', 'sha'))
        self.assertEqual(('image/jpeg', data), source_bytes(self.root, code_query, index, version))
        image.write_bytes(b'new bytes')
        self.assertIsNone(source_bytes(self.root, code_query, index, version))
        image.write_bytes(data)
        record['content_version'] = 'version-two'
        self.write(directory / 'request.json', record)
        self.assertIsNone(source_bytes(self.root, code_query, index, version))

    def test_requirement_duration_uses_only_confirmed_bound_transitions(self):
        code = 'MOG-20261124-007'
        self.write('work/fireplay-monitor/assignments.json', {'requirements': {
            'record-id': {'code': code, 'state': 'verified_complete'},
        }})
        receipt = {'code': code, 'requirement_id': 'record-id', 'state': 'confirmed',
                   'readback_at': '2026-11-24T06:50:00Z'}
        self.write(f'state/fireplay/{code}/status-start.json',
                   {**receipt, 'target_status': 'in_progress', 'observed_updated_at': '2026-11-24T06:00:00Z'})
        finish = {**receipt, 'target_status': 'done', 'observed_updated_at': '2026-11-24T06:30:00Z'}
        finish_path = f'state/fireplay/{code}/status-finish.json'
        self.write(finish_path, finish)
        self.assertEqual(1800, snapshot(self.root, self.now)['requirements'][0]['timing']['elapsed_seconds'])
        self.write(finish_path, {**finish, 'requirement_id': 'other-id'})
        self.assertIsNone(snapshot(self.root, self.now)['requirements'][0]['timing']['elapsed_seconds'])
        self.write(finish_path, {**finish, 'observed_updated_at': '2026-11-24T06:55:00Z'})
        self.assertIsNone(snapshot(self.root, self.now)['requirements'][0]['timing']['closed_at'])

    def test_knowledge_catalog_uses_only_indexed_knowledge_documents(self):
        relative = 'knowledge/methods/example.md'
        self.write('knowledge/index.json', {'entries': [
            {'id': 'example', 'kind': 'method', 'status': 'active', 'path': relative},
            {'id': 'external', 'path': 'other.md'},
        ]})
        document = self.root / relative
        document.parent.mkdir()
        document.write_text('# 示例方法\n\n正文', encoding='utf-8')
        (self.root / 'other.md').write_text('# 外部', encoding='utf-8')
        entry, = snapshot(self.root, self.now)['knowledge']
        self.assertEqual(('示例方法', relative), (entry['title'], entry['path']))
        self.assertEqual('示例方法', knowledge_document(self.root, 'example')['title'])
        self.assertIsNone(knowledge_document(self.root, 'external'))

    def test_usage_matches_bound_project_and_deduplicates_cost_receipts(self):
        code = 'MOG-20261124-007'
        codex = self.root / 'codex'
        rollout = codex / 'sessions' / 'rollout-bound.jsonl'
        rollout.parent.mkdir(parents=True)
        event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {
            'total_tokens': 120, 'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 75,
        }}}}
        rollout.write_text(json.dumps(event) + '\n', encoding='utf-8')
        with sqlite3.connect(codex / 'state_5.sqlite') as database:
            database.execute('CREATE TABLE threads (id TEXT, cwd TEXT, tokens_used INTEGER, rollout_path TEXT)')
            database.executemany('INSERT INTO threads VALUES (?, ?, ?, ?)', [
                ('bound', str(self.root), 120, str(rollout)),
                ('unrelated', str(self.root / 'other'), 300, str(rollout)),
            ])
        journal = self.root / 'work' / 'fireplay-production' / code / 'stage-events.jsonl'
        journal.parent.mkdir(parents=True)
        receipt = {'stage': 'background', 'started_at': self.now.isoformat(), 'finished_at': self.now.isoformat(),
                   'outcome': 'completed', 'evidence': 'receipt.json', 'cost': {'amount': '1.25', 'unit': 'KIE credits'}}
        journal.write_text((json.dumps(receipt) + '\n') * 2, encoding='utf-8')
        assigned = {'code': code, 'thread_id': 'bound', 'host_id': 'local',
                    'thread_title': code, 'delivery_verified': True}
        snapshot = session_usage(self.root, [
            assigned, dict(assigned, code='MOG-20261124-008', thread_id='unrelated', delivery_verified=False),
        ], codex)['sessions']
        self.assertEqual({'total': 120, 'input': 100, 'output': 20, 'cached_input': 75}, snapshot[0]['tokens'])
        self.assertEqual({'KIE Credits': '1.25'}, snapshot[0]['costs'])
        self.assertEqual({}, snapshot[1]['tokens'])
        self.assertEqual({}, snapshot[1]['costs'])

    def test_sessions_sort_by_creation_time_instead_of_requirement_order(self):
        with sqlite3.connect(self.root / 'state_5.sqlite') as database:
            database.execute('CREATE TABLE threads (id TEXT, cwd TEXT, tokens_used INTEGER, rollout_path TEXT, '
                             'created_at INTEGER, created_at_ms INTEGER)')
            database.executemany('INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)', [
                ('older', str(self.root), 120, None, 10, None),
                ('newer', str(self.root), 0, None, 10, 10001),
            ])
        requirements = [
            {'code': code, 'thread_id': title, 'host_id': 'local', 'thread_title': title, 'delivery_verified': False}
            for code, title in [('MOG-20261124-009', 'unknown'), ('MOG-20261124-008', 'older'), ('MOG-20261124-007', 'newer')]
        ]
        sessions = session_usage(self.root, requirements, self.root)['sessions']
        self.assertEqual(['newer', 'older', 'unknown'], [session['title'] for session in sessions])
        self.assertEqual([0, 120, None], [session['tokens'].get('total') for session in sessions])

    def test_requirement_accounting_keeps_estimate_and_shared_tokens_separate(self):
        code = 'MOG-20261124-007'
        folder = f'work/fireplay-production/{code}'
        self.write(f'{folder}/audit.json', {})
        self.write(f'{folder}/pricing.json', {})
        self.write(f'{folder}/generation-cost-summary.json', {
            'audit_receipt': 'audit.json', 'pricing_receipt': 'pricing.json',
            'verified_count': 1, 'verified_generations': [{'id': 'generated-one'}],
            'estimated_hdk': 1.4, 'settled_hdk': None,
        })
        assigned = {'code': code, 'thread_id': 'shared', 'host_id': 'local',
                    'thread_title': code, 'delivery_verified': True}
        usage = session_usage(self.root, [assigned, dict(assigned, code='MOG-20261124-008')], self.root)
        self.assertEqual({'costs': {}, 'estimated_hdk': '1.4', 'verified_generations': 1,
                          'tokens': {}, 'shared_session': True}, usage['requirements'][code])
        self.assertEqual({}, usage['sessions'][0]['costs'])
