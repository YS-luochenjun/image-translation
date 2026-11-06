"""Real loopback workflow HTTP integration; all writes use isolated fixtures."""
import base64
from http.client import HTTPConnection
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import create_server

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class WorkflowHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'runs' / 'sample'
        self.run.mkdir(parents=True)
        (self.run / 'source.png').write_bytes(PNG)
        (self.run / 'translated.png').write_bytes(PNG)
        (self.run / 'manifest.json').write_text(json.dumps({'source': {'path': 'source.png'},
            'regions': [{'id': 'title', 'source_text': 'Hello', 'target_text': '你好', 'bbox': [0, 0, 1, 1]}]}), encoding='utf-8')
        self.server = create_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, payload=None, headers=None, token=True):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {'X-Review-Token': self.server.token} if token else {}
        request_headers.update(headers or {})
        body = payload if isinstance(payload, bytes) else json.dumps(payload) if payload is not None else None
        if body is not None:
            request_headers.setdefault('Content-Type', 'application/json')
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def post(self, action, payload, expected=200):
        status, body, _ = self.request('POST', '/api/workflow/' + action, payload)
        self.assertEqual(expected, status, body)
        return json.loads(body)

    def metadata(self, sid='session-a', **extra):
        return {'session_id': sid, 'cwd': str(self.root), 'title': 'Project session',
                'status': 'idle', 'events': [], **extra}

    def register(self, sid='session-a', events=None):
        return self.post('ingest', {'sessions': [self.metadata(sid, events=events or [])]})

    def produce(self, sid='session-a'):
        event = {'event_id': 'render-1', 'tool_name': 'ops_bash', 'phase': 'completed',
                 'run_root': 'runs/sample', 'stage': 'basic.render', 'command_kind': 'render',
                 'output_paths': ['runs/sample/translated.png']}
        self.register(sid, [event])
        return self.snapshot([sid])['sessions'][0]['runs'][0]

    def snapshot(self, ids):
        return self.post('snapshot', {'session_ids': ids})

    def comment_payload(self, run):
        artifact = run['result']
        return {'session_id': run['session_id'], 'run_id': run['id'], 'node_id': 'result',
                'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256'],
                'request_id': 'request-1', 'note': 'Please fix the title position', 'bbox': [0, 0, 1, 1]}

    def asset_path(self, path, sha256):
        return '/api/workflow/asset?' + urlencode({'path': path, 'sha256': sha256})

    def test_every_workflow_route_requires_private_header(self):
        for action in ('snapshot', 'index', 'ingest', 'comments', 'delivery', 'results/save', 'bind'):
            with self.subTest(action=action):
                status, _, _ = self.request('POST', '/api/workflow/' + action, {}, token=False)
                self.assertEqual(403, status)
        status, _, _ = self.request('GET', self.asset_path('runs/sample/source.png', hashlib.sha256(PNG).hexdigest()), token=False)
        self.assertEqual(403, status)

    def test_index_route_is_private_bounded_and_does_not_build_a_graph(self):
        self.register(events=[{'event_id': 'started', 'tool_name': 'ops_bash', 'phase': 'started',
                              'command': 'python bin/it.py render runs/sample'}])
        flow = self.server.desk.workflow
        before = flow._ledger_path().read_bytes()
        with patch.object(flow, '_inventory', side_effect=AssertionError('Index inventory')):
            with patch.object(flow, '_artifact', side_effect=AssertionError('Index image read')):
                index = self.post('index', {'session_ids': ['session-a']})
        session = index['sessions'][0]
        self.assertFalse(session['detail_loaded'])
        self.assertEqual('runs/sample', session['runs'][0]['root'])
        self.assertEqual([], session['runs'][0]['nodes'])
        self.assertEqual(before, flow._ledger_path().read_bytes())
        self.assertEqual([], self.post('index', {'session_ids': ['unrelated']})['sessions'])
        for payload in ({}, [], {'session_ids': ['']}, {'session_ids': [0]}, {'session_ids': ['x'] * 101}):
            self.post('index', payload, expected=400)
        status, _, _ = self.request('GET', '/api/workflow/index')
        self.assertEqual(404, status)

    def test_origin_host_and_cross_site_headers_are_rejected(self):
        for headers in ({'Origin': 'https://untrusted.invalid'}, {'Host': 'untrusted.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                status, _, _ = self.request('POST', '/api/workflow/snapshot', {'session_ids': []}, headers)
                self.assertEqual(403, status)
        self.assertEqual([], self.snapshot([])['sessions'])

    def test_metadata_registration_then_explicit_manual_binding(self):
        result = self.register()
        self.assertEqual(0, result['accepted'])
        initial = self.snapshot(['session-a'])
        self.assertEqual([], initial['sessions'][0]['runs'])
        self.assertEqual('runs/sample', initial['unassigned'][0]['root'])
        bound = self.post('bind', {'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True})
        run = self.snapshot(['session-a'])['sessions'][0]['runs'][0]
        self.assertEqual(bound['run_id'], run['id'])
        self.assertEqual('runs/sample', run['root'])
        self.assertEqual('review_pending', run['status'])

    def test_registration_rejects_nonproject_or_relative_cwd(self):
        for cwd in (str(self.run), str(self.root.parent), '.'):
            self.post('ingest', {'sessions': [self.metadata(cwd=cwd)]}, expected=403)
        self.assertEqual([], self.snapshot(['session-a'])['sessions'])

    def test_snapshot_filters_verified_sessions_and_caps_requests(self):
        self.produce('session-a')
        self.produce('session-b')
        result = self.snapshot(['session-a'])
        self.assertEqual(['session-a'], [session['session_id'] for session in result['sessions']])
        self.assertTrue(result['sessions'][0]['runs'][0]['binding_conflict'])
        self.assertEqual([], self.snapshot([])['sessions'])
        for invalid in ({}, {'session_ids': 'session-a'}, {'session_ids': [None]}, {'session_ids': ['x'] * 101}):
            self.post('snapshot', invalid, expected=400)

    def test_snapshot_rejects_json_nonobject_with_bad_request(self):
        for invalid in (b'[]', b'null', b'"text"'):
            status, body, _ = self.request('POST', '/api/workflow/snapshot', invalid)
            self.assertEqual(400, status, body)

    def test_ingest_comment_delivery_and_snapshot_roundtrip(self):
        run = self.produce()
        payload = self.comment_payload(run)
        response = self.post('comments', payload, expected=201)
        comment = response['comment']
        for key in ('session_id', 'run_id', 'node_id', 'artifact_id', 'artifact_sha256', 'request_id'):
            self.assertEqual(payload[key], comment[key])
        self.assertEqual('session-a', response['session_id'])
        self.assertIn(comment['id'], response['prompt'])
        delivery = {'session_id': 'session-a', 'comment_id': comment['id'], 'request_id': comment['request_id'], 'status': 'queued'}
        self.post('delivery', delivery)
        self.post('delivery', dict(delivery, status='failed', error='Disconnected'))
        restored = self.snapshot(['session-a'])['sessions'][0]['runs'][0]
        self.assertEqual([comment], restored['comments'])
        self.assertEqual(['queued', 'failed'], [item['status'] for item in restored['deliveries']])
        self.post('delivery', dict(delivery, session_id='session-b'), expected=409)
        self.assertFalse((self.run / 'review.json').exists())

    def test_comment_hash_and_cross_session_identity_are_conflicts(self):
        run = self.produce()
        payload = self.comment_payload(run)
        self.post('comments', dict(payload, artifact_sha256='0' * 64), expected=409)
        self.post('comments', dict(payload, session_id='session-b'), expected=409)
        (self.run / 'translated.png').write_bytes(PNG + b'new')
        self.post('comments', payload, expected=409)
        self.assertEqual([], self.snapshot(['session-a'])['sessions'][0]['runs'][0]['comments'])

    def test_asset_allowlist_hash_checks_and_json_readback(self):
        run = self.produce()
        artifact = run['result']
        status, body, headers = self.request('GET', self.asset_path(artifact['path'], artifact['sha256']))
        self.assertEqual(200, status)
        self.assertEqual(PNG, body)
        self.assertEqual('image/png', headers['Content-Type'])
        self.assertEqual(409, self.request('GET', self.asset_path(artifact['path'], '0' * 64))[0])
        for path in ('../outside.png', '.review-desk/workflows/ledger.jsonl', 'runs/sample/unindexed.json'):
            self.assertEqual(404, self.request('GET', self.asset_path(path, artifact['sha256']))[0])
        self.assertEqual(400, self.request('GET', '/api/workflow/asset?path=runs/sample/source.png')[0])
        manifest = next(item for node in run['nodes'] for item in node['artifacts'] if item['name'] == 'manifest.json')
        status, body, headers = self.request('GET', self.asset_path(manifest['path'], manifest['sha256']))
        self.assertEqual(200, status)
        self.assertIn('regions', json.loads(body))
        self.assertIn('application/json', headers['Content-Type'])

    def test_save_result_copy_idempotency_and_refresh(self):
        run = self.produce()
        artifact = run['result']
        payload = {'session_id': 'session-a', 'run_id': run['id'], 'artifact_id': artifact['id'], 'sha256': artifact['sha256']}
        first = self.post('results/save', payload, expected=201)
        second = self.post('results/save', payload, expected=201)
        self.assertFalse(first['idempotent'])
        self.assertTrue(second['idempotent'])
        self.assertEqual(first['saved'], second['saved'])
        saved = first['saved']
        self.assertEqual(PNG, (self.root / saved['path']).read_bytes())
        self.assertEqual([saved], self.snapshot(['session-a'])['sessions'][0]['runs'][0]['saved_results'])
        status, body, _ = self.request('GET', self.asset_path(saved['path'], saved['sha256']))
        self.assertEqual(200, status)
        self.assertEqual(PNG, body)
        (self.run / 'translated.png').write_bytes(PNG + b'new')
        self.post('results/save', payload, expected=409)
        self.assertEqual(PNG, (self.root / saved['path']).read_bytes())

    def test_invalid_payloads_and_methods_are_controlled_errors(self):
        for action in ('ingest', 'comments', 'delivery', 'results/save', 'bind'):
            status, _, _ = self.request('POST', '/api/workflow/' + action, b'[]')
            self.assertEqual(400, status)
        self.assertEqual(400, self.request('POST', '/api/workflow/ingest', b'{')[0])
        self.assertEqual(415, self.request('POST', '/api/workflow/snapshot', b'{}', {'Content-Type': 'text/plain'})[0])
        self.assertEqual(404, self.request('GET', '/api/workflow/snapshot')[0])
        self.assertEqual(405, self.request('OPTIONS', '/api/workflow/snapshot')[0])


if __name__ == '__main__':
    unittest.main()
