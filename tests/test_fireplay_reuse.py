from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from image_localizer import fireplay
from image_localizer.artifact_copy import stage_artifact
from image_localizer.fireplay_download import _inspect
from image_localizer.fireplay_reuse import _requirement_candidates, reuse_plan, search_history, stage_reuse
from image_localizer.storage_retention import archive_path, maintain_storage, manage_quarantine, retention_hold


class HistoricalReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code = 'TEST-20261126-001'
        self.original = self.register_source(self.code, 'red')

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def register_source(self, code, color):
        original = self.root / 'state/fireplay' / code / 'sources/original.png'
        original.parent.mkdir(parents=True)
        Image.new('RGB', (300, 250), color).save(original)
        identifier = str(uuid4())
        source = {'index': 1, 'id': 'original', 'url': 'https://example.invalid/original.png'}
        snapshot = {'content': {'assetType': 'button', 'code': code, 'id': identifier},
                    'parsed': {'sources': [source]}}
        version = fireplay._hash(snapshot)
        self.write(original.parent.parent / 'snapshots' / f'{version}.json', snapshot)
        self.write(original.parent.parent / 'request.json', {
            'code': code, 'requirement_id': identifier, 'content_version': version, 'sources': [source],
            'targets': ['ja'], 'downloads': {'1': {
                **_inspect(original), 'local_path': str(original), 'content_version': version,
            }},
        })
        return original

    def test_local_requires_same_source_and_confirmed_historical_material(self):
        previous = 'TEST-20261125-002'
        historical_original = self.register_source(previous, 'red')
        row = json.loads(historical_original.parent.parent.joinpath('request.json').read_bytes())
        folder = self.root / 'work/fireplay-production' / previous
        self.write(folder / 'delivery-state.json', {
            'content_version': row['content_version'], 'requirement': {'asset_type': 'button'}, 'size_spec': 'sizes.json',
        })
        self.write(folder / 'sizes.json', {'sources': [{'id': 'orig', 'path': str(historical_original)}]})
        confirmed = {'state': 'confirmed', 'code': previous, 'requirement_id': row['requirement_id'],
                     'content_version': row['content_version'], 'verified_material_ids': ['historical']}
        self.write(historical_original.parent.parent / 'status-finish.json', confirmed)
        deliveries = [{'source_id': 'orig', 'language': 'ja', 'member_id': '300x250',
                       'material_id': 'historical', 'resource_id': 'exact'}]
        report = {'requirement_id': row['requirement_id'], 'code': previous, 'complete': True,
                  'publication_unknown': [], 'verified_deliveries': deliveries}
        with patch('image_localizer.fireplay_reuse.next_delivery', return_value=report):
            self.assertEqual(['exact'], [item['resource_id'] for item in self.search()['local_candidates']])
            report['publication_unknown'] = ['unconfirmed']
            self.assertEqual([], self.search()['local_candidates'])
            report['publication_unknown'] = []
            Image.new('RGB', (300, 250), 'blue').save(historical_original)
            self.assertEqual([], self.search()['local_candidates'])

    def test_vector_scores_do_not_authorize_hash_mismatches(self):
        source = self.original.read_bytes()
        sha = hashlib.sha256(source).hexdigest()
        md5 = hashlib.md5(source).hexdigest()
        hits = [{'id': item, 'matchedResourceId': f'{item}-bytes', 'canView': True,
                 'matchedResourceUrl': f'https://example.invalid/{item}.png', 'similarityScore': score}
                for item, score in (('changed', 0.999), ('exact', 0.91), ('different-size', 0.95))]
        spec = {'source_sha256': sha, 'image_search': {'items': hits}}
        planned = self.search(spec)['fireplay']['resource_lookups']
        self.assertEqual(['changed', 'exact', 'different-size'], planned[0]['arguments']['material_ids'])
        spec['resources'] = [{'items': [{
            'material_id': hit['id'], 'complete': True, 'query_status': 'ready',
            'images': [{'resource_id': hit['matchedResourceId'], 'status': 'normal',
                        'url': hit['matchedResourceUrl'], 'md5': md5,
                        'width': 301 if hit['id'] == 'different-size' else 300, 'height': 250}],
        } for hit in hits]}]
        calls = []

        def transfer(url, sink, *, max_bytes):
            calls.append(url)
            sink(source if '/exact.' in url else bytes(reversed(source)))
            self.assertEqual(len(source), max_bytes)
            return len(source)

        with patch('image_localizer.fireplay_reuse.transfer_public_bytes', side_effect=transfer):
            result = self.search(spec)['fireplay']
        self.assertEqual(['exact'], result['verified_source_material_ids'])
        self.assertEqual(['exact'], result['requirement_lookup']['arguments']['material_ids'])
        self.assertEqual(2, len(calls))
        spec['source_sha256'] = 'unrelated'
        with self.assertRaisesRegex(ValueError, 'different original'):
            self.search(spec)

    def retention_fixture(self):
        code = 'TEST-20261125-002'
        original = self.register_source(code, 'red')
        folder = original.parent.parent
        row = json.loads((folder / 'request.json').read_bytes())
        production = self.root / 'work/fireplay-production' / code
        self.write(production / 'delivery-state.json', {
            'content_version': row['content_version'], 'requirement': {'asset_type': 'button'}, 'size_spec': 'sizes.json',
        })
        self.write(production / 'sizes.json', {'sources': [{'id': 'orig', 'path': str(original)}]})
        self.write(folder / 'status-finish.json', {
            'state': 'confirmed', 'code': code, 'requirement_id': row['requirement_id'],
            'content_version': row['content_version'], 'verified_material_ids': ['historical'],
            'verified_members': 1, 'readback_at': '2026-11-19T20:00:00+00:00',
        })
        self.write(self.root / 'config/storage-retention.json',
                   {'media_days': 7, 'archive_days': 180, 'quarantine_days': 14})
        old_time = datetime(2026, 11, 18, tzinfo=timezone.utc).timestamp()
        os.utime(original, (old_time, old_time))
        delivery = {'source_id': 'orig', 'language': 'ja', 'member_id': '300x250', 'width': 300, 'height': 250,
                    'delivery_mode': 'explicit_members', 'material_id': 'historical', 'resource_id': 'exact',
                    'output_sha256': 'a' * 64}
        report = {'requirement_id': row['requirement_id'], 'code': code, 'complete': True, 'verified_members': 1,
                  'publication_unknown': [], 'verified_deliveries': [delivery]}
        return code, original, row, report

    def test_retention_preserves_exact_lookup_after_media_leaves(self):
        code, original, row, report = self.retention_fixture()
        now = datetime(2026, 11, 26, 20, tzinfo=timezone.utc)
        with patch('image_localizer.fireplay_reuse.next_delivery', return_value=report):
            before = self.search()['local_candidates']
            preview = maintain_storage(self.root, code=code, now=now)['demands'][0]
            self.assertEqual('media_due', preview['state'])
            self.assertFalse(archive_path(original.parent.parent, row).exists())
            self.assertTrue(original.exists())
            result = maintain_storage(self.root, code=code, now=now, apply=True)['demands'][0]
            self.assertEqual('media_quarantined', result['state'])
        self.assertFalse(original.exists())
        self.assertEqual(before, self.search()['local_candidates'])
        self.assertEqual([], self.search()['historical_records_to_check'])
        purge = manage_quarantine(self.root, code, purge=True, now=now)['operations'][0]
        self.assertEqual('retained', purge['state'])
        recovery = manage_quarantine(self.root, code, apply=True, now=now)['operations'][0]
        self.assertEqual(1, recovery['files'])
        self.assertTrue(original.exists())
        self.assertEqual([], self.search()['local_candidates'])
        self.assertEqual('manual_hold', maintain_storage(self.root, code=code, now=now)['demands'][0]['state'])

    def test_retired_archive_does_not_fall_back_to_legacy_candidates(self):
        code, original, row, report = self.retention_fixture()
        now = datetime(2027, 6, 5, 20, tzinfo=timezone.utc)
        with patch('image_localizer.fireplay_reuse.next_delivery', return_value=report):
            result = maintain_storage(self.root, code=code, now=now, apply=True)['demands'][0]
        self.assertEqual('archive_quarantined', result['state'])
        self.assertFalse(archive_path(original.parent.parent, row).exists())
        self.assertEqual([], self.search()['local_candidates'])
        self.assertEqual([], self.search()['historical_records_to_check'])
        with patch('image_localizer.storage_retention.datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            retention_hold(self.root, code, 'rework')
            retention_hold(self.root, code, 'rework resolved', release=True)
        future = datetime(2027, 6, 20, 20, tzinfo=timezone.utc)
        purged = manage_quarantine(self.root, code, purge=True, apply=True, now=future)
        self.assertEqual(2, sum(item['files'] for item in purged['operations']))
        self.assertTrue((original.parent.parent / 'status-finish.json').is_file())

    def test_stage_artifact_reuses_identical_destination_and_rejects_replacement(self):
        target = self.root / 'proofs' / 'original.png'
        first = stage_artifact(self.original, target)
        self.assertEqual(self.original.read_bytes(), target.read_bytes())
        self.assertNotEqual(self.original.stat().st_ino, target.stat().st_ino)
        self.assertEqual(first['sha256'], stage_artifact(self.original, target)['sha256'])
        other = self.register_source('TEST-20261125-003', 'blue')
        with self.assertRaisesRegex(ValueError, 'new revision path'):
            stage_artifact(other, target)
        self.assertEqual(self.original.read_bytes(), target.read_bytes())

    def test_reference_protection_and_corrupt_archive_fail_closed(self):
        code, original, row, report = self.retention_fixture()
        now = datetime(2026, 11, 26, 20, tzinfo=timezone.utc)
        self.write(self.root / 'runs/shared/frozen.json', {'path': str(original)})
        with patch('image_localizer.fireplay_reuse.next_delivery', return_value=report):
            result = maintain_storage(self.root, code=code, now=now, apply=True)['demands'][0]
        self.assertEqual('referenced_evidence', result['state'])
        self.assertTrue(original.exists())
        archive_path(original.parent.parent, row).write_bytes(b'invalid gzip')
        self.assertEqual([], self.search()['local_candidates'])
        self.assertIn(code, self.search()['historical_records_to_check'])
        result = maintain_storage(self.root, code=code, now=now, apply=True)['demands'][0]
        self.assertEqual('kept_for_review', result['state'])
        self.assertTrue(original.exists())

    def test_batch_matches_current_spec_and_stage_downloads_once(self):
        previous, _, old, historical = self.retention_fixture()
        demand = self.root / 'work/fireplay-production' / self.code
        registered = fireplay.load_record(self.code, self.root)
        pixels = self.original.read_bytes()
        historical['verified_deliveries'][0]['output_sha256'] = hashlib.sha256(pixels).hexdigest()
        historical['verified_deliveries'][0]['delivery_mode'] = None
        state = demand / 'delivery-state.json'
        self.write(state, {'requirement': {'id': registered['requirement_id'], 'code': self.code, 'asset_type': 'button'},
                           'content_version': registered['content_version'], 'size_spec': 'sizes.json'})
        self.write(demand / 'sizes.json', {'sources': [
            {'id': 'current', 'path': str(self.original)},
            {'id': 'same-bytes', 'path': str(self.original)},
        ]})
        member = {'source_id': 'current', 'language': 'ja', 'member_id': '300x250',
                  'width': 300, 'height': 250, 'max_bytes': 150 * 1024}
        spec = {'status': 'planned', 'asset_type': 'button', 'items': [member, {**member, 'source_id': 'same-bytes'},
                {**member, 'member_id': '336x280', 'width': 336, 'height': 280}]}
        panel = {'view': 'delivery', 'serverTime': datetime.now(timezone.utc).isoformat(),
                 'items': [{'id': old['requirement_id'], 'code': previous, 'status': 'partially_rejected',
                            'materials': [{'id': 'historical'}]}]}
        evidence = {'historical_delivery': panel, 'material_detail': {'material': {
            'id': 'historical', 'canUse': True, 'status': 'available', 'type': 'button', 'images': [
                {'id': 'exact', 'url': 'https://example.invalid/render.png', 'status': 'normal',
                 'width': 300, 'height': 250, 'fileSize': len(pixels), 'mimeType': 'image/png'}]}}}

        def transfer(url, sink, *, max_bytes):
            sink(pixels)
            return len(pixels)

        with (patch('image_localizer.fireplay_reuse.plan_sizes', return_value=spec),
              patch('image_localizer.fireplay_reuse.next_delivery', side_effect=lambda path, **kwargs:
                    historical if kwargs.get('include_verified') else {
                        'actions': [dict(member, action='make')], 'publication_unknown': []}),
              patch('image_localizer.fireplay_reuse.transfer_public_bytes', side_effect=transfer) as download):
            plan = reuse_plan(self.root, self.code, state)
            self.assertEqual(1, plan['unique_originals_checked'])
            self.assertEqual(2, len(plan['matches']))
            self.assertEqual('336x280', plan['unmatched'][0]['member_id'])
            self.assertEqual('rework', reuse_plan(self.root, self.code, state, rework=True)['mode'])
            with self.assertRaisesRegex(ValueError, 'requirement status'):
                stage_reuse(self.root, self.code, state, 'current', 'ja', '300x250', 'historical', evidence)
            panel['items'][0]['status'] = 'done'
            staged = stage_reuse(self.root, self.code, state, 'current', 'ja', '300x250', 'historical', evidence)
            self.assertEqual(pixels, Path(staged['path']).read_bytes())
            existing = stage_reuse(self.root, self.code, state, 'current', 'ja', '300x250', 'historical', evidence)
            self.assertEqual('reused', existing['mode'])
            download.assert_called_once()

    def test_panel_reverse_search_requires_source_relation(self):
        receipt = {'view': 'summary', 'next_cursor': 'partial', 'query_meta': {
            'filters_applied': {'material_ids': ['original']}}, 'items': [
                {'id': 'source-id', 'code': 'OLD', 'status': 'done', 'matched_relations': ['source']},
                {'id': 'reference-id', 'code': 'OTHER', 'status': 'done', 'matched_relations': ['reference']},
                {'id': 'rejected-id', 'code': 'REJECTED', 'status': 'partially_rejected', 'matched_relations': ['source']},
            ]}
        result = _requirement_candidates(['original'], self.code, receipt)
        self.assertEqual(['source-id'], result['delivery_lookup']['arguments']['requirement_ids'])
        self.assertTrue(result['panel_lookup_partial'])

    def search(self, receipt=None):
        return search_history(self.root, self.code, 1, receipt)
