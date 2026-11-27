"""Local read-only autopilot, publication readback and explicit member regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.delivery_autopilot import compact_delivery, next_delivery, publication_gate
from image_localizer.delivery_quality import CHECKS, WEIGHTS
from image_localizer.fireplay_publication_receipts import EMPTY_DESIGNER_WARNING, FILENAME_ATTRIBUTION_WARNING
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

    def receipt(self, tool, response, **evidence):
        operation_id = str(uuid4())
        received = {'operation_id': operation_id, 'tool': tool, 'requirement_id': 'req',
                    'state': 'received', 'response': {'structuredContent': response}, **evidence}
        path = self.save(f'publication-{operation_id}.json', received)
        entry = {key: value for key, value in received.items() if key != 'response'}
        entry['receipt_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        with (self.base / 'publication-journal.jsonl').open('a', encoding='utf-8') as journal:
            journal.write(json.dumps(entry) + '\n')
        return operation_id

    def receipt_group(self, members, material_id, *, index=0, warning=None, creation_type='derivative'):
        outputs, urls = [], []
        for member in members:
            record, _ = self.ready(member)
            record['group_id'] = material_id
            output = {field: record[field] for field in ('source_id', 'language', 'member_id')}
            output.update(self.binding(record['output_path']))
            url = f'https://example.invalid/{member}.png'
            self.receipt('material_r2_upload_from_bytes',
                         {'success': True, 'resource_id': f'{member}-resource', 'public_url': url}, outputs=[output])
            outputs.append(output)
            urls.append(url)
        item = {'item_index': index, 'success': True, 'material_id': material_id, 'type': 'image',
                'shared_dept_id': 'dept', 'designers': [], 'creation_type': creation_type,
                'count': len(outputs), 'urls': urls, 'warnings': [warning] if warning else []}
        return outputs, item

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
            'resource_id': (record['member_id'] + '-resource' if record['language'] == 'ja'
                            else f"{record['member_id']}-{record['language']}-resource"),
            'attribution_receipt': 'attribution-observed',
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

    def test_official_receipts_cover_unequal_groups_without_downloads(self):
        self.state['requirement']['designer_ids'] = []
        self.spec['explicit_members'] += [
            {'member_id': member, 'width': 300, 'height': 300} for member in ('two', 'three')]
        first, first_result = self.receipt_group(['one', 'two'], 'group-a', creation_type='unmarked',
                                                 warning='未识别到 DID，设计师为空')
        second, second_result = self.receipt_group(['three'], 'group-b', index=1)
        self.receipt('material_upload_from_url', {'results': [second_result, first_result]},
                     outputs=first + second, output_groups=[first, second])
        self.assertEqual(['attribute', 'attribute', 'bind'], [row['action'] for row in self.plan()['actions']])
        self.receipt('material_attribution_update', {'success': True, 'updated': ['group-a']},
                     attribution={'material_ids': ['group-a'], 'creation_type': 'derivative'})
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True,
                                   'materialIds': ['group-a', 'group-b']}]},
                     binding_material_ids=['group-a', 'group-b'])
        result = self.plan()
        self.assertTrue(result['complete'])
        self.assertEqual({'official_receipts': 3}, result['evidence_counts'])
        self.assertEqual(['group-a', 'group-b'], result['verified_material_ids'])
        self.assertFalse(list(self.base.glob('download-*')))
        compact = compact_delivery(result, self.base / 'delivery-state.json', None)
        self.assertIn('原执行会话', compact['owner_closeout'])
        unchanged = compact_delivery(result, self.base / 'delivery-state.json', compact['checkpoint'])
        self.assertEqual(compact['owner_closeout'], unchanged['owner_closeout'])

    def test_current_intake_requires_all_designers_before_closeout(self):
        self.state['requirement']['designer_ids'] = []
        self.save('intake.json', {'summary': {'items': [
            {'id': 'other', 'code': 'R-2', 'assigneeIds': ['outsider']},
            {'id': 'req', 'code': 'R-1', 'assigneeIds': ['designer', 'c74b62d9', 'second', 'b2fbcgab', 'designer']},
        ]}})
        outputs, item = self.receipt_group(['one'], 'group-a', warning=EMPTY_DESIGNER_WARNING)
        self.receipt('material_upload_from_url', {'results': [item]}, outputs=outputs, output_groups=[outputs])
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True, 'materialIds': ['group-a']}]})
        result = self.plan()
        self.assertEqual(['designer', 'second'], result['designer_ids'])
        self.assertEqual('attribute', result['actions'][0]['action'])
        self.assertTrue(result['actions'][0]['frozen'])
        self.assertFalse(result['complete'])
        for designers, complete in ((['designer'], False), (['second', 'designer'], True)):
            self.receipt('material_attribution_update', {'success': True, 'updated': ['group-a']},
                         attribution={'material_ids': ['group-a'], 'creation_type': 'derivative',
                                      'designer_ids': designers})
            self.assertEqual(complete, self.plan()['complete'])

    def test_official_import_designer_objects_use_ids_not_names(self):
        outputs, item = self.receipt_group(['one'], 'group-a')
        item['designers'] = [{'id': 'designer', 'name': 'Design Owner'}]
        self.receipt('material_upload_from_url', {'results': [item]}, outputs=outputs, output_groups=[outputs])
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True, 'materialIds': ['group-a']}]})
        self.assertTrue(self.plan()['complete'])
        self.state['requirement']['designer_ids'] = ['Design Owner']
        self.assertEqual('attribute', self.action())

    def test_current_intake_can_expect_no_designers(self):
        self.save('intake.json', {'summary': {'id': 'req', 'code': 'R-1', 'assigneeIds': ['c74b62d9', 'b2fbcgab']}})
        outputs, item = self.receipt_group(['one'], 'group-a', warning=EMPTY_DESIGNER_WARNING)
        self.receipt('material_upload_from_url', {'results': [item]}, outputs=outputs, output_groups=[outputs])
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True, 'materialIds': ['group-a']}]})
        result = self.plan()
        self.assertEqual([], result['designer_ids'])
        self.assertTrue(result['complete'])

    def test_warning_leaves_other_group_verified_and_unknown_blocks_finish(self):
        self.state['requirement']['designer_ids'] = []
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        first, first_result = self.receipt_group(['one'], 'group-a')
        second, second_result = self.receipt_group(['two'], 'group-b', index=1, warning='Resource changed')
        self.receipt('material_upload_from_url', {'results': [first_result, second_result]},
                     outputs=first + second, output_groups=[first, second])
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True,
                                   'materialIds': ['group-a', 'group-b']}]})
        result = self.plan()
        self.assertFalse(result['complete'])
        self.assertNotIn('owner_closeout', result)
        self.assertEqual(['delivery_verified', 'verify'], [row['action'] for row in result['actions']])
        self.assertEqual('group-b', result['actions'][1]['material_id'])
        with (self.base / 'publication-journal.jsonl').open('a', encoding='utf-8') as journal:
            journal.write(json.dumps({'tool': 'requirement_bind_materials', 'operation_id': 'timed-out',
                                      'requirement_id': 'req', 'state': 'unknown'}) + '\n')
        self.assertEqual(['timed-out'], self.plan()['publication_unknown'])

    def test_filename_warning_requires_later_attribution_for_each_material(self):
        self.state['requirement']['designer_ids'] = []
        self.spec['explicit_members'] += [
            {'member_id': member, 'width': 300, 'height': 300} for member in ('two', 'three')
        ]
        self.receipt('material_attribution_update', {'success': True, 'updated': ['group-b']},
                     attribution={'material_ids': ['group-b'], 'creation_type': 'derivative'})
        outputs, results, groups = [], [], []
        for index, (member, material_id) in enumerate(zip(('one', 'two', 'three'), ('group-a', 'group-b', 'group-c'))):
            group, item = self.receipt_group([member], material_id, index=index, creation_type='unmarked')
            item['warnings'] = [FILENAME_ATTRIBUTION_WARNING, EMPTY_DESIGNER_WARNING]
            if material_id == 'group-c':
                item['warnings'].append('Resource changed')
            outputs.extend(group)
            results.append(item)
            groups.append(group)
        self.receipt('material_upload_from_url', {'results': results}, outputs=outputs, output_groups=groups)
        self.assertEqual(['attribute', 'attribute', 'verify'], [row['action'] for row in self.plan()['actions']])

        self.receipt('material_attribution_update', {'success': True, 'updated': ['group-a', 'group-c']},
                     attribution={'material_ids': ['group-a', 'group-b', 'group-c'], 'creation_type': 'derivative'})
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'req', 'success': True,
                                   'materialIds': ['group-a', 'group-b', 'group-c']}]},
                     binding_material_ids=['group-a', 'group-b', 'group-c'])
        result = self.plan()
        self.assertEqual(['delivery_verified', 'attribute', 'verify'], [row['action'] for row in result['actions']])
        self.assertEqual({'official_receipts': 1}, result['evidence_counts'])
        self.assertFalse(result['complete'])
        self.assertFalse(list(self.base.glob('download-*')))

    def test_official_receipts_with_wrong_binding_cannot_close(self):
        self.state['requirement']['designer_ids'] = []
        outputs, item = self.receipt_group(['one'], 'group-a')
        self.receipt('material_upload_from_url', {'results': [item]}, outputs=outputs, output_groups=[outputs])
        self.receipt('requirement_bind_materials',
                     {'results': [{'requirement_id': 'another-req', 'success': True, 'materialIds': ['group-a']}]},
                     binding_material_ids=['group-a'])
        self.assertEqual('verify', self.action())
        self.assertFalse(self.plan()['complete'])

    def test_partial_import_without_a_result_for_its_group_requires_reconciliation(self):
        self.state['requirement']['designer_ids'] = []
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        first, first_result = self.receipt_group(['one'], 'group-a')
        second, _ = self.receipt_group(['two'], 'group-b', index=1)
        self.receipt('material_upload_from_url', {'results': [first_result]},
                     outputs=first + second, output_groups=[first, second])
        self.assertEqual(['bind', 'verify'], [row['action'] for row in self.plan()['actions']])
        self.assertFalse(self.plan()['complete'])

    def test_rework_continues_after_two_rounds(self):
        record, review = self.ready()
        review['dimensions']['semantic_accuracy']['score'] = 4
        self.save(record['review_path'], review)
        self.assertEqual('rework', self.action())
        review['targeted_reworks'] = 2
        self.save(record['review_path'], review)
        self.assertEqual('rework', self.action())

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
        self.assertEqual('rework', self.action())

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

    def test_empty_designer_is_allowed_and_excluded_people_are_rejected(self):
        record, _ = self.ready()
        fresh = self.published(record)
        self.state['requirement']['designer_ids'] = []
        fresh['designer_ids'] = []
        self.assertEqual('delivery_verified', self.action())
        self.state['requirement']['designer_ids'] = ['c74b62d9']
        fresh['designer_ids'] = ['c74b62d9']
        self.assertEqual('verify', self.action())

    def test_presigned_unknown_blocks_only_its_member(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        confirmed, _ = self.ready('one')
        pending, _ = self.ready('two')
        for record in (confirmed, pending):
            output = {field: record[field] for field in ('source_id', 'language', 'member_id')}
            output.update(self.binding(record['output_path']))
            if record is confirmed:
                self.receipt('material_r2_presign_upload', {
                    'resourceId': str(uuid4()), 'publicUrl': 'https://example.invalid/confirmed.png',
                    'transfer': {'method': 'PUT', 'http_status': 200, 'sha256': output['sha256']},
                }, outputs=[output])
            else:
                operation = {'operation_id': str(uuid4()), 'requirement_id': 'req', 'state': 'unknown',
                             'tool': 'material_r2_presign_upload', 'outputs': [output]}
                with (self.base / 'publication-journal.jsonl').open('a', encoding='utf-8') as journal:
                    journal.write(json.dumps(operation) + '\n')
        report = self.plan()
        self.assertTrue(report['isolate_unknown_uploads'])
        self.assertFalse(report['complete'])
        self.assertEqual({row['member_id']: row['action'] for row in report['actions']},
                         {'one': 'import', 'two': 'await_official_result'})

    def test_unknown_publication_blocks_only_members_without_publication_evidence(self):
        self.spec['explicit_members'].append({'member_id': 'two', 'width': 300, 'height': 300})
        first, _ = self.ready('one')
        second, _ = self.ready('two')
        self.published(first)
        self.state['publication_unknown'] = ['unknown-write-operation']
        result = self.plan()
        self.assertEqual(['delivery_verified', 'await_official_result'], [row['action'] for row in result['actions']])
        self.assertFalse(result['complete'])

    def test_journal_proves_frozen_member_even_after_local_candidate_and_review_disappear(self):
        record, review = self.ready()
        self.published(record)
        output = {'source_id': 'source', 'language': 'ja', 'member_id': 'one',
                  'sha256': review['artifact']['sha256'], 'path': record['output_path']}
        operation = {'operation_id': 'upload', 'requirement_id': 'req',
                     'tool': 'material_r2_upload_from_bytes', 'state': 'received',
                     'outputs': [output], 'response': {'structuredContent': {'resourceId': 'one-resource'}}}
        self.save('publication-journal.jsonl', operation)
        (self.base / record['output_path']).unlink()
        (self.base / record['review_path']).unlink()
        self.assertTrue(self.plan()['complete'])
        first_publication = record.pop('publication')
        self.assertEqual('import', self.action())
        operation['state'] = 'unknown'
        self.save('publication-journal.jsonl', operation)
        record['publication'] = first_publication
        self.assertEqual('await_official_result', self.action())

    def test_receipt_reference_preserves_freeze_and_rejects_changed_bytes(self):
        record, review = self.ready()
        output = {'source_id': 'source', 'language': 'ja', 'member_id': 'one',
                  'sha256': review['artifact']['sha256'], 'path': record['output_path']}
        operation_id = str(uuid4())
        operation = {'operation_id': operation_id, 'requirement_id': 'req',
                     'tool': 'material_r2_upload_from_bytes', 'state': 'received', 'outputs': [output]}
        receipt = self.base / f'publication-{operation_id}.json'
        receipt.write_text(json.dumps({**operation, 'response': {'structuredContent': {'resourceId': 'resource'}}}))
        self.save('publication-journal.jsonl', {**operation,
                  'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()})
        (self.base / record['output_path']).unlink()
        self.assertEqual('import', self.action())
        receipt.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'receipt hash mismatch'):
            self.plan()

    def test_authorized_same_byte_upload_recovers_original_unknown(self):
        record, _ = self.ready()
        output = {**self.binding(record['output_path']), 'source_id': 'source',
                  'language': 'ja', 'member_id': 'one'}
        tool = 'material_r2_upload_from_bytes'
        original = {'operation_id': str(uuid4()), 'requirement_id': 'req', 'tool': tool,
                    'state': 'unknown', 'outputs': [output], 'request_sha256': 'a' * 64}
        journal = self.save('publication-journal.jsonl', original)
        with journal.open('a', encoding='utf-8') as stream:
            stream.write('\n')
        self.state['publication_unknown'] = [original['operation_id']]
        self.assertEqual('await_official_result', self.action())

        replacement_id = str(uuid4())
        authorization = self.save('retry.json', {
            'authorization_source': 'explicit_current_user_message',
            'user_request': 'Retry this upload', 'requirement_id': 'req', 'code': 'R-1',
            'historical_unknown_operations': [
                {key: original[key] for key in ('operation_id', 'request_sha256')},
            ],
            'new_operations': {replacement_id: tool},
        })
        replacement = {
            **original, 'operation_id': replacement_id, 'state': 'received',
            'reupload_authorization': {
                'path': 'work/fireplay-production/R-1/retry.json',
                'sha256': hashlib.sha256(authorization.read_bytes()).hexdigest(),
            },
        }
        for replacement_output, expected in [({**output, 'sha256': 'b' * 64}, False), (output, True)]:
            replacement['outputs'] = [replacement_output]
            receipt = self.save(f'publication-{replacement_id}.json', {
                **replacement, 'response': {'structuredContent': {
                    'resourceId': 'one-resource', 'publicUrl': 'https://example.invalid/one.png',
                }},
            })
            entry = {**replacement, 'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}
            journal.write_text('\n'.join(map(json.dumps, [original, entry])) + '\n', encoding='utf-8')
            result = self.plan()
            self.assertEqual(expected, not result['publication_unknown'])
            self.assertEqual('import' if expected else 'await_official_result', result['actions'][0]['action'])

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
