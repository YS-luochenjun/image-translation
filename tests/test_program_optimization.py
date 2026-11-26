"""Offline tests for the optimization CLI and the Fireplay status lifecycle."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from image_localizer.delivery_autopilot import compact_delivery
from image_localizer.fireplay import accept
from image_localizer.fireplay_status_transition import status_transition
from image_localizer.jobs import initialize_job
from image_localizer.pipeline import normalize_source, packet, read_json, write_json
from image_localizer.stage_events import record_stage, summarize_stages


class ProgramOptimizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code = 'TEST-20261123-001'
        self.requirement_id = str(uuid4())
        self.now = datetime(2026, 11, 23, 18, tzinfo=timezone.utc)
        self.updated = (self.now - timedelta(minutes=3)).isoformat()
        self.folder = self.root / 'state/fireplay' / self.code
        (self.folder / 'snapshots').mkdir(parents=True)
        self.request = {'requirement_id': self.requirement_id, 'updatedAt': self.updated,
                        'content_version': 'test', 'state': 'accepted'}
        write_json(self.folder / 'request.json', self.request)

    def snapshot(self, status='not_started', *, tick=0, version=None, permission=True, materials=None):
        now = self.now + timedelta(seconds=tick)
        row = {'code': self.code, 'id': self.requirement_id, 'aiMode': 'fixture_image_translation',
               'status': status, 'assetType': 'image', 'updatedAt': version or self.updated,
               'canUpdateStatus': permission, 'materials': materials or []}
        snapshot = {view: {'scope': 'all', 'view': view, 'serverTime': now.isoformat(), 'items': [dict(row)]}
                    for view in ('summary', 'blind_brief', 'delivery')}
        snapshot['options'] = {'fixed': {'aiModes': [{'label': '图片翻译', 'enabled': True,
                                                      'value': row['aiMode']}]},
                               'configured': {'status': ['not_started', 'in_progress', 'done']}}
        return snapshot, now

    def transition(self, action, snapshot, **options):
        receipt, now = snapshot
        return status_transition(self.root, self.code, action, receipt, now=now, **options)

    def test_start_timeout_never_reissues_action_and_fresh_readback_confirms(self):
        original = self.snapshot()
        self.assertEqual('eligible', self.transition('start', original)['state'])
        intent = self.transition('start', original, prepare=True)
        self.assertEqual(self.updated, intent['host_action']['arguments']['items'][0]['base_updated_at'])
        self.assertNotIn('host_action', self.transition('start', original, prepare=True))
        self.assertEqual('reconcile_required', self.transition('start', self.snapshot(tick=1), reconcile=True)['state'])
        version = (self.now + timedelta(seconds=2)).isoformat()
        readback = self.snapshot('in_progress', tick=3, version=version)
        self.assertEqual('confirmed', self.transition('start', readback, reconcile=True)['state'])
        self.assertNotIn('host_action', self.transition('start', readback, prepare=True))

    def test_start_accepts_complete_exact_batch_when_blind_brief_omits_status(self):
        receipt, now = self.snapshot()
        other_id = str(uuid4())
        for view in ('summary', 'blind_brief'):
            response = receipt[view]
            row = response['items'][0]
            if view == 'blind_brief':
                del row['status']
            response['items'].append({**row, 'id': other_id, 'code': 'TEST-20261123-002'})
            response['total'] = 2
            response['query_meta'] = {'filters_applied': {'requirement_ids': [self.requirement_id, other_id]}}
        self.assertEqual('eligible', self.transition('start', (receipt, now))['state'])
        receipt['summary']['next_cursor'] = 'remaining'
        with self.assertRaisesRegex(ValueError, 'complete exact-UUID batch'):
            self.transition('start', (receipt, now))

    def test_status_permissions_and_existing_owner_are_checked(self):
        self.assertEqual('needs_status_permission', self.transition('start', self.snapshot(permission=False), prepare=True)['state'])
        self.assertEqual('manual_review', self.transition('start', self.snapshot('in_progress'), prepare=True)['state'])
        assignments = self.root / 'work/fireplay-monitor/assignments.json'
        assignments.parent.mkdir(parents=True, exist_ok=True)
        write_json(assignments, {'requirements': {self.requirement_id: {'responsible_thread_id': 'owner'}}})
        with self.assertRaisesRegex(ValueError, 'existing or unresolved owner'):
            self.transition('start', self.snapshot(), prepare=True)

    def test_twentieth_reservation_blocks_next_start_until_a_slot_is_released(self):
        assignments = self.root / 'work/fireplay-monitor/assignments.json'
        assignments.parent.mkdir(parents=True)
        active = {str(uuid4()): {'state': 'dispatched'} for _ in range(19)}
        write_json(assignments, {'requirements': active})
        record = {**self.request, 'state': 'ready', 'blockers': [], 'translation_intent': True,
                  'sources': [{'id': 'source'}], 'targets': ['ja'], 'accepted_versions': []}
        write_json(self.folder / 'request.json', record)
        self.assertEqual('accepted', accept(self.code, self.root)['state'])
        self.assertEqual('accepted', accept(self.code, self.root)['state'])
        queued_code = 'TEST-20261123-002'
        queued_id = str(uuid4())
        queued_folder = self.folder.parent / queued_code
        (queued_folder / 'snapshots').mkdir(parents=True)
        queued = {**record, 'requirement_id': queued_id}
        write_json(queued_folder / 'request.json', queued)
        denied = accept(queued_code, self.root)
        self.assertEqual('queued_capacity', denied['state'])
        self.assertEqual({'limit': 20, 'active': 20, 'available': 0}, denied['capacity'])
        self.assertEqual('ready', read_json(queued_folder / 'request.json')['state'])
        write_json(queued_folder / 'request.json', {**queued, 'state': 'accepted'})
        snapshot, now = self.snapshot()
        for view in ('summary', 'blind_brief', 'delivery'):
            snapshot[view]['items'][0].update(code=queued_code, id=queued_id)
        result = status_transition(self.root, queued_code, 'start', snapshot, prepare=True, now=now)
        self.assertEqual('queued_capacity', result['state'])
        self.assertNotIn('host_action', result)
        active[next(iter(active))]['state'] = 'verified_complete'
        write_json(assignments, {'requirements': active})
        self.assertEqual('accepted', accept(queued_code, self.root)['state'])

    def test_finish_requires_complete_matrix_and_current_platform_bindings(self):
        folder = self.root / 'work/fireplay-production' / self.code
        folder.mkdir(parents=True)
        delivery_state = folder / 'delivery-state.json'
        write_json(delivery_state, {'content_version': self.request['content_version']})
        write_json(self.folder / 'status-start.json', {'state': 'confirmed', 'requirement_id': self.requirement_id})
        snapshot = self.snapshot('in_progress', materials=[{'id': 'verified-material'}])
        report = {'requirement_id': self.requirement_id, 'code': self.code, 'complete': False,
                  'verified_material_ids': ['verified-material'], 'verified_members': 7, 'publication_unknown': [],
                  'evidence_counts': {'official_receipts': 7}}
        with patch('image_localizer.fireplay_status_transition.next_delivery', return_value=report):
            with self.assertRaisesRegex(ValueError, 'All members'):
                self.transition('finish', snapshot, prepare=True, delivery_state=delivery_state)
            report['complete'] = True
            with self.assertRaisesRegex(ValueError, 'does not include every'):
                self.transition('finish', self.snapshot('in_progress'), prepare=True, delivery_state=delivery_state)
            intent = self.transition('finish', snapshot, prepare=True, delivery_state=delivery_state)
        self.assertEqual('done', intent['host_action']['arguments']['items'][0]['status'])
        later = (self.now + timedelta(seconds=1)).isoformat()
        missing, now = self.snapshot('done', tick=2, version=later)
        with self.assertRaisesRegex(ValueError, 'does not include every bound material'):
            self.transition('finish', ({'delivery': missing['delivery']}, now), reconcile=True)
        readback, now = self.snapshot('done', tick=3, version=later, materials=[{'id': 'verified-material'}])
        self.assertEqual('confirmed', self.transition('finish', ({'delivery': readback['delivery']}, now),
                                                      reconcile=True)['state'])
        admissions = read_json(self.root / 'work/fireplay-monitor/admissions.json')
        self.assertEqual('released', admissions['requirements'][self.requirement_id]['state'])

    def test_packet_checkpoint_changes_only_current_sections_and_keeps_legacy_export(self):
        source = self.root / 'source.png'
        Image.new('RGB', (80, 50), 'white').save(source)
        job = self.root / 'job'
        with patch('image_localizer.jobs.normalize_source', wraps=normalize_source) as normalize:
            initialize_job(source, ['ja', 'fr'], job)
        self.assertEqual(1, normalize.call_count)
        run = job / 'languages/ja'
        start = packet(run, compact=True)
        complete = packet(run)
        same = packet(run, compact=True, since=start['checkpoint'])
        self.assertNotIn('prompts', same)
        self.assertEqual('unchanged', same['mode'])
        self.assertEqual(24, len(same['checkpoint']))
        self.assertLess(len(json.dumps(same, ensure_ascii=False)), len(json.dumps(complete, ensure_ascii=False)) / 10)
        manifest = read_json(run / 'manifest.json')
        manifest['analysis']['observations'] = ['New observation.']
        write_json(run / 'manifest.json', manifest)
        change = packet(run, compact=True, since=same['checkpoint'])
        self.assertEqual(['New observation.'], change['manifest']['analysis']['observations'])
        self.assertNotIn('knowledge', change)
        (run / 'source.png').write_bytes(b'changed')
        with Image.open(job / 'source.png') as original:
            self.assertEqual(1, original.n_frames)
        with self.assertRaises(ValueError):
            packet(run, compact=True, since=change['checkpoint'])

    def test_packet_sends_only_changed_or_removed_regions(self):
        source = self.root / 'source.png'
        Image.new('RGB', (80, 50), 'white').save(source)
        run = self.root / 'job/languages/ja'
        initialize_job(source, ['ja'], self.root / 'job')
        manifest = read_json(run / 'manifest.json')
        manifest['regions'] = [{'id': identity, 'bbox': box, 'action': 'preserve',
                                'source_text': 'ID', 'confidence': 1, 'notes': 'Keep'}
                               for identity, box in [('first', [0, 0, 10, 10]), ('second', [20, 0, 10, 10])]]
        write_json(run / 'manifest.json', manifest)
        first = packet(run, compact=True)
        manifest['regions'][1]['notes'] = 'Updated reason'
        write_json(run / 'manifest.json', manifest)
        second = packet(run, compact=True, since=first['checkpoint'])
        self.assertEqual('delta', second['mode'])
        self.assertEqual([manifest['regions'][1]], second['region_changes']['upsert'])
        self.assertNotIn('manifest', second)
        manifest['regions'].pop(0)
        write_json(run / 'manifest.json', manifest)
        third = packet(run, compact=True, since=second['checkpoint'])
        self.assertEqual(['first'], third['region_changes']['remove'])
        self.assertEqual(['second'], third['region_order'])
        self.assertEqual('snapshot', packet(run, compact=True, since='old-checkpoint')['mode'])

    def test_delivery_delta_covers_members_beyond_initial_preview(self):
        spec = self.root / 'delivery-state.json'
        members = [{'source_id': f'source{index}', 'language': 'ja', 'member_id': 'wide',
                    'action': 'review', 'reason': 'Needs review'} for index in range(14)]
        report = {'code': self.code, 'requirement_id': self.requirement_id, 'complete': False,
                  'verified_members': 0, 'expected_members': 14, 'publication_unknown': [], 'actions': members}
        first = compact_delivery(report, spec, None)
        self.assertEqual(8, len(first['next_actions']))
        members[-1]['reason'] = 'Review changed'
        delta = compact_delivery(report, spec, first['checkpoint'])
        self.assertEqual([members[-1]], delta['member_changes']['upsert'])
        self.assertNotIn('next_actions', delta)
        same = compact_delivery(report, spec, delta['checkpoint'])
        self.assertEqual('unchanged', same['mode'])

    def test_stage_summary_keeps_parallel_time_and_actual_costs(self):
        path = self.root / 'stage-events.jsonl'
        base = {'started_at': self.now.isoformat(),
                'finished_at': (self.now + timedelta(seconds=10)).isoformat(),
                'outcome': 'completed'}
        record_stage(path, {**base, 'stage': 'visual_review'})
        record_stage(path, {**base, 'stage': 'provider_wait', 'evidence': 'receipt.json',
                            'cost': {'amount': '1.25', 'unit': 'credits'}})
        report = summarize_stages(path)
        self.assertEqual({'credits': '1.25'}, report['costs'])
        self.assertEqual(10, report['wall_span_seconds'])
        self.assertEqual(20, sum(stage['seconds'] for stage in report['stages'].values()))
