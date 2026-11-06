"""Local read-only autopilot, publication readback and explicit member regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.delivery_autopilot import next_delivery, publication_gate
from image_localizer.delivery_quality import CHECKS, WEIGHTS
from image_localizer.material_sizes import plan_sizes, validate_sizes


class DeliveryAutopilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        Image.new('RGB', (300, 300), 'white').save(self.base / 'source.png')
        self.spec = {'asset_type': 'image', 'delivery_mode': 'explicit_members',
                     'scope_basis': 'User explicitly requires these members',
                     'explicit_members': [{'member_id': 'one', 'width': 300, 'height': 300}],
                     'sources': [{'id': 'source', 'path': 'source.png', 'languages': ['ja']}]}
        self.state = {'requirement': {'id': 'req', 'code': 'R-1', 'asset_type': 'image',
                                     'designer_ids': ['designer'], 'shared_dept_ids': ['dept']},
                      'size_spec': 'sizes.json', 'members': [], 'publication_unknown': []}

    def save(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def plan(self):
        self.save('sizes.json', self.spec)
        return next_delivery(self.save('state.json', self.state))

    def action(self):
        return self.plan()['actions'][0]['action']

    def binding(self, name):
        return {'path': name, 'sha256': hashlib.sha256((self.base / name).read_bytes()).hexdigest()}

    def ready(self, member='one', language='ja', output=None):
        output = output or f'{member}-{language}.png'
        color = tuple(hashlib.sha256(f'{member}/{language}'.encode()).digest()[:3])
        Image.new('RGB', (300, 300), color).save(self.base / output)
        record = {'source_id': 'source', 'language': language, 'member_id': member,
                  'output_path': output, 'review_path': f'review-{member}-{language}.json'}
        review = {'requirement_id': 'req', 'source_id': 'source', 'language': language,
                  'member_id': member, 'source': self.binding('source.png'),
                  'artifact': self.binding(output), 'reviewer': {'identity': 'host', 'kind': 'self'},
                  'attestation': {'source_viewed': True, 'candidate_viewed': True,
                                  'notes': 'Source and candidate title, price and logo inspected at full size.'},
                  'dimensions': {name: {'score': 5, 'notes': 'Title and preserved logo compared at 100%.'}
                                 for name in WEIGHTS},
                  'checks': {name: {'checked': True, 'notes': 'Source and target price and logo agree.'}
                             for name in CHECKS},
                  'requirements': [{'id': 'title', 'checked': True, 'notes': 'Translated title preserves meaning.'}],
                  'spec': {'width': 300, 'height': 300, 'max_bytes': 5242880, 'formats': ['PNG']},
                  'blocking_issues': [], 'minor_differences': [], 'targeted_reworks': 0}
        self.save(record['review_path'], review)
        self.state['members'].append(record)
        return record, review

    def published(self, record):
        downloaded = 'download-' + record['output_path']
        (self.base / downloaded).write_bytes((self.base / record['output_path']).read_bytes())
        record['publication'] = {
            'upload_receipt': 'upload-observed', 'material_id': record['member_id'] + '-material',
            'resource_id': record['member_id'] + '-resource', 'attribution_receipt': 'attribution-observed',
            'binding_receipt': 'binding-observed'}
        record['publication']['fresh_verification'] = {
            'requirement_id': 'req', 'material_id': record['publication']['material_id'],
            'resource_id': record['publication']['resource_id'], 'creation_type': 'derivative',
            'source_id': record['source_id'], 'language': record['language'], 'member_id': record['member_id'],
            'designer_ids': ['designer'], 'shared_dept_ids': ['dept'],
            'sha256': self.binding(record['output_path'])['sha256'], 'downloaded_path': downloaded,
            'observed_at': datetime.now(timezone.utc).isoformat()}
        return record['publication']['fresh_verification']

    def test_every_phase_and_fresh_verification_are_required(self):
        self.assertEqual('make', self.action())
        record, review = self.ready()
        record.pop('review_path')
        self.assertEqual('review', self.action())
        record['review_path'] = 'review-one-ja.json'
        self.assertEqual('upload', self.action())
        record['publication'] = {'upload_receipt': 'receipt'}
        self.assertEqual('import', self.action())
        record['publication']['material_id'] = 'material'
        self.assertEqual('attribute', self.action())
        record['publication']['attribution_receipt'] = 'receipt'
        self.assertEqual('bind', self.action())
        record['publication']['binding_receipt'] = 'receipt'
        self.assertEqual('verify', self.action())
        self.published(record)
        result = self.plan()
        self.assertEqual('delivery_verified', result['actions'][0]['action'])
        self.assertTrue(result['complete'])
        self.assertFalse(result['external_write'])

    def test_rework_then_limit_blocks_only_member(self):
        record, review = self.ready()
        review['dimensions']['semantic_accuracy']['score'] = 4
        self.save(record['review_path'], review)
        self.assertEqual('rework', self.action())
        review['targeted_reworks'] = 2
        self.save(record['review_path'], review)
        self.assertEqual('blocked', self.action())

    def test_gate_failures_recommend_repair_without_submission(self):
        record, review = self.ready()
        for score in (2.5, 5):
            for name in WEIGHTS:
                review['dimensions'][name]['score'] = score
            review['checks']['amounts_numbers']['checked'] = score != 5
            self.save(record['review_path'], review)
            row = self.plan()['actions'][0]
            self.assertEqual('blocked', row['assessment']['status'])
            self.assertEqual('rework', row['action'])
        review['unresolved_facts'] = ['Source amount cannot be read reliably.']
        self.save(record['review_path'], review)
        self.assertEqual('blocked', self.action())
        review['unresolved_facts'] = []
        review['targeted_reworks'] = 2
        self.save(record['review_path'], review)
        self.assertEqual('blocked', self.action())

    def test_bad_member_review_never_stops_another_ready_member(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        first, review = self.ready('one')
        self.ready('two')
        path = self.base / first['review_path']
        for payload in (None, '{broken json', '{}', '[]', json.dumps(dict(review, source=None))):
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(payload, encoding='utf-8')
            with self.subTest(payload=payload):
                result = self.plan()
                self.assertEqual(['review', 'upload'], [row['action'] for row in result['actions']])
                self.assertFalse(result['complete'])

    def test_null_or_nontext_fresh_fields_require_verify_without_crashing(self):
        record, _ = self.ready()
        fresh = deepcopy(self.published(record))
        for field in ('observed_at', 'downloaded_path'):
            for value in (None, 123, False, [], {}):
                record['publication']['fresh_verification'] = dict(fresh, **{field: value})
                with self.subTest(field=field, value=value):
                    self.assertEqual('verify', self.action())
                    self.assertFalse(self.plan()['complete'])

    def test_stale_output_and_source_require_actual_review(self):
        record, review = self.ready()
        for name in (record['output_path'], 'source.png'):
            previous = (self.base / name).read_bytes()
            Image.new('RGB', (300, 300), 'red').save(self.base / name)
            self.assertEqual('review', self.action())
            (self.base / name).write_bytes(previous)

    def test_review_identity_and_paths_must_match_member(self):
        record, review = self.ready()
        for key in ('requirement_id', 'source_id', 'language', 'member_id'):
            other = deepcopy(review)
            other[key] = 'other'
            self.save(record['review_path'], other)
            self.assertEqual('review', self.action())
        self.save(record['review_path'], review)
        Image.new('RGB', (300, 300), 'white').save(self.base / 'other.png')
        for key in ('source', 'artifact'):
            other = deepcopy(review)
            other[key] = self.binding('other.png')
            self.save(record['review_path'], other)
            self.assertEqual('review', self.action())

    def test_review_cannot_weaken_formats_or_dimensions(self):
        record, review = self.ready()
        for field, value in [('width', 301), ('max_bytes', 5242881), ('formats', ['PNG', 'GIF'])]:
            other = deepcopy(review)
            other['spec'][field] = value
            self.save(record['review_path'], other)
            self.assertEqual('review', self.action())

    def test_stale_future_incomplete_and_wrong_readback_never_complete(self):
        record, review = self.ready()
        fresh = deepcopy(self.published(record))
        changes = [('source_id', 'other'), ('language', 'other'), ('member_id', 'other'),
                   ('requirement_id', 'other'), ('material_id', 'other'), ('resource_id', 'other'),
                   ('creation_type', 'original'), ('designer_ids', ['other']), ('shared_dept_ids', []),
                   ('sha256', '0' * 64), ('downloaded_path', record['output_path']),
                   ('downloaded_path', 'missing.png'),
                   ('observed_at', (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()),
                   ('observed_at', (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
                   ('observed_at', 'invalid')]
        for field, value in changes:
            record['publication']['fresh_verification'] = dict(fresh, **{field: value})
            with self.subTest(field=field, value=value):
                self.assertEqual('verify', self.action())
                self.assertFalse(self.plan()['complete'])
        for key in fresh:
            bad = deepcopy(fresh)
            bad.pop(key)
            record['publication']['fresh_verification'] = bad
            self.assertEqual('verify', self.action())
        record['publication']['fresh_verification'] = fresh
        (self.base / fresh['downloaded_path']).write_bytes(b'changed bytes')
        self.assertEqual('verify', self.action())

    def test_fresh_ids_must_be_arrays_and_material_id_must_be_text(self):
        record, _ = self.ready()
        fresh = self.published(record)
        for key in ('designer_ids', 'shared_dept_ids'):
            previous = self.state['requirement'][key]
            self.state['requirement'][key] = ['d']
            fresh[key] = 'd'
            self.assertEqual('verify', self.action())
            self.state['requirement'][key] = previous
            fresh[key] = previous
        record['publication']['material_id'] = 123
        fresh['material_id'] = 123
        self.assertEqual('verify', self.action())

    def test_unknown_publication_blocks_all_publishable_members(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        first, _ = self.ready('one')
        second, _ = self.ready('two')
        self.published(first)
        self.state['publication_unknown'] = ['unknown-write-operation']
        result = self.plan()
        self.assertEqual(['await_official_result'] * 2, [row['action'] for row in result['actions']])
        self.assertFalse(result['complete'])

    def test_duplicate_or_unexpected_member_records_are_rejected(self):
        record, _ = self.ready()
        self.state['members'].append(deepcopy(record))
        with self.assertRaises(ValueError):
            self.plan()
        self.state['members'][-1]['member_id'] = 'outside-matrix'
        with self.assertRaises(ValueError):
            self.plan()

    def test_explicit_seven_times_four_languages_is_28_not_unique_sizes(self):
        self.spec['explicit_members'] = [{'member_id': f'm{i}', 'width': 300, 'height': 300} for i in range(7)]
        self.spec['sources'][0]['languages'] = ['ja', 'en', 'de', 'fr']
        result = self.plan()
        self.assertEqual(28, result['expected_members'])
        self.assertEqual(28, len(result['actions']))
        self.assertFalse(result['complete'])
        self.assertEqual(0, result['verified_members'])
        self.assertEqual(28, plan_sizes(self.base / 'sizes.json')['expected_resource_count'])
        record, _ = self.ready('m0', 'ja')
        self.published(record)
        result = self.plan()
        self.assertEqual(1, result['verified_members'])
        self.assertFalse(result['complete'])
        for language in ('ja', 'en', 'de', 'fr'):
            for index in range(7):
                if (language, index) in (('ja', 0), ('fr', 6)):
                    continue
                record, _ = self.ready(f'm{index}', language)
                self.published(record)
        result = self.plan()
        self.assertEqual(27, result['verified_members'])
        self.assertFalse(result['complete'])
        record, _ = self.ready('m6', 'fr')
        self.published(record)
        self.assertTrue(self.plan()['complete'])

    def test_explicit_members_reject_duplicates_conflicts_and_bad_dimensions(self):
        original = deepcopy(self.spec)
        for members in ([], [{'member_id': 'one', 'width': True, 'height': 300}],
                        [{'member_id': 'one', 'width': 0, 'height': 300}],
                        original['explicit_members'] * 2):
            self.spec = dict(original, explicit_members=members)
            with self.assertRaises(ValueError):
                self.plan()
        self.spec = dict(original, explicit_sizes=[[300, 300]])
        with self.assertRaises(ValueError):
            self.plan()

    def test_size_validation_rejects_shared_file_for_different_members(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        self.spec['outputs'] = [{'source_id': 'source', 'language': 'ja', 'member_id': member,
                                 'path': 'source.png', 'adaptation': 'recompose'} for member in ('one', 'two')]
        result = validate_sizes(self.save('sizes.json', self.spec))
        self.assertEqual('fail', result['status'])
        self.assertEqual(1, result['covered_resource_count'])

    def test_autopilot_rejects_shared_output_for_different_members(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        for member in ('one', 'two'):
            record, _ = self.ready(member, output='shared.png')
            self.published(record)
        with self.assertRaises(ValueError):
            self.plan()

    def test_autopilot_rejects_same_bytes_at_different_member_paths(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        one, _ = self.ready('one')
        two, review = self.ready('two')
        (self.base / two['output_path']).write_bytes((self.base / one['output_path']).read_bytes())
        review['artifact'] = self.binding(two['output_path'])
        self.save(two['review_path'], review)
        with self.assertRaises(ValueError):
            self.plan()

    def test_publication_gate_binds_current_reviewed_member_sha_without_writes(self):
        record, review = self.ready()
        self.plan()
        before = {path: path.read_bytes() for path in self.base.iterdir()}
        gate = publication_gate(self.base / 'state.json')
        self.assertTrue(gate['passed'])
        self.assertEqual('req', gate['requirement_id'])
        self.assertEqual('host', gate['reviewed_by'])
        self.assertEqual(record['review_path'], gate['evidence'])
        self.assertEqual([{'path': str((self.base / record['output_path']).resolve()),
                           'sha256': review['artifact']['sha256'], 'source_id': 'source',
                           'language': 'ja', 'member_id': 'one'}], gate['outputs'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.base.iterdir()})
        Image.new('RGB', (300, 300), 'red').save(self.base / record['output_path'])
        stale = publication_gate(self.base / 'state.json')
        self.assertFalse(stale['passed'])
        self.assertEqual([], stale['outputs'])

    def test_publication_gate_allows_ready_subset_without_claiming_complete(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        self.ready('one')
        self.plan()
        gate = publication_gate(self.base / 'state.json')
        self.assertTrue(gate['passed'])
        self.assertEqual(2, gate['expected_members'])
        self.assertEqual(1, gate['reviewed_members'])
        self.assertEqual(['one'], [row['member_id'] for row in gate['outputs']])
        self.assertFalse(gate['requirement_complete'])

    def test_publication_gate_excludes_unready_members_and_all_unknown_writes(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        self.ready('one')
        record, review = self.ready('two')
        review['checks']['amounts_numbers']['checked'] = False
        self.save(record['review_path'], review)
        self.plan()
        gate = publication_gate(self.base / 'state.json')
        self.assertTrue(gate['passed'])
        self.assertEqual(['one'], [row['member_id'] for row in gate['outputs']])
        self.state['publication_unknown'] = ['unknown-operation']
        self.plan()
        gate = publication_gate(self.base / 'state.json')
        self.assertFalse(gate['passed'])
        self.assertFalse(gate['requirement_complete'])
        self.assertEqual([], gate['outputs'])
        self.assertEqual(0, gate['reviewed_members'])

    def test_planning_leaves_existing_input_bytes_unchanged(self):
        record, _ = self.ready()
        self.published(record)
        self.plan()
        before = {path: path.read_bytes() for path in self.base.iterdir()}
        next_delivery(self.base / 'state.json')
        self.assertEqual(before, {path: path.read_bytes() for path in self.base.iterdir()})


if __name__ == '__main__':
    unittest.main()
