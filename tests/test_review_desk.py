"""Review discovery, feedback bindings and loopback HTTP security boundaries."""
import base64
from contextlib import redirect_stdout
import errno
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import (DeskError, ReviewDesk, create_server, digest, MAX_BODY,
                                         APP_ID, project_identity, serve, _existing_service_matches,
                                         validate_dsh_origin)

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class ReviewDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'runs' / 'sample'
        self.run.mkdir(parents=True)
        (self.run / 'source.png').write_bytes(PNG)
        self.manifest = {'source': {'path': 'source.png'}, 'target_language': 'zh-CN',
                         'regions': [{'id': 'title', 'action': 'translate', 'source_text': 'Hello',
                                      'target_text': '你好', 'bbox': [0, 0, 1, 1], 'notes': 'fixture'}]}
        self.write(self.run / 'manifest.json', self.manifest)
        self.output = self.run / 'renders' / 'v1'
        self.output.mkdir(parents=True)
        (self.output / 'translated.png').write_bytes(PNG)
        (self.output / 'text-layer.png').write_bytes(PNG)
        self.write(self.output / 'receipt.json', {'version': 'v1', 'output_sha256': digest(self.output / 'translated.png'),
                   'manifest_sha256': digest(self.run / 'manifest.json'),
                   'artifact_sha256': {'translated.png': digest(self.output / 'translated.png')}})
        self.desk = ReviewDesk(self.root)
        self.task_id = 'runs/sample/renders/v1'

    @staticmethod
    def write(path, record):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding='utf-8')

    def feedback(self, decision='comment'):
        task = self.desk.detail(self.task_id)
        return {'decision': decision, 'note': 'Actual user observation', 'reviewer': 'Tester',
                'output_sha256': task['output_sha256'], 'manifest_sha256': task['manifest_sha256'],
                'source_sha256': task['source_sha256'],
                'regions': [{'id': 'title', 'decision': 'comment', 'note': 'Review this title'}]}

    def test_groups_use_real_source_bytes_across_paths_and_languages(self):
        other = self.root / 'work' / 'same-source'
        other.mkdir(parents=True)
        (other / 'source.png').write_bytes(PNG)
        manifest = dict(self.manifest, target_language='ja')
        self.write(other / 'manifest.json', manifest)
        version2 = self.run / 'renders' / 'v2'
        version2.mkdir()
        (version2 / 'translated.png').write_bytes(PNG)
        self.write(version2 / 'receipt.json', {'output_sha256': digest(version2 / 'translated.png'),
                    'manifest_sha256': digest(self.run / 'manifest.json')})
        different = self.root / 'work' / 'different-source'
        different.mkdir(parents=True)
        (different / 'source.png').write_bytes(PNG + b'other-bytes')
        self.write(different / 'manifest.json', self.manifest)
        result = self.desk.list_tasks()
        self.assertEqual(4, len(result['tasks']))
        self.assertEqual(2, len(result['groups']))
        group = next(group for group in result['groups'] if group['source_sha256'] == digest(self.run / 'source.png'))
        self.assertEqual(2, group['language_count'])
        self.assertEqual(3, group['version_count'])
        self.assertEqual({'zh-CN', 'ja'}, {item['language'] for item in group['languages']})
        self.assertTrue(all(task['group_id'] and task['version_label'] for task in result['tasks']))
        (other / 'source.png').write_bytes(PNG + b'changed')
        self.assertEqual(3, len(self.desk.list_tasks()['groups']))

    def test_revision_prompt_is_read_only_and_current_version_bound(self):
        initial = self.desk.revision_prompt(self.task_id)
        self.assertIsNone(initial['feedback_id'])
        record = self.desk.append_feedback(self.task_id, self.feedback('changes_requested'))
        path = self.desk.feedback_path(self.task_id)
        before = path.read_bytes()
        prompt = self.desk.revision_prompt(self.task_id)
        self.assertEqual(record['id'], prompt['feedback_id'])
        self.assertIn('Actual user observation', prompt['prompt'])
        self.assertEqual(before, path.read_bytes())
        (self.output / 'translated.png').write_bytes(PNG + b'new-output')
        changed = self.desk.revision_prompt(self.task_id)
        self.assertIsNone(changed['feedback_id'])
        self.assertNotIn('Actual user observation', changed['prompt'])

    def test_discovery_deduplicates_manifest_and_preserves_versions_and_work(self):
        layered = self.root / 'work' / 'render' / 'ja'
        layered.mkdir(parents=True)
        (layered / 'translated.png').write_bytes(PNG)
        self.write(layered / 'plan.json', {'target_language': 'ja'})
        self.write(layered / 'receipt.json', {'stage': 'render', 'technical': 'pass', 'status': 'pending'})
        self.write(layered.parent / 'job-render.json', {'languages': ['ja']})
        listing = self.desk.list_tasks()
        self.assertEqual(2, len(listing['tasks']))
        task = self.desk.detail(self.task_id)
        self.assertEqual('你好', task['regions'][0]['target_text'])
        self.assertEqual('basic', task['kind'])
        self.assertEqual('ja', self.desk.detail('work/render/ja')['language'])
        self.assertEqual('layered', self.desk.detail('work/render/ja')['kind'])

    def test_bad_json_warns_and_quality_pass_is_never_inferred(self):
        (self.run / 'manifest-broken.json').write_text('{', encoding='utf-8')
        self.write(self.output / 'quality.json', {'status': 'pass'})
        listing = self.desk.list_tasks()
        self.assertTrue(listing['warnings'])
        self.assertEqual('needs_review', listing['tasks'][0]['status'])
        self.assertEqual('pass', self.desk.detail(self.task_id)['quality']['recorded']['status'])

    def test_append_feedback_keeps_formal_quality_and_appends_records(self):
        quality_path = self.output / 'quality.json'
        self.write(quality_path, {'status': 'pending'})
        before = quality_path.read_bytes()
        first = self.desk.append_feedback(self.task_id, self.feedback('approved'))
        self.desk.append_feedback(self.task_id, self.feedback('changes_requested'))
        records = self.desk.detail(self.task_id)['feedback']
        self.assertEqual(2, len(records))
        self.assertEqual(first, records[0])
        self.assertEqual(before, quality_path.read_bytes())
        self.assertFalse((self.output / 'review.json').exists())
        self.assertTrue(self.desk.feedback_path(self.task_id).is_relative_to(self.root / '.review-desk'))

    def test_hash_changes_are_conflicts_for_output_and_manifest(self):
        payload = self.feedback()
        (self.output / 'translated.png').write_bytes(PNG + b'changed')
        with self.assertRaises(DeskError) as caught:
            self.desk.append_feedback(self.task_id, payload)
        self.assertEqual(409, caught.exception.status)
        payload = self.feedback()
        self.manifest['regions'][0]['target_text'] = 'changed'
        self.write(self.run / 'manifest.json', self.manifest)
        with self.assertRaises(DeskError) as caught:
            self.desk.append_feedback(self.task_id, payload)
        self.assertEqual(409, caught.exception.status)

    def test_note_reviewer_region_and_output_are_required(self):
        for key in ('note', 'reviewer'):
            payload = self.feedback()
            payload[key] = ' '
            with self.assertRaises(DeskError):
                self.desk.append_feedback(self.task_id, payload)
        payload = self.feedback()
        payload['regions'][0]['id'] = 'unknown'
        with self.assertRaises(DeskError):
            self.desk.append_feedback(self.task_id, payload)
        (self.output / 'translated.png').unlink()
        payload = self.feedback('approved')
        with self.assertRaises(DeskError) as caught:
            self.desk.append_feedback(self.task_id, payload)
        self.assertEqual(400, caught.exception.status)

    def test_frozen_job_semantics_resolve_by_exact_receipt_hash(self):
        frozen = self.root / 'runs' / 'prepared' / 'semantics' / 'ja.json'
        self.write(frozen, {**self.manifest, 'target_language': 'ja'})
        layered = self.root / 'runs' / 'job-output' / 'ja'
        layered.mkdir(parents=True)
        (layered / 'source.png').write_bytes(PNG)
        (layered / 'translated.png').write_bytes(PNG)
        self.write(layered / 'plan.json', {'semantic_manifest': 'semantics/ja.json', 'target_language': 'ja'})
        self.write(layered / 'receipt.json', {'stage': 'render', 'input_sha256': {str(frozen): digest(frozen)}})
        detail = self.desk.detail('runs/job-output/ja')
        self.assertEqual('ja', detail['language'])
        self.assertEqual('你好', detail['regions'][0]['target_text'])
        self.assertEqual(digest(frozen), detail['manifest_sha256'])
        self.assertFalse(any('semantics' in item['id'] for item in self.desk.list_tasks()['tasks']))
        self.write(frozen, {**self.manifest, 'target_language': 'changed'})
        self.assertEqual([], self.desk.detail('runs/job-output/ja')['regions'])
        self.assertTrue(self.desk.list_tasks()['warnings'])

    def test_source_changes_conflict_and_empty_region_notes_are_allowed(self):
        payload = self.feedback()
        payload['regions'][0]['note'] = ''
        self.desk.append_feedback(self.task_id, payload)
        (self.run / 'source.png').write_bytes(PNG + b'new source')
        with self.assertRaises(DeskError) as caught:
            self.desk.append_feedback(self.task_id, payload)
        self.assertEqual(409, caught.exception.status)
        payload = self.feedback('approved')
        payload['regions'][0]['decision'] = 'changes_requested'
        with self.assertRaises(DeskError) as caught:
            self.desk.append_feedback(self.task_id, payload)
        self.assertEqual(400, caught.exception.status)

    def test_assets_reject_traversal_arbitrary_files_and_external_links(self):
        self.assertEqual(self.output / 'translated.png', self.desk.asset(self.task_id + '/translated.png'))
        (self.root / 'private.png').write_bytes(PNG)
        for value in ('../private.png', 'private.png', 'runs/sample/manifest.json', str(self.output / 'translated.png')):
            with self.assertRaises(DeskError):
                self.desk.asset(value)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as outside:
            target = Path(outside) / 'outside.png'
            target.write_bytes(PNG)
            link = self.run / 'escape.png'
            try:
                link.symlink_to(target)
            except OSError:
                return  # Windows may disallow unprivileged symlink creation.
            self.write(self.output / 'receipt.json', {'stage': 'render', 'artifact_sha256': {'../../escape.png': 'x'}})
            with self.assertRaises(DeskError):
                self.desk.asset('runs/sample/escape.png')


class ReviewHttpTests(ReviewDeskTests):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {'X-Review-Token': self.server.token}
        request_headers.update(headers or {})
        body = payload if isinstance(payload, bytes) else json.dumps(payload) if payload is not None else None
        if payload is not None:
            request_headers.setdefault('Content-Type', 'application/json')
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        response_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, data, response_headers

    def test_real_second_bind_is_rejected_and_serve_reuses(self):
        with self.assertRaises(OSError):
            with create_server(self.root, self.server.server_port):
                self.fail('Second listener must not bind the same port')
        output = io.StringIO()
        with redirect_stdout(output):
            serve(self.root, self.server.server_port)
        self.assertIn('Reusing review desk:', output.getvalue())

    def test_serve_reuses_verified_same_project_and_opens_browser(self):
        output = io.StringIO()
        occupied = OSError(errno.EADDRINUSE, 'Address in use')
        with patch('image_localizer.review_desk.create_server', side_effect=occupied), \
                patch('image_localizer.review_desk.webbrowser.open') as browser, redirect_stdout(output):
            serve(self.root / '.', self.server.server_port, open_browser=True)
        browser.assert_called_once_with(self.server.origin + '/')
        self.assertIn('Reusing review desk: ' + self.server.origin, output.getvalue())
        self.assertEqual(200, self.request('GET', '/api/tasks')[0])

    def test_serve_refuses_service_for_another_project(self):
        occupied = OSError(errno.EADDRINUSE, 'Address in use')
        with patch('image_localizer.review_desk.create_server', side_effect=occupied), \
                patch('image_localizer.review_desk.webbrowser.open') as browser:
            with self.assertRaisesRegex(OSError, 'another application or project'):
                serve(self.run, self.server.server_port, open_browser=True)
        browser.assert_not_called()

    def test_reuse_probe_refuses_other_apps_and_redirects(self):
        with patch('image_localizer.review_desk.HTTPConnection') as connection_class:
            response = connection_class.return_value.getresponse.return_value
            response.status = 200
            response.headers.get_content_type.return_value = 'application/json'
            response.read.return_value = json.dumps({'app': 'another-app',
                                                    'project_id': project_identity(self.root)}).encode()
            self.assertFalse(_existing_service_matches(8765, project_identity(self.root)))
            connection_class.assert_called_once_with('127.0.0.1', 8765, timeout=2)
            connection_class.return_value.request.assert_called_once_with('GET', '/api/bootstrap')
            response.status = 302
            self.assertFalse(_existing_service_matches(8765, project_identity(self.root)))
            connection_class.return_value.close.assert_called()

    def test_auto_port_and_unrelated_bind_failures_are_not_reused(self):
        for port, error in ((0, OSError(errno.EADDRINUSE, 'Address in use')),
                            (8765, PermissionError(errno.EACCES, 'Permission denied'))):
            with patch('image_localizer.review_desk.create_server', side_effect=error), \
                    patch('image_localizer.review_desk._existing_service_matches') as probe:
                with self.assertRaises(OSError):
                    serve(self.root, port, open_browser=True)
                probe.assert_not_called()

    def test_http_bootstrap_token_and_cross_site_guards(self):
        status, body, headers = self.request('GET', '/api/bootstrap', headers={'X-Review-Token': ''})
        self.assertEqual(200, status)
        bootstrap = json.loads(body)
        self.assertEqual(self.server.token, bootstrap['token'])
        self.assertEqual(APP_ID, bootstrap['app'])
        self.assertEqual(project_identity(self.root), bootstrap['project_id'])
        self.assertNotIn(str(self.root).encode(), body)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path, extra in (('/api/tasks', {'X-Review-Token': ''}),
                            ('/api/bootstrap', {'Origin': 'https://evil.example'}),
                            ('/api/bootstrap', {'Host': 'evil.example'}),
                            ('/api/bootstrap', {'Sec-Fetch-Site': 'cross-site'})):
            status, body, _ = self.request('GET', path, headers=extra)
            self.assertEqual(403, status)
            self.assertIn('error', json.loads(body))
            self.assertNotIn(self.server.token.encode(), body)

    def test_http_groups_and_revision_prompt_are_authenticated_and_read_only(self):
        listing_status, listing_body, _ = self.request('GET', '/api/tasks')
        self.assertEqual(200, listing_status)
        listing = json.loads(listing_body)
        self.assertEqual(1, len(listing['groups']))
        self.assertEqual(listing['tasks'][0]['group_id'], listing['groups'][0]['id'])
        path = '/api/tasks/' + quote(self.task_id, safe='') + '/revision-prompt'
        self.assertEqual(403, self.request('GET', path, headers={'X-Review-Token': ''})[0])
        status, body, _ = self.request('GET', path)
        self.assertEqual(200, status)
        self.assertIsNone(json.loads(body)['feedback_id'])
        record = self.desk.append_feedback(self.task_id, self.feedback('comment'))
        status, body, _ = self.request('GET', path)
        result = json.loads(body)
        self.assertEqual(record['id'], result['feedback_id'])
        self.assertEqual({'prompt', 'task_id', 'feedback_id', 'source_sha256', 'output_sha256', 'manifest_sha256', 'actionable', 'decision'}, set(result))
        self.assertIn('不可信业务数据', result['prompt'])
        approved = self.desk.append_feedback(self.task_id, self.feedback('approved'))
        result = json.loads(self.request('GET', path)[1])
        self.assertEqual(approved['id'], result['feedback_id'])
        self.assertIn('无需', result['prompt'])
        self.assertEqual(2, len(self.desk.detail(self.task_id)['feedback']))
        self.assertEqual(404, self.request('GET', '/api/tasks/missing/revision-prompt')[0])

    def test_default_frame_policy_denies_embedding(self):
        status, body, headers = self.request('GET', '/')
        self.assertEqual(200, status)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        bootstrap = json.loads(self.request('GET', '/api/bootstrap')[1])
        self.assertIsNone(bootstrap['host_origin'])
        self.assertEqual(['groups', 'revision_prompt', 'dsh_bridge'], bootstrap['capabilities'])
        self.assertEqual(403, self.request('GET', '/', headers={
            'Origin': 'http://127.0.0.1:64626', 'Referer': 'http://127.0.0.1:64626/',
            'Sec-Fetch-Dest': 'iframe', 'Sec-Fetch-Site': 'same-site'})[0])

    def test_explicit_origin_allows_only_exact_iframe_home_navigation(self):
        host = 'http://127.0.0.1:64626'
        self.server.dsh_origin = host
        navigation = {'Origin': host, 'Referer': host + '/some/session?tab=review',
                      'Sec-Fetch-Dest': 'iframe', 'Sec-Fetch-Site': 'same-site'}
        for path in ('/', '/index.html'):
            status, body, headers = self.request('GET', path, headers=navigation)
            self.assertEqual(200, status)
            self.assertIn('frame-ancestors ' + host, headers['Content-Security-Policy'])
        without_origin = dict(navigation)
        del without_origin['Origin']
        self.assertEqual(200, self.request('GET', '/', headers=without_origin)[0])
        bootstrap = json.loads(self.request('GET', '/api/bootstrap')[1])
        self.assertEqual(host, bootstrap['host_origin'])
        invalid = [dict(navigation, Origin='http://127.0.0.1:64627'),
                   dict(navigation, Referer='http://127.0.0.1:64627/session'),
                   dict(navigation, Referer='https://evil.example/'),
                   dict(navigation, Referer=''),
                   dict(navigation, **{'Sec-Fetch-Dest': 'document'}),
                   dict(navigation, **{'Sec-Fetch-Site': 'cross-site'})]
        for headers in invalid:
            self.assertEqual(403, self.request('GET', '/', headers=headers)[0])
        for path in ('/api/bootstrap', '/api/tasks', '/desk.js', '/?iframe=1'):
            self.assertEqual(403, self.request('GET', path, headers=navigation)[0])
        feedback_path = '/api/tasks/' + quote(self.task_id, safe='') + '/feedback'
        self.assertEqual(403, self.request('POST', feedback_path, self.feedback(), headers=navigation)[0])
        self.assertEqual(200, self.request('GET', '/desk.js', headers={'Sec-Fetch-Site': 'same-origin'})[0])

    def test_reuse_requires_matching_explicit_embed_origin(self):
        host = 'http://127.0.0.1:64626'
        self.assertFalse(_existing_service_matches(self.server.server_port, self.server.project_id, host))
        self.server.dsh_origin = host
        self.assertTrue(_existing_service_matches(self.server.server_port, self.server.project_id, host))
        self.assertFalse(_existing_service_matches(self.server.server_port, self.server.project_id, 'http://127.0.0.1:64627'))
        self.assertTrue(_existing_service_matches(self.server.server_port, self.server.project_id))
        with self.assertRaisesRegex(OSError, 'DSH origin'):
            serve(self.root, self.server.server_port, dsh_origin='http://127.0.0.1:64627')
        output = io.StringIO()
        with redirect_stdout(output):
            serve(self.root, self.server.server_port, dsh_origin=host)
        self.assertIn('Reusing review desk:', output.getvalue())

    def test_http_encoded_task_feedback_and_image(self):
        path = '/api/tasks/' + quote(self.task_id, safe='')
        self.assertEqual(200, self.request('GET', path)[0])
        self.assertEqual(201, self.request('POST', path + '/feedback', self.feedback())[0])
        self.assertEqual(403, self.request('POST', path + '/feedback', self.feedback(), {'Origin': 'null'})[0])
        asset = self.desk.detail(self.task_id)['output_url']
        status, body, headers = self.request('GET', asset, headers={'X-Review-Token': ''})
        self.assertEqual(200, status)
        self.assertEqual(PNG, body)
        self.assertEqual('image/png', headers['Content-Type'])
        self.assertEqual(403, self.request('GET', '/api/assets?path=..%2Fprivate.png')[0])
        self.assertEqual(404, self.request('GET', '/%2e%2e/review_desk.py')[0])

    def test_http_request_limits_and_json_errors(self):
        path = '/api/tasks/' + quote(self.task_id, safe='') + '/feedback'
        self.assertEqual(413, self.request('POST', path, {}, {'Content-Length': str(MAX_BODY + 1)})[0])
        self.assertEqual(415, self.request('POST', path, {}, {'Content-Type': 'text/plain'})[0])
        self.assertEqual(400, self.request('POST', path, b'{broken')[0])
        payload = self.feedback()
        payload['decision'] = []
        self.assertEqual(400, self.request('POST', path, payload)[0])
        payload = self.feedback()
        (self.output / 'translated.png').write_bytes(PNG + b'changed')
        self.assertEqual(409, self.request('POST', path, payload)[0])
        status, body, _ = self.request('DELETE', '/api/tasks')
        self.assertEqual(501, status)
        self.assertIn('error', json.loads(body))
        status, body, headers = self.request('GET', '/api/tasks/missing')
        self.assertEqual(404, status)
        self.assertEqual('application/json; charset=utf-8', headers['Content-Type'])
        self.assertNotIn(b'Traceback', body)


class DshOriginTests(unittest.TestCase):
    def test_only_exact_loopback_origins_are_accepted(self):
        self.assertIsNone(validate_dsh_origin(None))
        for value in ('http://127.0.0.1:64626', 'http://localhost:8765'):
            self.assertEqual(value, validate_dsh_origin(value))
        for value in ('', 'http://127.0.0.1', 'https://127.0.0.1:1', 'http://0.0.0.0:1',
                      'http://example.com:1', 'http://127.0.0.1:1/', 'http://127.0.0.1:1/path',
                      'http://user@127.0.0.1:1', 'http://127.0.0.1:1?x=1', 'http://127.0.0.1:1#x',
                      'http://127.0.0.1:0', 'http://127.0.0.1:65536', '*', 'http://127.0.0.1:1\n',
                      'http://localhost:01', 'http://[::1]:1', 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_dsh_origin(value)

    def test_invalid_origin_is_rejected_before_binding_or_service_probe(self):
        with patch('image_localizer.review_desk.create_server') as constructor:
            with self.assertRaises(ValueError):
                serve(Path('.'), 8765, dsh_origin='http://evil.example:1')
        constructor.assert_not_called()

    def test_existing_service_origin_match_is_optional_but_exact_when_requested(self):
        with patch('image_localizer.review_desk.HTTPConnection') as connection_class:
            response = connection_class.return_value.getresponse.return_value
            response.status = 200
            response.headers.get_content_type.return_value = 'application/json'
            identity = {'app': APP_ID, 'project_id': 'project', 'host_origin': 'http://127.0.0.1:1'}
            response.read.return_value = json.dumps(identity).encode()
            self.assertTrue(_existing_service_matches(8765, 'project'))
            self.assertTrue(_existing_service_matches(8765, 'project', 'http://127.0.0.1:1'))
            self.assertFalse(_existing_service_matches(8765, 'project', 'http://127.0.0.1:2'))
            del identity['host_origin']
            response.read.return_value = json.dumps(identity).encode()
            self.assertFalse(_existing_service_matches(8765, 'project', 'http://127.0.0.1:1'))

    def test_cli_forwards_explicit_dsh_origin(self):
        from image_localizer.cli import main
        with patch('image_localizer.review_desk.serve') as service:
            self.assertEqual(0, main(['review-desk', '--port', '8766', '--dsh-origin', 'http://127.0.0.1:64626']))
        self.assertEqual('http://127.0.0.1:64626', service.call_args.kwargs['dsh_origin'])


if __name__ == '__main__':
    unittest.main()
