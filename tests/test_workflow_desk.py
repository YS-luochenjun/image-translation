"""Workflow evidence identity, immutable history and local-path boundaries."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import DeskError, ReviewDesk
from image_localizer.workflow_desk import WorkflowDesk

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class WorkflowDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'runs' / 'sample'
        self.run.mkdir(parents=True)
        self.write(self.run / 'manifest.json', {'source': {'path': 'source.png'}, 'regions': [
            {'id': 'title', 'source_text': 'Hello', 'target_text': '你好', 'bbox': [0, 0, 1, 1]}]})
        (self.run / 'source.png').write_bytes(PNG)
        (self.run / 'translated.png').write_bytes(PNG)
        (self.run / 'text-layer.png').write_bytes(PNG)
        self.desk = ReviewDesk(self.root)
        self.flow = WorkflowDesk(self.desk)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def session(self, sid='session-a', events=None, **extra):
        return {'session_id': sid, 'cwd': str(self.root), 'title': sid, 'status': 'idle', 'events': events or [], **extra}

    def event(self, event_id='e1', phase='end', command='python bin/it.py render runs/sample', **extra):
        return {'event_id': event_id, 'phase': phase, 'tool_name': 'functions.ops_bash', 'command': command, 'success': True, **extra}

    def ingest(self, sid='session-a', events=None):
        return self.flow.ingest({'sessions': [self.session(sid, [self.event()] if events is None else events)]})

    def run_record(self, sid='session-a'):
        return self.flow.snapshot([sid])['sessions'][0]['runs'][0]

    @staticmethod
    def node(run, name):
        return next(item for item in run['nodes'] if item['id'] == name)

    def comment_payload(self):
        run = self.run_record()
        artifact = self.node(run, 'result')['artifacts'][0]
        return {'session_id': 'session-a', 'run_id': run['id'], 'node_id': 'result',
                'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256'],
                'note': '请保留字重并修正位置', 'request_id': 'request-one', 'bbox': [0.1, 0.1, 0.5, 0.5]}

    def assert_status(self, status, callback):
        with self.assertRaises(DeskError) as caught:
            callback()
        self.assertEqual(status, caught.exception.status)

    def test_unassigned_history_never_uses_mtime_or_hash_as_session(self):
        snapshot = self.flow.snapshot()
        self.assertEqual([], snapshot['sessions'])
        self.assertEqual(['runs/sample'], [item['root'] for item in snapshot['unassigned']])
        self.ingest(events=[])
        self.assertEqual([], self.run_record_list())
        self.assertEqual(1, len(self.flow.snapshot()['unassigned']))

    def run_record_list(self):
        return self.flow.snapshot()['sessions'][0]['runs']

    def test_exact_cwd_required_and_missing_cwd_rejected(self):
        for cwd in ('', '.', str(self.run), str(self.root.parent)):
            with self.subTest(cwd=cwd):
                self.assert_status(403, lambda: self.flow.ingest({'sessions': [self.session(cwd=cwd)]}))
        self.assertEqual([], self.flow.snapshot()['sessions'])

    def test_raw_tool_history_binds_and_deduplicates(self):
        self.assertEqual(1, self.ingest()['accepted'])
        self.assertEqual(0, self.ingest()['accepted'])
        run = self.run_record()
        self.assertEqual('runs/sample', run['root'])
        self.assertEqual(hashlib.sha256(b'session-a\0runs/sample').hexdigest()[:20], run['id'])
        self.assertEqual('completed', self.node(run, 'basic.render')['execution_status'])
        self.assertEqual('unknown', self.node(run, 'basic.render')['verdict'])
        self.assertEqual('review_pending', self.node(run, 'result')['status'])

    def test_same_root_different_sessions_are_distinct_conflicts(self):
        self.ingest()
        first = self.run_record()['id']
        self.ingest('session-b')
        snapshot = self.flow.snapshot()
        runs = [session['runs'][0] for session in snapshot['sessions']]
        self.assertNotEqual(first, runs[1]['id'])
        self.assertTrue(all(run['binding_conflict'] for run in runs))
        self.assertEqual(1, len(self.flow.snapshot(['session-a'])['sessions']))
        self.assertEqual([], self.flow.snapshot([])['sessions'])

    def test_manual_binding_is_explicit_and_requires_verified_session(self):
        self.assert_status(400, lambda: self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample'}))
        self.assert_status(409, lambda: self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True}))
        self.ingest(events=[])
        self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True})
        self.assertEqual('runs/sample', self.run_record()['root'])

    def test_unrelated_commands_and_chat_do_not_bind(self):
        events = [self.event(command='echo runs/sample'), self.event('e2', tool_name='chat', command='python bin/it.py render runs/sample')]
        result = self.ingest(events=events)
        self.assertEqual(2, result['ignored'])
        self.assertEqual([], self.run_record_list())

    def test_file_event_binds_only_known_project_file(self):
        self.ingest(events=[self.event(command=None, tool_name='functions.write', file_path='runs/sample/manifest.json')])
        self.assertEqual('completed', self.node(self.run_record(), 'semantic.analyze')['execution_status'])
        self.assertTrue(self.node(self.run_record(), 'semantic.translate')['regions'])
        self.assertTrue(self.node(self.run_record(), 'semantic.analyze')['artifacts'][0]['preview'])

    def test_host_sanitized_events_keep_original_session_and_children(self):
        event = {'event_id': 'host-1', 'phase': 'completed', 'tool_name': 'ops_bash',
                 'run_root': 'runs/sample', 'stage': 'basic.render', 'command_kind': 'render',
                 'output_paths': ['runs/sample/translated.png'], 'call_id': 'call-1', 'executor_session_id': 'child'}
        session = self.session('child', [event], origin_session_id='session-a')
        self.flow.ingest({'sessions': [session]})
        self.assertEqual('session-a', self.run_record()['session_id'])
        self.assertEqual('child', next(item for item in self.flow._records() if item['kind'] == 'event')['executor_session_id'])
        event['paths'] = ['../outside.png']
        event['event_id'] = 'outside'
        self.assert_status(403, lambda: self.flow.ingest({'sessions': [self.session(events=[event])]}))

    def test_live_start_failure_unknown_and_historical_observation(self):
        self.ingest(events=[self.event(phase='start', success=None)])
        self.assertEqual('running', self.node(self.run_record(), 'basic.render')['status'])
        self.ingest(events=[self.event(phase='end', success=False)])
        self.assertEqual('failed', self.node(self.run_record(), 'basic.render')['status'])
        self.ingest(events=[self.event('no-outcome', success=None)])
        self.assertEqual('unknown', self.node(self.run_record(), 'basic.render')['status'])
        self.ingest(events=[self.event('historical', phase='observed')])
        self.assertNotEqual('running', self.node(self.run_record(), 'basic.render')['status'])

    def test_basic_route_has_one_fused_render_and_no_fake_clean(self):
        self.ingest()
        run = self.run_record()
        self.assertEqual('basic', run['route'])
        self.assertEqual('skipped', self.node(run, 'background.prepare')['status'])
        self.assertEqual([], self.node(run, 'background.prepare')['artifacts'])
        self.assertTrue(any(item['name'] == 'text-layer.png' for item in self.node(run, 'basic.render')['artifacts']))
        self.assertEqual([], self.node(run, 'lettering.render')['artifacts'])

    def test_native_and_layered_catalog_and_review_required(self):
        self.write(self.run / 'job.json', {'languages': ['ja']})
        self.write(self.run / 'plan-ja.json', {'target_language': 'ja'})
        background = self.run / 'background'
        background.mkdir()
        (background / 'background.png').write_bytes(PNG)
        self.ingest()
        run = self.run_record()
        self.assertEqual('native', run['route'])
        self.assertTrue(self.node(run, 'background.review')['required'])
        self.assertEqual('planned', self.node(run, 'background.review')['status'])
        self.assertEqual('artifact_ready', self.node(run, 'background.prepare')['status'])
        expected = {'font.coverage', 'evidence.benchmark', 'semantic.resolve', 'quality.job', 'job.freeze'}
        self.assertTrue(expected.issubset({node['id'] for node in run['nodes']}))

    def test_corrupt_json_does_not_hide_other_runs_or_create_pass(self):
        (self.run / 'manifest-broken.json').write_text('{', encoding='utf-8')
        self.write(self.run / 'quality.json', {'status': 'pass'})
        self.ingest()
        snapshot = self.flow.snapshot()
        self.assertTrue(snapshot['warnings'])
        self.assertEqual('unknown', self.node(self.run_record(), 'quality.basic')['verdict'])
        self.assertEqual('review_pending', self.node(self.run_record(), 'result')['status'])

    def test_snapshot_copy_keeps_old_bytes_after_live_overwrite(self):
        self.ingest()
        artifacts = self.node(self.run_record(), 'result')['artifacts']
        frozen = next(item for item in artifacts if item['capture'] != 'current')
        old = self.root / frozen['path']
        self.assertEqual(PNG, old.read_bytes())
        (self.run / 'translated.png').write_bytes(PNG + b'new-version')
        self.ingest(events=[self.event('e2')])
        self.assertEqual(PNG, old.read_bytes())
        self.assertEqual(2, len({item['sha256'] for item in self.node(self.run_record(), 'result')['artifacts']}))

    def test_comment_is_append_only_bound_idempotent_and_survives_restart(self):
        self.ingest()
        payload = self.comment_payload()
        result = self.flow.comment(payload)
        self.assertEqual('session-a', result['session_id'])
        self.assertEqual(result, self.flow.comment(payload))
        self.assertIn(result['comment']['id'], result['prompt'])
        restored = WorkflowDesk(self.desk).snapshot()['sessions'][0]['runs'][0]
        self.assertEqual(1, len(restored['comments']))
        self.assertFalse((self.run / 'review.json').exists())
        changed = dict(payload, note='different')
        self.assert_status(409, lambda: self.flow.comment(changed))

    def test_comment_hash_session_node_and_region_conflicts(self):
        self.ingest()
        payload = self.comment_payload()
        for change in ({'artifact_sha256': '0' * 64}, {'session_id': 'other'}, {'node_id': 'semantic.analyze'}, {'region_id': 'missing'}):
            self.assert_status(409, lambda change=change: self.flow.comment(dict(payload, **change)))
        (self.run / 'translated.png').write_bytes(PNG + b'new')
        self.assert_status(409, lambda: self.flow.comment(payload))

    def test_bbox_and_note_validation(self):
        self.ingest()
        payload = self.comment_payload()
        for bbox in ([-0.1, 0, 1, 1], [0, 0, 2, 1], [0, 0, 0, 1], [0, 0, float('nan'), 1], [True, 0, 1, 1], [0, 0, 1]):
            self.assert_status(400, lambda bbox=bbox: self.flow.comment(dict(payload, bbox=bbox)))
        self.assert_status(400, lambda: self.flow.comment(dict(payload, note='x' * 20001)))

    def test_delivery_keeps_original_session_and_never_claims_completion(self):
        self.ingest()
        comment = self.flow.comment(self.comment_payload())['comment']
        payload = {'comment_id': comment['id'], 'request_id': comment['request_id'], 'session_id': 'session-a', 'status': 'queued'}
        self.assertEqual('session-a', self.flow.record_delivery(payload)['delivery']['session_id'])
        self.flow.record_delivery(dict(payload, status='failed', error='disconnected'))
        self.assertEqual(2, len(self.run_record()['deliveries']))
        self.assert_status(409, lambda: self.flow.record_delivery(dict(payload, session_id='other')))
        self.assert_status(400, lambda: self.flow.record_delivery(dict(payload, status='completed')))

    def test_save_result_hash_copy_idempotency_and_history(self):
        self.ingest()
        payload = self.comment_payload()
        save = {key: payload[key] for key in ('session_id', 'run_id', 'artifact_id')}
        save['sha256'] = payload['artifact_sha256']
        first = self.flow.save_result(save)
        self.assertFalse(first['idempotent'])
        saved_path = self.root / first['saved']['path']
        self.assertEqual(PNG, saved_path.read_bytes())
        self.assertTrue(saved_path.is_relative_to(self.root / 'saved-results'))
        self.assertTrue(self.flow.save_result(save)['idempotent'])
        self.assertEqual(saved_path, self.flow.artifact(first['saved']['path']))
        (self.run / 'translated.png').write_bytes(PNG + b'changed')
        self.assertEqual(PNG, saved_path.read_bytes())
        self.assert_status(409, lambda: self.flow.save_result(save))

    def test_save_result_rejects_source_and_changed_saved_file(self):
        self.ingest()
        run = self.run_record()
        source = self.node(run, 'source.normalize')['artifacts'][0]
        self.assert_status(409, lambda: self.flow.save_result({'session_id': 'session-a', 'run_id': run['id'], 'artifact_id': source['id'], 'sha256': source['sha256']}))
        payload = self.comment_payload()
        save = {'session_id': 'session-a', 'run_id': run['id'], 'artifact_id': payload['artifact_id'], 'sha256': payload['artifact_sha256']}
        saved = self.flow.save_result(save)['saved']
        (self.root / saved['path']).write_bytes(b'tampered')
        self.assert_status(409, lambda: self.flow.save_result(save))

    def test_allowlist_rejects_unindexed_outside_and_ledger(self):
        self.ingest()
        self.assertEqual(self.run / 'source.png', self.flow.artifact('runs/sample/source.png'))
        self.assertIsNone(self.flow.artifact('../outside.png'))
        self.assertIsNone(self.flow.artifact('.review-desk/workflows/ledger.jsonl'))
        self.write(self.run / 'unrelated.json', {'private': True})
        self.assertIsNone(self.flow.artifact('runs/sample/unrelated.json'))

    def test_symlink_and_reparse_paths_rejected(self):
        link = self.root / 'runs' / 'linked'
        try:
            link.symlink_to(self.run, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation is unavailable')
        result = self.flow.ingest({'sessions': [self.session(events=[self.event(command='python bin/it.py render runs/linked')])]})
        self.assertEqual(1, result['ignored'])
        self.assertEqual([], self.run_record_list())
        self.assertIsNone(self.flow.artifact('runs/linked/source.png'))

    def test_consuming_history_does_not_claim_production(self):
        for command in ('packet', 'validate', 'check-layered', 'check-job', 'quality'):
            result = self.ingest(events=[self.event(command='python bin/it.py ' + command + ' runs/sample')])
            self.assertEqual(1, result['ignored'])
            self.assertEqual([], self.run_record_list())
        result = self.ingest(events=[self.event(tool_name='read_image', command=None, file_path='runs/sample/source.png')])
        self.assertEqual(1, result['ignored'])
        self.assertEqual(1, len(self.flow.snapshot()['unassigned']))
        self.ingest()
        self.ingest(events=[self.event('read-after-binding', tool_name='read_image', command=None, file_path='runs/sample/source.png')])
        self.assertNotEqual('completed', self.node(self.run_record(), 'source.normalize')['execution_status'])

    def test_copied_images_are_current_observations_not_historical_outputs(self):
        self.ingest(events=[self.event(timestamp='2020-01-01T00:00:00Z')])
        frozen = next(item for item in self.node(self.run_record(), 'result')['artifacts'] if item['capture'] != 'current')
        self.assertEqual('observed_current', frozen['capture'])
        self.assertIn('captured_at', frozen)
        self.assertFalse(frozen['captured_at'].startswith('2020'))
        (self.root / frozen['path']).write_bytes(b'corrupt')
        self.assertIsNone(self.flow.artifact(frozen['path']))
        self.assertTrue(self.flow.snapshot()['warnings'])

    def test_current_result_follows_hash_bound_pointer_not_sorted_versions(self):
        (self.run / 'translated.png').unlink()
        for version in ('a-old', 'z-new'):
            output = self.run / 'renders' / version
            output.mkdir(parents=True)
            (output / 'translated.png').write_bytes(PNG + version.encode())
            self.write(output / 'receipt.json', {'output_sha256': hashlib.sha256((output / 'translated.png').read_bytes()).hexdigest()})
        selected = self.run / 'renders' / 'a-old' / 'receipt.json'
        self.write(self.run / 'current.json', {'version': 'a-old', 'receipt_sha256': hashlib.sha256(selected.read_bytes()).hexdigest()})
        self.ingest()
        self.assertEqual('runs/sample/renders/a-old/translated.png', self.run_record()['result']['path'])
        self.write(self.run / 'current.json', {'version': 'z-new', 'receipt_sha256': '0' * 64})
        self.assertIsNone(self.run_record()['result'])

    def test_region_overlays_require_bound_source_canvas_and_receipt(self):
        sha = hashlib.sha256(PNG).hexdigest()
        manifest = {'source': {'path': 'source.png', 'sha256': sha}, 'canvas': {'width': 1, 'height': 1},
                    'regions': [{'id': 'title', 'bbox': [0, 0, 1, 1]}]}
        self.write(self.run / 'manifest.json', manifest)
        self.write(self.run / 'receipt.json', {'source_sha256': sha,
                   'manifest_sha256': hashlib.sha256((self.run / 'manifest.json').read_bytes()).hexdigest(),
                   'artifact_sha256': {'translated.png': sha, 'text-layer.png': sha}})
        (self.run / 'comparison.png').write_bytes(PNG)
        self.ingest()
        run = self.run_record()
        self.assertEqual([0.0, 0.0, 1.0, 1.0], run['regions'][0]['bbox'])
        self.assertEqual('source_normalized', run['result']['coordinate_space'])
        comparison = next(item for item in self.node(run, 'evidence.compare')['artifacts'] if item['name'] == 'comparison.png')
        self.assertNotIn('coordinate_space', comparison)
        payload = self.comment_payload()
        self.flow.comment(dict(payload, region_id='title'))
        manifest['source']['sha256'] = '0' * 64
        self.write(self.run / 'manifest.json', manifest)
        self.assertEqual([], self.run_record()['regions'])
        self.assertNotIn('coordinate_space', self.run_record()['result'])

    def test_job_source_is_a_validated_input_without_claiming_source_run(self):
        (self.run / 'manifest.json').unlink()
        source = self.run / 'inputs' / 'original.png'
        source.parent.mkdir()
        source.write_bytes(PNG)
        self.write(self.run / 'job.json', {'source': 'inputs/original.png', 'source_sha256': hashlib.sha256(PNG).hexdigest()})
        self.ingest(events=[self.event(command='python bin/it.py render-job runs/sample --output runs/native-output')])
        self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True})
        run = next(run for run in self.flow.snapshot()['sessions'][0]['runs'] if run['root'] == 'runs/sample')
        self.assertEqual('runs/sample/inputs/original.png', self.node(run, 'source.normalize')['artifacts'][0]['path'])

    def test_event_times_preserve_order_when_history_arrives_reversed(self):
        self.ingest(events=[self.event(phase='end', timestamp='2025-01-01T00:00:02Z'),
                            self.event(phase='start', timestamp='2025-01-01T00:00:01Z')])
        self.assertEqual('completed', self.node(self.run_record(), 'basic.render')['execution_status'])

    def test_host_command_substages_are_whitelisted(self):
        events = [{'event_id': 'substage', 'phase': 'completed', 'tool_name': 'ops_bash',
                   'run_root': 'runs/sample', 'stage': 'source.normalize', 'command_kind': 'init',
                   'output_paths': ['runs/sample/source.png']}]
        self.assertEqual(1, self.ingest(events=events)['accepted'])
        events[0].update(event_id='invented', stage='revision.attempt')
        self.assertEqual(1, self.ingest(events=events)['ignored'])

    def test_inventory_skips_safety_walks_for_irrelevant_files(self):
        from image_localizer.project_monitor import EvidenceReader
        for index in range(80):
            (self.run / ('unused-%d.bin' % index)).write_bytes(b'ignored')
        reader = EvidenceReader(self.root)
        with patch.object(reader, 'safe', wraps=reader.safe) as safe:
            files = self.flow._inventory(reader)
        self.assertIn(self.run / 'translated.png', files)
        self.assertLess(safe.call_count, 12, 'Inventory walks ancestors for every irrelevant file')

    def test_snapshot_validates_duplicate_observed_images_once(self):
        self.ingest()
        records = self.flow._records()
        event = next(item for item in records if item.get('kind') == 'event' and item.get('artifacts'))
        duplicate = dict(event, event_id='repeat-observation')
        self.flow._append([duplicate])
        stored = self.root / event['artifacts'][0]['path']
        original = self.flow._artifact
        reads = []
        def observed(path, *args, **kwargs):
            if path == stored:
                reads.append(path)
            return original(path, *args, **kwargs)
        with patch.object(self.flow, '_artifact', side_effect=observed):
            self.flow.snapshot()
        self.assertEqual(1, len(reads), 'One snapshot repeatedly validates the same stored image')

    def test_projection_folds_repeated_observations_but_retains_distinct_sha_history(self):
        self.ingest()
        event = next(item for item in self.flow._records() if item.get('kind') == 'event' and item.get('artifacts'))
        duplicate = json.loads(json.dumps(event))
        duplicate['event_id'] = 'same-bytes-later'
        for artifact in duplicate['artifacts']:
            artifact['captured_at'] = '2026-11-04T08:00:00+00:00'
        self.flow._append([duplicate])
        before = self.flow._ledger_path().read_bytes()
        run = self.flow.snapshot()['sessions'][0]['runs'][0]
        for node in run['nodes']:
            observed = [item for item in node['artifacts'] if item.get('capture') != 'current']
            identities = {(item['source_path'], item['path'], item['sha256']) for item in observed}
            self.assertEqual(len(identities), len(observed), node['id'])
        self.assertEqual(before, self.flow._ledger_path().read_bytes())
        (self.run / 'translated.png').write_bytes(PNG + b'new sha version')
        self.ingest(events=[self.event('new-sha')])
        run = self.flow.snapshot()['sessions'][0]['runs'][0]
        self.assertEqual(2, len({item['sha256'] for item in self.node(run, 'result')['artifacts']}))

    def test_partial_snapshot_reuses_cache_without_replenishing_read_budgets(self):
        self.ingest()
        (self.run / 'unused.bin').write_bytes(b'ignored')
        with patch('image_localizer.workflow_desk.MAX_ENTRIES', 5):
            first = self.flow.snapshot()
            self.assertIn('Workflow scan entry budget reached; partial index', first['warnings'])
            with patch.object(self.flow, '_artifact', side_effect=AssertionError('Stable partial graph rebuilt')):
                second = self.flow.snapshot()
        self.assertEqual(first, second)

    def test_artifact_preview_is_bounded_without_truncating_sha_identity(self):
        from image_localizer.project_monitor import EvidenceReader
        self.write(self.run / 'plan-large.json', {'notes': 'a' * 12000})
        path = self.run / 'plan-large.json'
        artifact = self.flow._artifact(path, EvidenceReader(self.root))
        self.assertLessEqual(len(artifact['preview'].encode('utf-8')), 2000)
        self.assertTrue(artifact['preview_truncated'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])

    def test_ledger_uses_its_own_budget_above_generic_record_read_limit(self):
        padding = {'kind': 'padding', 'value': 'a' * (17 * 1024 * 1024)}
        self.flow._append([padding])
        records = self.flow._records()
        self.assertEqual(1, len(records))
        self.assertEqual(len(padding['value']), len(records[0]['value']))
        with patch('image_localizer.workflow_desk.MAX_LEDGER', 16 * 1024 * 1024):
            self.assert_status(409, self.flow._records)

    def test_unassigned_index_never_reads_artifact_bytes(self):
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Unassigned artifact read')):
            snapshot = self.flow.snapshot()
        self.assertEqual(['runs/sample'], [item['root'] for item in snapshot['unassigned']])

    def test_snapshot_cache_avoids_hashing_and_returns_defensive_copies(self):
        self.ingest()
        first = self.flow.snapshot(['session-a'])
        first['sessions'][0]['runs'][0]['title'] = 'mutated-by-caller'
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Cached artifact rehash')):
            second = self.flow.snapshot(['session-a'])
        self.assertEqual('sample', second['sessions'][0]['runs'][0]['title'])
        nested = self.run / 'renders' / 'new-directory'
        nested.mkdir(parents=True)
        (nested / 'translated.png').write_bytes(PNG + b'new')
        updated = self.flow.snapshot(['session-a'])
        self.assertTrue(any(item['path'].endswith('new-directory/translated.png') for item in self.node(updated['sessions'][0]['runs'][0], 'result')['artifacts']))

    def test_asset_allowlist_reuses_snapshot_but_always_rehashes(self):
        self.ingest()
        self.flow.snapshot(['session-a'])
        with patch.object(self.flow, 'snapshot', side_effect=AssertionError('Asset caused another full snapshot')):
            self.assertEqual(self.run / 'translated.png', self.flow.artifact('runs/sample/translated.png'))
            (self.run / 'translated.png').write_bytes(PNG + b'changed')
            self.assertIsNone(self.flow.artifact('runs/sample/translated.png'))

    def test_comments_and_saves_bypass_cached_metadata_identity(self):
        self.ingest()
        payload = self.comment_payload()
        path = self.run / 'translated.png'
        original = path.stat()
        path.write_bytes(PNG[:-1] + b'X')
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        # A metadata fingerprint may remain unchanged, but is never used for writes.
        self.assertEqual(payload['artifact_sha256'], self.flow.snapshot(['session-a'])['sessions'][0]['runs'][0]['result']['sha256'])
        self.assert_status(409, lambda: self.flow.comment(payload))
        self.assert_status(409, lambda: self.flow.save_result({'session_id': 'session-a', 'run_id': payload['run_id'],
                           'artifact_id': payload['artifact_id'], 'sha256': payload['artifact_sha256']}))

    def test_ingest_preserves_omitted_session_title_and_status(self):
        self.flow.ingest({'sessions': [self.session(title='User project title', status='running')]})
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'events': [self.event()]}]})
        session = self.flow.snapshot(['session-a'])['sessions'][0]
        self.assertEqual('User project title', session['title'])
        self.assertEqual('running', session['status'])
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'status': 'idle', 'events': []}]})
        session = self.flow.snapshot(['session-a'])['sessions'][0]
        self.assertEqual('User project title', session['title'])
        self.assertEqual('idle', session['status'])

    def test_lazy_unassigned_inspection_has_stable_id_and_never_writes_ledger(self):
        metadata = self.flow.snapshot()['unassigned'][0]
        result = self.flow.inspect_unassigned('runs/sample')
        run = result['run']
        self.assertEqual(metadata['id'], run['id'])
        self.assertEqual('__unassigned__', run['session_id'])
        self.assertTrue(run['read_only'])
        self.assertEqual('unassigned', run['binding_status'])
        self.assertTrue(all(node['binding_status'] == 'unassigned' for node in run['nodes']))
        self.assertEqual('translated.png', run['result']['name'])
        self.assertEqual(self.run / 'translated.png', self.flow.artifact(run['result']['path']))
        self.assertFalse((self.root / '.review-desk').exists())
        self.assertEqual([], self.flow.snapshot()['sessions'])
        self.assertEqual(metadata['id'], self.flow.snapshot()['unassigned'][0]['id'])

    def test_lazy_inspection_reads_only_selected_root_and_caches_it(self):
        other = self.root / 'runs' / 'other'
        other.mkdir()
        (other / 'translated.png').write_bytes(PNG)
        original = self.flow._artifact
        def selected_only(path, *args, **kwargs):
            self.assertFalse(path.is_relative_to(other))
            return original(path, *args, **kwargs)
        with patch.object(self.flow, '_artifact', side_effect=selected_only):
            first = self.flow.inspect_unassigned('runs/sample')
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Repeated lazy rehash')):
            second = self.flow.inspect_unassigned('runs/sample')
        self.assertEqual(first, second)
        self.assertEqual(2, len(self.flow.snapshot()['unassigned']))

    def test_unassigned_view_cannot_comment_save_or_create_pseudo_session(self):
        run = self.flow.inspect_unassigned('runs/sample')['run']
        artifact = run['result']
        payload = {'session_id': '__unassigned__', 'run_id': run['id'], 'node_id': 'result',
                   'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256'],
                   'request_id': 'readonly-request', 'note': 'Feedback'}
        self.assert_status(409, lambda: self.flow.comment(payload))
        self.assert_status(409, lambda: self.flow.save_result(dict(payload, sha256=artifact['sha256'])))
        self.assert_status(400, lambda: self.flow.ingest({'sessions': [self.session('__unassigned__')]}))
        self.assert_status(400, lambda: self.flow.bind({'session_id': '__unassigned__', 'run_root': 'runs/sample', 'manual': True}))
        self.assertFalse((self.root / '.review-desk').exists())
        self.ingest(events=[])
        self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True})
        self.assert_status(409, lambda: self.flow.inspect_unassigned('runs/sample'))
        self.assertNotEqual(run['id'], self.run_record()['id'])

    def test_lazy_inspection_rejects_unindexed_or_unsafe_roots(self):
        self.assert_status(403, lambda: self.flow.inspect_unassigned('../outside'))
        (self.run / 'arbitrary').mkdir()
        self.assert_status(404, lambda: self.flow.inspect_unassigned('runs/sample/arbitrary'))
        self.assert_status(404, lambda: self.flow.inspect_unassigned('runs/missing'))

    def test_artifact_handles_survive_snapshot_key_changes_without_rebuilding(self):
        self.ingest()
        run = self.run_record()
        path = run['result']['path']
        self.flow.snapshot([])
        with patch.object(self.flow, 'snapshot', side_effect=AssertionError('Asset rebuilt snapshot')):
            self.assertEqual(self.root / path, self.flow.artifact(path))
        sha, seen_at = self.flow._asset_allowlist[path]
        self.flow._asset_allowlist[path] = (sha, seen_at - 61)
        with patch.object(self.flow, 'snapshot', side_effect=AssertionError('Expired asset rebuilt snapshot')):
            self.assertEqual(self.root / path, self.flow.artifact(path))
            self.assertIsNone(self.flow.artifact('.review-desk/workflows/ledger.jsonl'))

    def test_cold_artifact_lookup_is_exact_bound_or_ledger_only(self):
        self.ingest()
        run = self.run_record()
        frozen = next(item for item in self.node(run, 'result')['artifacts'] if item['capture'] != 'current')
        cold = WorkflowDesk(self.desk)
        with patch.object(cold, 'snapshot', side_effect=AssertionError('Cold asset rebuilt snapshot')):
            self.assertEqual(self.run / 'translated.png', cold.artifact('runs/sample/translated.png'))
            self.assertEqual(self.root / frozen['path'], cold.artifact(frozen['path']))
            self.assertIsNone(cold.artifact('runs/sample/private-key.json'))
        other = self.root / 'runs' / 'unowned'
        other.mkdir()
        (other / 'translated.png').write_bytes(PNG)
        self.assertIsNone(cold.artifact('runs/unowned/translated.png'))
        self.flow.inspect_unassigned('runs/unowned')
        self.flow.snapshot(['session-a'])
        self.assertEqual(other / 'translated.png', self.flow.artifact('runs/unowned/translated.png'))

    def test_asset_handles_are_bounded_and_retain_strong_hash_checks(self):
        self.ingest()
        with patch('image_localizer.workflow_desk.MAX_ASSET_HANDLES', 2):
            self.flow.snapshot(['session-a'])
            self.assertLessEqual(len(self.flow._asset_allowlist), 2)
        payload = self.comment_payload()
        self.assert_status(409, lambda: self.flow.comment(dict(payload, artifact_sha256='0' * 64)))

    def test_session_index_is_ledger_only_and_keeps_exact_run_identity(self):
        self.ingest(events=[self.event(phase='start')])
        self.ingest('session-b', events=[self.event('other-start', phase='start')])
        before = self.flow._ledger_path().read_bytes()
        with patch.object(self.flow, '_inventory', side_effect=AssertionError('Index scanned files')):
            with patch.object(self.flow, '_artifact', side_effect=AssertionError('Index read images')):
                index = self.flow.session_index(['session-a'])
        self.assertEqual(1, index['schema_version'])
        self.assertEqual([], index['unassigned'])
        self.assertEqual(['session-a'], [session['session_id'] for session in index['sessions']])
        session = index['sessions'][0]
        self.assertFalse(session['detail_loaded'])
        run = session['runs'][0]
        loaded_run = self.flow.snapshot(['session-a'])['sessions'][0]['runs'][0]
        self.assertEqual(loaded_run['id'], run['id'])
        self.assertEqual('session-a', run['session_id'])
        self.assertEqual('runs/sample', run['root'])
        self.assertEqual('sample', run['title'])
        self.assertEqual('unknown', run['route'])
        self.assertEqual([], run['nodes'])
        self.assertFalse(run['detail_loaded'])
        self.assertEqual(before, self.flow._ledger_path().read_bytes())

    def test_index_run_ids_match_snapshot_across_metadata_polls_and_restart(self):
        self.ingest(events=[self.event(phase='start')])
        self.ingest('session-b', events=[self.event('other-start', phase='start')])
        selected = ['session-a', 'session-b']
        loaded = self.flow.snapshot(selected)
        def identities(snapshot):
            return {(session['session_id'], run['root']): run['id']
                    for session in snapshot['sessions'] for run in session['runs']}
        expected = identities(loaded)
        self.assertEqual(2, len(set(expected.values())))
        self.assertEqual(expected, identities(self.flow.session_index(selected)))
        activity = {'event_id': 'metadata-poll', 'time': '2026-11-04T08:00:00Z',
                    'type': 'stage', 'text': 'Review current image'}
        self.flow.ingest({'sessions': [self.session(title='Updated title', status='running', activities=[activity])]})
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Metadata poll rebuilt graph')):
            cached = self.flow.snapshot(selected)
            index = self.flow.session_index(selected)
        self.assertEqual(expected, identities(cached))
        self.assertEqual(expected, identities(index))
        self.assertEqual(expected, identities(WorkflowDesk(self.desk).session_index(selected)))
        self.assertTrue(all(session['detail_loaded'] for session in cached['sessions']))
        self.assertTrue(all(not session['detail_loaded'] for session in index['sessions']))
        self.assertTrue(all(run['nodes'] for session in cached['sessions'] for run in session['runs']))
        self.assertTrue(all(not run['nodes'] for session in index['sessions'] for run in session['runs']))

    def test_session_index_and_snapshot_share_latest_activity_projection(self):
        plan = {'event_id': 'index-plan', 'time': '2026-11-04T01:00:00Z', 'type': 'plan', 'text': 'Plan',
                'items': [{'content': 'Build backend', 'status': 'in_progress'}]}
        self.flow.ingest({'sessions': [self.session(title='SHE-20261103-068', status='running', activities=[plan])]})
        snapshot = self.flow.snapshot(['session-a'])['sessions'][0]
        index = self.flow.session_index(['session-a'])['sessions'][0]
        self.assertEqual({key: value for key, value in snapshot.items() if key not in ('runs', 'detail_loaded')},
                         {key: value for key, value in index.items() if key not in ('runs', 'detail_loaded')})
        self.assertTrue(snapshot['detail_loaded'])
        self.assertFalse(index['detail_loaded'])
        self.assertEqual('Build backend', index['active_stage']['text'])
        self.assertEqual([], index['runs'])

    def test_session_index_historical_owners_and_unsafe_roots_do_not_leak(self):
        self.ingest(events=[])
        self.ingest('session-b', events=[])
        self.flow._append([{'kind': 'binding', 'session_id': sid, 'root': root}
                           for sid, root in [('session-a', 'runs/sample'), ('session-b', 'runs/sample'),
                                             ('session-a', '../outside'), ('session-a', '.review-desk/private'),
                                             ('session-a', 'runs/missing-history')]])
        index = WorkflowDesk(self.desk).session_index(['session-a'])
        runs = index['sessions'][0]['runs']
        self.assertEqual(['runs/missing-history', 'runs/sample'], [run['root'] for run in runs])
        self.assertTrue(all(run['session_id'] == 'session-a' for run in runs))
        self.assertTrue(index['warnings'])
        self.assertEqual([], self.flow.session_index(['unrelated'])['sessions'])

    def test_membership_and_index_reads_do_not_wait_for_graph_lock(self):
        self.ingest(events=[])
        class RefuseGraphLock:
            def __enter__(self):
                raise AssertionError('Metadata read waited for graph lock')
            def __exit__(self, *args):
                return False
        self.flow.lock = RefuseGraphLock()
        self.assertEqual({'session_ids': ['session-a']}, self.flow.session_ids())
        self.assertEqual(['session-a'], [item['session_id'] for item in self.flow.session_index(['session-a'])['sessions']])

    def test_session_index_rejects_unbounded_or_invalid_ids(self):
        for ids in (None, {}, 'session-a', ['x'] * 101, [0], [''], ['x' * 201]):
            with self.subTest(ids_type=type(ids).__name__):
                self.assert_status(400, lambda: self.flow.session_index(ids))
        self.assertEqual([], self.flow.session_index([])['sessions'])
        self.assertFalse(self.flow._ledger_path().exists())

    def test_persisted_session_ids_do_not_require_graph_or_current_cwd(self):
        self.assertEqual({'session_ids': []}, self.flow.session_ids())
        self.ingest(events=[])
        self.ingest('session-b', events=[])
        self.assertEqual({'session_ids': ['session-a', 'session-b']}, WorkflowDesk(self.desk).session_ids())
        self.assert_status(403, lambda: self.flow.ingest({'sessions': [self.session(cwd=str(self.root.parent))]}))
        with patch.object(self.flow, 'snapshot', side_effect=AssertionError('Member query built graph')):
            self.assertEqual({'session_ids': ['session-a', 'session-b']}, self.flow.session_ids())
        self.flow.inspect_unassigned('runs/sample')
        self.assertNotIn('__unassigned__', self.flow.session_ids()['session_ids'])

    def test_metadata_cache_reuses_unmodified_artifacts_after_ledger_change(self):
        self.ingest()
        self.flow.snapshot(['session-a'])
        self.flow.ingest({'sessions': [self.session(status='running')]})
        from image_localizer.project_monitor import EvidenceReader
        original = EvidenceReader.read
        def refuse_image_reads(reader, path, *args, **kwargs):
            if Path(path).suffix.lower() == '.png':
                self.fail('State-only update reread unchanged image bytes')
            return original(reader, path, *args, **kwargs)
        with patch.object(EvidenceReader, 'read', refuse_image_reads):
            snapshot = self.flow.snapshot(['session-a'])
        self.assertEqual('running', snapshot['sessions'][0]['status'])
        item = self.flow._artifact_metadata['runs/sample/translated.png']['item']
        self.assertNotIn('regions', item)
        self.assertNotIn('coordinate_space', item)

    def test_metadata_cache_is_bounded_and_refreshes_changed_image(self):
        self.ingest()
        with patch('image_localizer.workflow_desk.MAX_ARTIFACT_CACHE', 2):
            self.flow._artifact_metadata.clear()
            self.flow._artifact_metadata_bytes = 0
            self.flow.snapshot(['session-a'], _fresh=True)
            self.assertLessEqual(len(self.flow._artifact_metadata), 2)
        before = self.run_record()['result']['sha256']
        (self.run / 'translated.png').write_bytes(PNG + b'metadata-change')
        self.flow.ingest({'sessions': [self.session(status='running')]})
        self.assertNotEqual(before, self.run_record()['result']['sha256'])
        with patch('image_localizer.workflow_desk.MAX_ARTIFACT_CACHE_BYTES', 1):
            self.flow._artifact_metadata.clear()
            self.flow._artifact_metadata_bytes = 0
            self.flow.snapshot(['session-a'], _fresh=True)
            self.assertEqual(0, len(self.flow._artifact_metadata))

    def test_ingest_batch_freezes_each_unchanged_image_once(self):
        events = [self.event('batch-%d' % index) for index in range(32)]
        with patch.object(self.flow, '_freeze', wraps=self.flow._freeze) as freeze:
            with patch.object(self.flow, '_artifact', wraps=self.flow._artifact) as artifact:
                result = self.ingest(events=events)
        self.assertEqual(32, result['accepted'])
        self.assertEqual(3, freeze.call_count)
        self.assertEqual(3, artifact.call_count)
        captured = [item for item in self.flow._records() if item.get('kind') == 'event']
        self.assertEqual(32, len(captured))
        self.assertTrue(all(len(item['artifacts']) == 3 for item in captured))

    def test_ingest_batch_does_not_repeat_failed_freezes_for_unchanged_images(self):
        with patch.object(self.flow, '_freeze', side_effect=ValueError('Storage budget exceeded')) as freeze:
            result = self.ingest(events=[self.event('failed-freeze-%d' % index) for index in range(32)])
        self.assertEqual(32, result['accepted'])
        self.assertEqual(3, freeze.call_count)
        self.assertTrue(result['warnings'])
        self.assertTrue(all(not item['artifacts'] for item in self.flow._records() if item.get('kind') == 'event'))

    def test_ingest_batch_observes_changed_sha_and_never_reuses_another_request(self):
        path = self.run / 'translated.png'
        original_freeze = self.flow._freeze
        def change_after_freeze(artifact, reader):
            frozen = original_freeze(artifact, reader)
            if artifact['path'] == 'runs/sample/translated.png' and path.read_bytes() == PNG:
                path.write_bytes(PNG + b'changed during batch')
            return frozen
        with patch.object(self.flow, '_freeze', side_effect=change_after_freeze):
            self.ingest(events=[self.event('before-change'), self.event('after-change')])
        captured = [item for item in self.flow._records() if item.get('kind') == 'event']
        hashes = [next(artifact['sha256'] for artifact in item['artifacts'] if artifact['name'] == 'translated.png') for item in captured]
        self.assertEqual([hashlib.sha256(PNG).hexdigest(), hashlib.sha256(path.read_bytes()).hexdigest()], hashes)
        old = path.stat()
        replacement = path.read_bytes()[:-1] + b'X'
        path.write_bytes(replacement)
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        with patch.object(self.flow, '_freeze', wraps=self.flow._freeze) as freeze:
            self.ingest(events=[self.event('another-request')])
        self.assertEqual(3, freeze.call_count)
        latest = [item for item in self.flow._records() if item.get('kind') == 'event'][-1]
        self.assertEqual(hashlib.sha256(replacement).hexdigest(), next(item['sha256'] for item in latest['artifacts'] if item['name'] == 'translated.png'))

    def test_fresh_snapshot_and_end_observation_ignore_metadata_cache(self):
        self.ingest()
        payload = self.comment_payload()
        path = self.run / 'translated.png'
        old = path.stat()
        path.write_bytes(PNG[:-1] + b'X')
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.flow.ingest({'sessions': [self.session(status='running')]})
        stale = self.run_record()['result']['sha256']
        self.assertEqual(payload['artifact_sha256'], stale)
        self.assertIsNone(self.flow.artifact('runs/sample/translated.png'))
        self.assert_status(409, lambda: self.flow.comment(payload))
        self.assertNotEqual(stale, self.flow.snapshot(['session-a'], _fresh=True)['sessions'][0]['runs'][0]['result']['sha256'])
        self.ingest(events=[self.event('fresh-end')])
        raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        event = next(item for item in self.flow._records() if item.get('event_id') == 'fresh-end' and item['kind'] == 'event')
        captured = next(item for item in event['artifacts'] if item['name'] == 'translated.png')
        self.assertEqual(raw_sha, captured['sha256'])

    def test_route_inapplicable_nodes_are_skipped_without_inventing_quality(self):
        self.ingest(events=[])
        self.flow.bind({'session_id': 'session-a', 'run_root': 'runs/sample', 'manual': True})
        basic = self.run_record()
        self.assertEqual('skipped', self.node(basic, 'job.initialize')['status'])
        self.assertEqual('skipped', self.node(basic, 'quality.layered')['status'])
        self.write(self.run / 'plan.json', {'schema_version': 1})
        layered = self.run_record()
        self.assertEqual('layered', layered['route'])
        for name in ('basic.render', 'job.initialize', 'job.freeze', 'job.render', 'quality.job'):
            self.assertEqual('skipped', self.node(layered, name)['status'], name)
            self.assertFalse(self.node(layered, name)['applicable'])
        self.assertEqual('artifact_ready', self.node(layered, 'composite.render')['status'])
        self.assertEqual('artifact_ready', self.node(layered, 'lettering.render')['status'])
        self.write(self.run / 'job.json', {'schema_version': 1})
        native = self.run_record()
        self.assertEqual('native', native['route'])
        self.assertEqual('skipped', self.node(native, 'basic.render')['status'])
        self.assertEqual('planned', self.node(native, 'background.prepare')['status'])
        self.assertTrue(self.node(native, 'background.review')['required'])
        self.assertEqual('unknown', self.node(native, 'composite.render')['verdict'])
        self.assertEqual(len(native['nodes']), len(self.flow.snapshot()['node_catalog']))

    def test_budget_failures_are_bounded(self):
        with patch('image_localizer.workflow_desk.MAX_EVENTS', 1):
            self.assert_status(413, lambda: self.ingest(events=[self.event(), self.event('e2')]))
        with patch('image_localizer.workflow_desk.MAX_CONTENT', 1):
            result = self.ingest()
            self.assertTrue(result['warnings'])
        with patch('image_localizer.workflow_desk.MAX_LEDGER', 1):
            self.assert_status(409, self.flow.snapshot)
    def test_readable_semantics_separate_recognition_from_translation(self):
        self.ingest()
        run = self.run_record()
        recognition = self.node(run, 'semantic.analyze')['content']
        translation = self.node(run, 'semantic.translate')['content']
        self.assertEqual('recognition', recognition['kind'])
        self.assertEqual('Hello', recognition['rows'][0]['source_text'])
        self.assertNotIn('target_text', recognition['rows'][0])
        self.assertEqual('你好', translation['rows'][0]['target_text'])
        self.assertTrue(self.node(run, 'semantic.translate')['has_content'])
        self.assertFalse(self.node(run, 'font.coverage')['has_content'])
        self.write(self.run / 'manifest.json', {'regions': [{'id': 'x', 'source_text': 'Unreadable'}, {'id': 'y', 'target_text': 'invented'}]})
        rows = self.node(self.run_record(), 'semantic.translate')['content']['rows']
        self.assertEqual(1, len(rows))
        self.assertNotIn('target_text', rows[0])

    def test_real_comparison_exports_are_separate_from_composite(self):
        directory = self.run / 'compare-export'
        directory.mkdir()
        for name in ('board.png', 'comparison.png', '07-final-comparison.png'):
            (directory / name).write_bytes(PNG)
        self.write(self.run / 'receipt.json', {'source_sha256': hashlib.sha256(PNG).hexdigest(), 'output_sha256': hashlib.sha256(PNG).hexdigest()})
        self.ingest()
        run = self.run_record()
        node = self.node(run, 'evidence.compare')
        self.assertEqual('comparison', node['stage'])
        self.assertEqual(3, len([item for item in node['artifacts'] if item.get('capture') == 'current']))
        self.assertTrue(all('comparison' not in item['name'] and item['name'] != 'board.png' for item in self.node(run, 'basic.render')['artifacts']))
        pair = node['comparison_inputs'][0]
        self.assertFalse(pair['generated'])
        self.assertEqual(run['result']['id'], pair['result_artifact_id'])

    def test_nested_work_pointer_selects_exact_result_without_rebinding_root(self):
        root = self.root / 'work' / 'demo'
        current = root / 'run'
        for version in ('old', 'new'):
            output = current / 'renders' / version
            output.mkdir(parents=True)
            (output / 'translated.png').write_bytes(PNG)
            self.write(output / 'receipt.json', {'output_sha256': hashlib.sha256(PNG).hexdigest()})
        receipt = current / 'renders' / 'new' / 'receipt.json'
        self.write(current / 'current.json', {'version': 'new', 'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()})
        self.flow.ingest({'sessions': [self.session(events=[self.event(command='python bin/it.py render work/demo')])]})
        run = self.run_record()
        self.assertEqual('work/demo', run['root'])
        self.assertEqual(1, len(run['results']))
        self.assertEqual('work/demo/run/renders/new/translated.png', run['result']['path'])
        self.assertEqual([run['result']['id']], self.node(run, 'result')['current_artifact_ids'])
        self.write(current / 'current.json', {'version': 'old', 'receipt_sha256': '0' * 64})
        run = self.run_record()
        self.assertEqual([], run['results'])
        self.assertEqual([], self.node(run, 'result')['current_artifact_ids'])

    def test_verified_receipt_semantics_read_input_without_claiming_its_root(self):
        semantics = self.root / 'runs' / 'prepared' / 'semantics' / 'en.json'
        sha = hashlib.sha256(PNG).hexdigest()
        self.write(semantics, {'source': {'sha256': sha}, 'target_language': 'en', 'regions': [{'id': 'heading', 'source_text': '原文', 'target_text': 'Translation'}]})
        self.write(self.run / 'receipt.json', {'input_sha256': {str(semantics): hashlib.sha256(semantics.read_bytes()).hexdigest()}})
        self.ingest()
        run = self.run_record()
        rows = self.node(run, 'semantic.translate')['content']['rows']
        self.assertTrue(any(row.get('language') == 'en' and row['target_text'] == 'Translation' for row in rows))
        self.assertNotIn('runs/prepared', [item['root'] for item in self.run_record_list()])
        self.write(semantics, {'source': {'sha256': '0' * 64}, 'regions': [{'id': 'bad', 'source_text': 'Wrong source', 'target_text': 'Do not show'}]})
        self.write(self.run / 'receipt.json', {'input_sha256': {str(semantics): hashlib.sha256(semantics.read_bytes()).hexdigest()}})
        self.assertFalse(any(row.get('target_text') == 'Do not show' for row in self.node(self.run_record(), 'semantic.translate')['content']['rows']))

    def test_review_content_uses_known_notes_not_json_dump(self):
        self.write(self.run / 'final-check.json', {'status': 'pending', 'reasons': ['Check lettering'], 'issues': [{'severity': 'minor', 'note': 'Spacing too narrow'}], 'secret_arbitrary_field': 'Do not expose'})
        self.ingest()
        node = self.node(self.run_record(), 'review.visual')
        self.assertEqual('review', node['content']['kind'])
        self.assertEqual({'Spacing too narrow', 'Check lettering'}, {row['text'] for row in node['content']['rows']})
        self.assertNotIn('secret_arbitrary_field', str(node['content']))
        self.assertEqual('unknown', node['verdict'])

    def test_highlights_are_bound_deduplicated_bounded_and_not_execution(self):
        self.ingest(events=[self.event(phase='observed')])
        highlight = {'event_id': 'assistant-1', 'executor_session_id': 'child-a', 'run_root': 'runs/sample', 'stage': 'semantic.translate', 'text': 'Verified conversation output', 'time': '2026-11-02T10:00:00Z'}
        payload = {'sessions': [self.session(highlights=[highlight])]}
        self.assertEqual(1, self.flow.ingest(payload)['accepted'])
        self.assertEqual(0, self.flow.ingest(payload)['accepted'])
        node = self.node(self.run_record(), 'semantic.translate')
        self.assertEqual('Verified conversation output', node['highlights'][0]['text'])
        self.assertEqual('child-a', node['highlights'][0]['executor_session_id'])
        self.assertEqual('unknown', node['execution_status'])
        self.assert_status(400, lambda: self.flow.ingest({'sessions': [self.session(highlights=[dict(highlight, event_id='oversized', text='x' * 4001)])]}))
        self.assert_status(400, lambda: self.flow.ingest({'sessions': [self.session(highlights=[dict(highlight, stage=[])])]}))
        other = self.root / 'runs' / 'other'
        other.mkdir()
        self.assert_status(409, lambda: self.flow.ingest({'sessions': [self.session(highlights=[dict(highlight, run_root='runs/other')])]}))
        self.assertFalse(any(item['root'] == 'runs/other' for item in self.run_record_list()))

    def test_cross_root_compare_needs_same_owner_and_all_verified_inputs(self):
        other = self.root / 'runs' / 'comparison-export'
        other.mkdir()
        (other / 'board.png').write_bytes(PNG)
        sha = hashlib.sha256(PNG).hexdigest()
        report = {'binding': {'source': {'path': str(self.run / 'source.png'), 'sha256': sha}, 'candidates': [{'path': str(self.run / 'translated.png'), 'sha256': sha}]}, 'board': 'board.png'}
        self.write(other / 'report.json', report)
        self.ingest()
        self.assertFalse(any(item.get('linked_from_root') for item in self.node(self.run_record(), 'evidence.compare')['artifacts']))
        self.flow.ingest({'sessions': [self.session(events=[self.event('compare-own', command='python bin/it.py compare runs/sample --output runs/comparison-export')])]})
        run = next(item for item in self.run_record_list() if item['root'] == 'runs/sample')
        linked = [item for item in self.node(run, 'evidence.compare')['artifacts'] if item.get('linked_from_root')]
        self.assertEqual('runs/comparison-export', linked[0]['linked_from_root'])
        self.assertEqual('verified_compare_source_and_candidates', linked[0]['input_binding'])
        report['binding']['candidates'].append({'path': str(self.run / 'translated.png'), 'sha256': '0' * 64})
        self.write(other / 'report.json', report)
        run = next(item for item in self.run_record_list() if item['root'] == 'runs/sample')
        self.assertFalse(any(item.get('linked_from_root') for item in self.node(run, 'evidence.compare')['artifacts']))

    def test_existing_board_is_not_claimed_current_after_output_changes(self):
        (self.run / 'board.png').write_bytes(PNG)
        sha = hashlib.sha256(PNG).hexdigest()
        self.write(self.run / 'report.json', {'board': 'board.png', 'binding': {'source': {'path': str(self.run / 'source.png'), 'sha256': sha}, 'candidates': [{'path': str(self.run / 'translated.png'), 'sha256': sha}]}})
        self.ingest()
        image = next(item for item in self.node(self.run_record(), 'evidence.compare')['artifacts'] if item['name'] == 'board.png')
        self.assertTrue(image['comparison_current'])
        (self.run / 'translated.png').write_bytes(PNG + b'new version')
        image = next(item for item in self.node(self.run_record(), 'evidence.compare')['artifacts'] if item['name'] == 'board.png')
        self.assertFalse(image['comparison_current'])
        self.assertEqual('historical_or_changed_inputs', image['comparison_status'])

    def test_no_comparison_pair_is_guessed_from_names_only(self):
        self.ingest()
        self.assertNotIn('comparison_inputs', self.node(self.run_record(), 'evidence.compare'))

    def test_reencoded_export_uses_verified_original_input_for_semantics_and_pair(self):
        original = self.root / 'runs' / 'prepared' / 'inputs' / 'original.png'
        original.parent.mkdir(parents=True)
        original.write_bytes(PNG)
        (self.run / 'source.png').write_bytes(PNG + b'reencoded source')
        semantic = original.parent.parent / 'semantics' / 'en.json'
        sha = hashlib.sha256(PNG).hexdigest()
        self.write(semantic, {'source': {'sha256': sha}, 'regions': [{'id': 'verified', 'source_text': 'Original text', 'target_text': 'Bound translation'}]})
        self.write(self.run / 'receipt.json', {'artifact_sha256': {'translated.png': sha}, 'input_sha256': {str(original): sha, str(semantic): hashlib.sha256(semantic.read_bytes()).hexdigest()}})
        self.ingest()
        run = self.run_record()
        source = next(item for item in self.node(run, 'source.normalize')['artifacts'] if item.get('role') == 'verified_source')
        self.assertEqual(sha, source['sha256'])
        self.assertTrue(any(row.get('target_text') == 'Bound translation' for row in self.node(run, 'semantic.translate')['content']['rows']))
        self.assertEqual(source['id'], self.node(run, 'evidence.compare')['comparison_inputs'][0]['source_artifact_id'])

    def test_malformed_optional_comparison_fields_do_not_break_projection(self):
        (self.run / 'board.png').write_bytes(PNG)
        self.write(self.run / 'report.json', {'board': {}, 'binding': {'source': {'sha256': [], 'path': {}}, 'candidates': [{'path': {}, 'sha256': []}]}})
        self.ingest()
        image = next(item for item in self.node(self.run_record(), 'evidence.compare')['artifacts'] if item['name'] == 'board.png')
        self.assertEqual('existing_export', image['comparison_status'])
        self.assertNotIn('comparison_current', image)

    def test_unassigned_content_is_readonly_and_does_not_write_ledger(self):
        before = list(self.root.rglob('ledger.jsonl'))
        result = self.flow.inspect_unassigned('runs/sample')['run']
        self.assertTrue(result['read_only'])
        self.assertEqual('Hello', self.node(result, 'semantic.analyze')['content']['rows'][0]['source_text'])
        self.assertEqual(before, list(self.root.rglob('ledger.jsonl')))


    def test_rootless_session_activity_and_plan_are_independent_from_workflow_ownership(self):
        activity = {'event_id': 'stage-one', 'seq': 1, 'time': '2026-11-03T01:00:00Z', 'type': 'stage', 'stage': '实现控制', 'text': '实现控制', 'status': 'observed'}
        plan = {'event_id': 'plan-one', 'seq': 2, 'time': '2026-11-03T01:01:00Z', 'type': 'plan', 'text': '会话任务计划', 'items': [{'content': '编写后端', 'status': 'in_progress'}, {'content': '验收', 'status': 'pending'}]}
        self.flow.ingest({'sessions': [{'session_id': 'rootless', 'cwd': str(self.root), 'status': 'running', 'activities': [activity, plan]}]})
        session = self.flow.snapshot(['rootless'])['sessions'][0]
        self.assertEqual([], session['runs'])
        self.assertEqual(2, len(session['activities']))
        self.assertEqual('plan-one', session['plan']['event_id'])
        self.assertEqual('编写后端', session['active_stage']['text'])
        self.assertEqual('running', session['active_stage']['status'])
        self.assertFalse(any(row['kind'] == 'binding' for row in self.flow._records()))
        self.flow.ingest({'sessions': [{'session_id': 'rootless', 'cwd': str(self.root), 'status': 'idle'}]})
        session = self.flow.snapshot(['rootless'])['sessions'][0]
        self.assertEqual('idle', session['active_stage']['status'])
        self.assertEqual('in_progress', session['plan']['items'][0]['status'])

    def test_conversation_plan_latest_identity_survives_old_replay_and_metadata_updates(self):
        def plan(event_id, minute):
            return {'event_id': event_id, 'time': '2026-11-03T01:%02d:00Z' % minute, 'type': 'plan', 'text': '任务计划', 'items': [{'content': event_id, 'status': 'pending'}]}
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'activities': [plan('new-plan', 2)]}]})
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'title': '新标题', 'activities': [plan('old-plan', 1)]}]})
        session = self.flow.snapshot(['session-a'])['sessions'][0]
        self.assertEqual('new-plan', session['plan']['event_id'])
        self.assertEqual('new-plan', session['plan']['items'][0]['content'])
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'activities': [plan('new-plan', 2)]}]})
        self.assertEqual(2, len(self.flow.snapshot(['session-a'])['sessions'][0]['activities']))

    def test_conversation_activity_is_bounded_and_rejects_foreign_identity_or_unsafe_text(self):
        def item(index):
            return {'event_id': 'a%d' % index, 'time': '2026-11-03T01:00:%02dZ' % index, 'seq': index, 'type': 'stage', 'text': '当前步骤%d' % index}
        for begin in (0, 20):
            self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'activities': [item(index) for index in range(begin, begin + 20)]}]})
        recent = self.flow.snapshot(['session-a'])['sessions'][0]['activities']
        self.assertEqual(20, len(recent))
        self.assertEqual('a20', recent[0]['event_id'])
        for raw in ({**item(40), 'executor_session_id': 'another'}, {**item(41), 'text': 'api_key: secret-value'}, {**item(42), 'text': 'x' * 601}):
            self.assert_status(409 if raw.get('executor_session_id') else 400, lambda: self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'activities': [raw]}]}))
        self.assertEqual([], self.flow.snapshot(['other'])['sessions'])


    def test_session_activity_updates_reuse_image_graph_but_publish_fresh_session_data(self):
        self.ingest()
        before = self.flow.snapshot()
        activity = {'event_id': 'fast-stage', 'time': '2026-11-03T02:00:00Z', 'type': 'stage', 'text': '实时控制步骤'}
        self.flow.ingest({'sessions': [{'session_id': 'session-a', 'cwd': str(self.root), 'title': '新标题', 'status': 'running', 'activities': [activity]}, {'session_id': 'rootless', 'cwd': str(self.root), 'status': 'running', 'activities': [{**activity, 'event_id': 'rootless-stage'}]}]})
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Unchanged image graph rebuilt')):
            after = self.flow.snapshot()
        session = next(item for item in after['sessions'] if item['session_id'] == 'session-a')
        self.assertEqual('新标题', session['title'])
        self.assertEqual('实时控制步骤', session['active_stage']['text'])
        self.assertEqual(before['sessions'][0]['runs'], session['runs'])
        self.assertEqual([], next(item for item in after['sessions'] if item['session_id'] == 'rootless')['runs'])

    def test_graph_fingerprint_uses_ledger_facts_before_display_annotations(self):
        self.ingest()
        original = self.flow._link_compares
        annotated = []
        def annotate(output, grouped, bindings, reader):
            original(output, grouped, bindings, reader)
            for session in output:
                for run in session['runs']:
                    for node in run['nodes']:
                        for artifact in node['artifacts']:
                            if artifact.get('source_path'):
                                artifact['display_annotation'] = True
                                annotated.append(artifact['id'])
        with patch.object(self.flow, '_link_compares', side_effect=annotate):
            self.flow.snapshot()
        self.assertTrue(annotated)
        with patch.object(self.flow, '_artifact', side_effect=AssertionError('Display annotations changed immutable ledger fingerprint')):
            self.flow.snapshot()

    def test_metadata_cache_separation_still_invalidates_external_graph_facts_and_file_changes(self):
        self.ingest()
        self.flow.snapshot()
        other = ReviewDesk(self.root).workflow
        other._append([{'kind': 'comment', 'comment': {'id': 'external-comment', 'run_id': self.run_record()['id'], 'note': '外部新点评'}}])
        with patch.object(self.flow, '_artifact', wraps=self.flow._artifact) as artifact:
            result = self.flow.snapshot()
            self.assertGreater(artifact.call_count, 0)
        self.assertEqual('external-comment', result['sessions'][0]['runs'][0]['comments'][-1]['id'])
        (self.run / 'new-comparison.png').write_bytes(PNG)
        with patch.object(self.flow, '_artifact', wraps=self.flow._artifact) as artifact:
            self.flow.snapshot()
            self.assertGreater(artifact.call_count, 0)
        with patch.object(self.flow, '_artifact', wraps=self.flow._artifact) as artifact:
            self.flow.snapshot(_fresh=True)
            self.assertGreater(artifact.call_count, 0)


if __name__ == '__main__':
    unittest.main()
