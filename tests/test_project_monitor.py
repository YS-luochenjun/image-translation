"""Monitor evidence, version bindings, bounded input and HTTP contracts."""
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import ReviewDesk, DeskError, create_server, digest
from image_localizer.project_monitor import DIMENSIONS, BINDINGS, EvidenceReader, RUBRIC_VERSION


class ProjectMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / 'runs' / 'sample'
        self.run.mkdir(parents=True)
        (self.run / 'source.png').write_bytes(b'source bytes')
        self.manifest = self.run / 'manifest.json'
        self.write(self.manifest, {'source': {'path': 'source.png'}, 'target_language': 'ja', 'regions': []})
        self.output = self.run / 'renders' / 'v1'
        self.output.mkdir(parents=True)
        (self.output / 'translated.png').write_bytes(b'output bytes')
        self.write(self.output / 'receipt.json', {'output_sha256': digest(self.output / 'translated.png'),
                   'manifest_sha256': digest(self.manifest), 'technical': 'pass'})
        self.write(self.output / 'quality.json', {'technical': 'pass', 'status': 'fail', 'review_kind': 'self'})
        self.write(self.output / 'review.json', {'output_sha256': digest(self.output / 'translated.png'),
                   'manifest_sha256': digest(self.manifest), 'review_kind': 'self'})
        self.desk = ReviewDesk(self.root)
        self.monitor = self.desk.monitor
        self.task_id = 'runs/sample/renders/v1'

    @staticmethod
    def write(path, record):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding='utf-8')

    def payload(self, task=None):
        if task is None:
            task = next(item for item in self.monitor.snapshot(fresh=True)['tasks'] if item['id'] == self.task_id)
        return {'task_id': task['id'], **{key: task[key] for key in BINDINGS},
                'reviewer': 'Human tester', 'agent_label': 'User-supplied label',
                'rubric_version': RUBRIC_VERSION, 'scores': dict.fromkeys(DIMENSIONS, None),
                'note': 'Observed source and candidate at native scale'}

    def test_separate_statuses_groups_order_and_no_inferred_runtime(self):
        other = self.root / 'work' / 'ja'
        other.mkdir(parents=True)
        (other / 'source.png').write_bytes(b'source bytes')
        self.write(other / 'manifest.json', {'source': {'path': 'source.png'}, 'target_language': 'zh-CN', 'regions': []})
        result = self.monitor.snapshot()
        self.assertEqual(2, result['summary']['tasks_total'])
        self.assertEqual({'with_output': 1, 'pending': 1}, result['summary']['production'])
        self.assertEqual(1, result['summary']['technical']['pass'])
        self.assertEqual(1, result['summary']['agent']['fail'])
        self.assertEqual(1, len(result['groups']))
        self.assertEqual(2, result['groups'][0]['language_count'])
        self.assertTrue(all(item['duration_seconds'] is None and item['cost'] is None for item in result['tasks']))
        self.assertEqual(sorted([item['updated_at'] for item in result['tasks']], reverse=True),
                         [item['updated_at'] for item in result['tasks']])
        self.assertIn(self.task_id, {item['id'] for item in self.desk.list_tasks()['tasks']})

    def test_append_history_mean_coverage_and_stale_versions(self):
        payload = self.payload()
        payload['scores']['semantic'] = 4
        before = (self.output / 'quality.json').read_bytes()
        first = self.monitor.append_evaluation(payload)
        second = self.monitor.append_evaluation(dict(payload, scores=dict.fromkeys(DIMENSIONS, None)))
        self.assertNotEqual(first['id'], second['id'])
        self.assertTrue(first['current_version'])
        result = self.monitor.snapshot()
        self.assertEqual({'total': 2, 'current': 2}, result['summary']['evaluations'])
        self.assertEqual({'mean': 4, 'scored': 1, 'total': 2}, result['summary']['dimension_scores']['semantic'])
        self.assertEqual(before, (self.output / 'quality.json').read_bytes())
        (self.output / 'translated.png').write_bytes(b'changed output')
        with self.assertRaises(DeskError) as caught:
            self.monitor.append_evaluation(payload)
        self.assertEqual(409, caught.exception.status)
        changed = self.monitor.snapshot(fresh=True)
        self.assertEqual(0, changed['summary']['evaluations']['current'])
        self.assertEqual('unknown', next(t for t in changed['tasks'] if t['id'] == self.task_id)['technical_status'])

    def test_validation_rejects_bool_float_unknown_fields_and_missing_note(self):
        for value in (True, 0, 6, 1.5, '5'):
            payload = self.payload()
            payload['scores']['semantic'] = value
            with self.subTest(value=value), self.assertRaises(DeskError) as caught:
                self.monitor.append_evaluation(payload)
            self.assertEqual(400, caught.exception.status)
        for updates in ({'note': ' '}, {'rubric_version': 'auto'}, {'automatic_score': 5}):
            with self.assertRaises(DeskError):
                self.monitor.append_evaluation(dict(self.payload(), **updates))

    def test_pending_task_only_allows_process_score(self):
        other = self.root / 'work' / 'pending'
        self.write(other / 'manifest.json', {'source': {'path': 'missing.png'}, 'target_language': 'en', 'regions': []})
        task = next(t for t in self.monitor.snapshot(fresh=True)['tasks'] if not t['has_output'])
        payload = self.payload(task)
        payload['scores']['semantic'] = 5
        with self.assertRaises(DeskError):
            self.monitor.append_evaluation(payload)
        payload['scores']['semantic'] = None
        payload['scores']['process'] = 2
        self.assertEqual(2, self.monitor.append_evaluation(payload)['scores']['process'])

    def test_feedback_current_version_is_separate_from_evaluation(self):
        payload = self.payload()
        self.desk.append_feedback(self.task_id, {**{key: payload[key] for key in BINDINGS},
                                               'reviewer': 'Tester', 'note': 'Needs changes',
                                               'decision': 'changes_requested'})
        self.monitor.append_evaluation(payload)
        result = self.monitor.snapshot(fresh=True)
        self.assertEqual(1, result['summary']['user']['changes_requested'])
        self.assertEqual(1, result['summary']['agent']['fail'])

    def test_benchmark_counts_hash_and_bounded_raw_evidence(self):
        report = self.root / 'work' / 'bench' / 'benchmark.json'
        self.write(report, {'schema_version': 1, 'technical_expectations': 'pass', 'cases': [
            {'id': 'good', 'technical': 'pass', 'report': '../../outside.json', 'report_sha256': '0' * 64},
            {'id': 'bad', 'technical': 'fail', 'known_visual_defects': ['supplied label']} ]})
        result = self.monitor.snapshot()
        benchmark = result['benchmarks'][0]
        self.assertEqual('benchmark-v1', benchmark['schema'])
        self.assertEqual({'pass': 1, 'fail': 1, 'pending': 0, 'unknown': 0}, benchmark['technical_counts'])
        self.assertEqual(digest(report), benchmark['sha256'])
        self.assertIn('known_visual_defects', benchmark['evidence']['cases'][1])
        self.assertTrue(benchmark['warnings'])
        self.assertEqual(0, result['summary']['evaluations']['total'])

    def test_corruption_duplicate_keys_depth_and_budget_warn(self):
        (self.run / 'manifest-bad.json').write_text('{', encoding='utf-8')
        (self.run / 'benchmark.json').write_text('{"schema_version":1,"schema_version":2}', encoding='utf-8')
        path = self.root / '.review-desk' / 'evaluations.jsonl'
        path.parent.mkdir()
        path.write_bytes(b'{broken\n')
        result = self.monitor.snapshot()
        self.assertGreaterEqual(len(result['warnings']), 3)
        self.assertEqual(1, result['summary']['tasks_total'])
        with patch('image_localizer.project_monitor.MAX_ENTRIES', 1):
            self.assertTrue(any('limit' in item for item in self.monitor.snapshot(fresh=True)['warnings']))
        with patch('image_localizer.project_monitor.MAX_READ_BYTES', 1):
            reader = EvidenceReader(self.root)
            self.assertEqual({}, reader.record(self.manifest))
            self.assertTrue(reader.warnings)

    def test_symlink_evidence_and_history_refused(self):
        link = self.root / 'work'
        try:
            link.symlink_to(self.run, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit creating symlinks')
        result = self.monitor.snapshot(fresh=True)
        self.assertEqual(1, len(result['tasks']))
        self.assertTrue(any('Unsafe' in warning for warning in result['warnings']))
        history = self.root / '.review-desk'
        history.symlink_to(self.run, target_is_directory=True)
        with self.assertRaises(DeskError) as caught:
            self.monitor.append_evaluation(self.payload())
        self.assertEqual(403, caught.exception.status)
        self.assertFalse((self.run / 'evaluations.jsonl').exists())

    def test_cache_copy_and_http_auth_roundtrip(self):
        first = self.monitor.snapshot()
        generated_at = first['generated_at']
        first['tasks'].clear()
        cached = self.monitor.snapshot()
        self.assertEqual(generated_at, cached['generated_at'])
        self.assertEqual(1, len(cached['tasks']))
        server = create_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('GET', '/api/monitor')
        response = connection.getresponse()
        self.assertEqual(403, response.status)
        response.read()
        headers = {'X-Review-Token': server.token, 'Origin': server.origin}
        connection.request('GET', '/api/monitor', headers=headers)
        response = connection.getresponse()
        self.assertEqual(200, response.status)
        self.assertEqual(1, json.loads(response.read())['schema_version'])
        headers['Content-Type'] = 'application/json'
        connection.request('POST', '/api/monitor/evaluations', body=json.dumps(self.payload()), headers=headers)
        response = connection.getresponse()
        self.assertEqual(201, response.status)
        self.assertTrue(json.loads(response.read())['evaluation']['current_version'])
        connection.request('GET', '/api/monitor', headers=headers)
        response = connection.getresponse()
        self.assertEqual(1, len(json.loads(response.read())['evaluations']))


if __name__ == '__main__':
    unittest.main()
