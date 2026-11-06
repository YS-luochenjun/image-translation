"""Synthetic real-shaped connector receipts; no network or platform mutations."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import fireplay

CODE = 'ABC-20261103-001'
URL = 'https://images.example.test/source.png?version=1'


def row(**changes):
    return {'id': 'f3e17b15-2e91-4a5c-b5a8-a04b457d02bb', 'code': CODE,
            'updatedAt': '2026-11-03T01:00:00Z', 'assetType': 'image',
            'title': 'Image translation', 'status': 'pending', 'priority': 'P1',
            'description': 'Translate this image.\n' + URL, 'refText': '',
            'locales': ['English', 'ja', 'Japanese'], **changes}


def response(*rows, more=False, cursor=None):
    return {'items': list(rows), 'has_more': more, 'next_cursor': cursor,
            'serverTime': '2026-11-03T01:00:03Z'}


def page(*rows, cursor=None, more=False, next_cursor=None, **query):
    return {'arguments': {**fireplay.QUERY, **query, **({'cursor': cursor} if cursor else {})},
            'response': response(*rows, more=more, cursor=next_cursor)}


def spec(**changes):
    return {'summary': response(row()), 'brief': response(row()), **changes}


class DiscoveryTests(unittest.TestCase):
    def test_exact_ai_modes_select_image_and_button_without_title_inference(self):
        mode = 'image_translate_or_resize'
        query = {'asset_type': ['image', 'button'], 'ai_mode': mode}
        candidates = [row(aiMode=mode, title='Plain poster'),
                      row(id=str(uuid4()), code='ABC-20261103-002', assetType='button', aiMode=mode),
                      row(id=str(uuid4()), code='ABC-20261103-003', aiMode=None),
                      row(id=str(uuid4()), code='ABC-20261103-004', aiMode='image_generate')]
        packet = fireplay.discover({'query': {'asset_type': ['image', 'button']},
                                   'required_ai_modes': [mode],
                                   'summary_pages': [page(*candidates, **query)]})
        self.assertEqual([item['code'] for item in packet['selected']], [CODE, 'ABC-20261103-002'])
        self.assertFalse(packet['selected'][0]['translation_hint'])
        self.assertEqual(packet['needs_classification'][0]['code'], 'ABC-20261103-003')
        self.assertEqual(packet['requirements'][-1]['classification'], 'excluded_ai_mode')
        self.assertEqual(len(packet['host_actions']), 3)
        self.assertNotIn('required_ai_modes', packet['host_actions'][0]['arguments'])
        reference = fireplay.discover({'query': {'asset_type': 'button'}, 'required_ai_modes': [mode]})
        self.assertEqual(reference['host_actions'][0]['arguments']['ai_mode'], mode)
        self.assertEqual(reference['host_actions'][0]['arguments']['asset_type'], 'button')

    def test_invalid_required_modes_and_conflicting_query_are_rejected(self):
        for modes in ([], 'image_translate_or_resize', ['Bad label'], ['same', 'same'], [None]):
            with self.subTest(modes=modes), self.assertRaises(ValueError):
                fireplay.discover({'required_ai_modes': modes})
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            fireplay.discover({'required_ai_modes': ['image_translate_or_resize'],
                               'query': {'ai_mode': 'image_generate'}})

    def test_same_uuid_with_changed_case_is_a_duplicate(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            fireplay.discover({'summary_pages': [page(row(), more=True, next_cursor='next'),
                page(row(id=row()['id'].upper(), code='ABC-20261103-002'), cursor='next')]})

    def test_reference_packet_is_honest_and_image_scoped(self):
        packet = fireplay.discover()
        self.assertEqual(packet['schema_status'], 'reference_only')
        self.assertEqual(packet['host_actions'][0]['arguments']['asset_type'], 'image')
        self.assertFalse(packet['platform_claimed'])
        self.assertFalse(packet['complete'])

    def test_empty_complete_differs_from_empty_partial(self):
        empty = fireplay.discover({'summary_pages': [page()]})
        self.assertEqual((empty['state'], empty['complete']), ('empty', True))
        partial = fireplay.discover({'summary_pages': [page(more=True, next_cursor='cursor-2')]})
        self.assertEqual((partial['state'], partial['complete']), ('partial', False))
        self.assertEqual(partial['host_actions'][0]['arguments']['cursor'], 'cursor-2')

    def test_pagination_p0_order_and_video_exclusion(self):
        urgent = row(id=str(uuid4()), code='ABC-20261103-002', priority='P0')
        video = row(id=str(uuid4()), code='ABC-20261103-003', assetType='video')
        packet = fireplay.discover({'summary_pages': [page(row(), more=True, next_cursor='next'),
            page(urgent, video, cursor='next')]})
        self.assertEqual([item['code'] for item in packet['requirements']], [urgent['code'], CODE])
        self.assertTrue(packet['complete'])
        self.assertEqual(packet['host_actions'][0]['arguments']['view'], 'blind_brief')

    def test_wrong_filters_chain_and_more_flags_are_rejected(self):
        cases = [[page(cursor='missing')], [page(more=True)],
                 [page(), page(cursor='extra')],
                 [page(more=True, next_cursor='n'), page(cursor='wrong')],
                 [page(more=True, next_cursor='n'), page(cursor='n', keyword='changed')],
                 [page(more=True, next_cursor='n'), page(cursor='n', more=True, next_cursor='n')]]
        missing_flag = page()
        del missing_flag['response']['has_more']
        cases.append([missing_flag])
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                fireplay.discover({'summary_pages': pages})
        with self.assertRaises(ValueError):
            fireplay.discover({'query': {'asset_type': 'video'}})

    def test_live_keyset_cursor_receipts_preserve_raw_and_follow_pages(self):
        def live(*rows, cursor=None, next_cursor=None):
            value = page(*rows, cursor=cursor, next_cursor=next_cursor)
            del value['response']['has_more']
            value['response']['query_meta'] = {'pagination': 'live_keyset', 'snapshot': False,
                                               'total_exact': True, 'returned': len(rows),
                                               'source_limit_reached': False}
            return value
        terminal = live(row())
        original = deepcopy(terminal)
        self.assertTrue(fireplay.discover({'summary_pages': [terminal]})['complete'])
        self.assertEqual(terminal, original)
        first = live(row(), next_cursor='next')
        second = live(row(id=str(uuid4()), code='ABC-20261103-002'), cursor='next')
        full = fireplay.discover({'summary_pages': [first, second]})
        self.assertTrue(full['complete'])
        self.assertEqual(len(full['requirements']), 2)
        partial = fireplay.discover({'summary_pages': [first]})
        self.assertFalse(partial['complete'])
        self.assertEqual(partial['next_cursor'], 'next')
        self.assertEqual(partial['host_actions'][0]['arguments']['cursor'], 'next')

    def test_missing_or_conflicting_pagination_evidence_rejects(self):
        cases = [page(next_cursor='conflict')]
        for bad_cursor in ('', ' ', 42, False):
            value = page(next_cursor=bad_cursor)
            del value['response']['has_more']
            value['response']['query_meta'] = {'pagination': 'live_keyset'}
            cases.append(value)
        missing = page()
        del missing['response']['has_more']
        del missing['response']['next_cursor']
        missing['response']['query_meta'] = {'pagination': 'live_keyset'}
        cases.append(missing)
        wrong = page()
        del wrong['response']['has_more']
        wrong['response']['query_meta'] = {'pagination': 'live_offset'}
        cases.append(wrong)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fireplay.discover({'summary_pages': [value]})

    def test_content_parsing_resolves_only_supported_aliases(self):
        parsed = fireplay._content(row(), {}, None)
        self.assertEqual(parsed['targets'], ['en', 'ja'])
        self.assertEqual(parsed['sources'][0]['url'], URL)
        self.assertEqual(parsed['blockers'], [])
        unknown = fireplay._content(row(locales=['English', 'Klingon']), {}, None)
        self.assertEqual(unknown['unresolved_targets'], ['Klingon'])
        self.assertIn('target_locales_unresolved', unknown['blockers'])
        negative = fireplay._content(row(description='Do not translate ' + URL), {}, None)
        self.assertFalse(negative['translation_intent'])

    def test_evidence_and_connector_error_parsing(self):
        with self.assertRaises(ValueError):
            fireplay.payload({'isError': True})
        with self.assertRaises(ValueError):
            fireplay._content(row(), {'translation_intent': True, 'source_quote': 'invented',
                                     'explanation': 'unsupported host claim'}, None)
        decision = {'translation_intent': False, 'source_quote': 'Translate this image.',
                    'explanation': 'Host clarified this is a design reference only.'}
        parsed = fireplay._content(row(), decision, None)
        self.assertFalse(parsed['translation_intent'])
        self.assertIn('translation_not_confirmed', parsed['blockers'])

    def test_duplicate_and_conflicting_id_code_are_rejected(self):
        for duplicate in (row(), row(id=str(uuid4())), row(code='ABC-20261103-002')):
            with self.subTest(duplicate=duplicate), self.assertRaises(ValueError):
                fireplay.discover({'summary_pages': [page(row(), duplicate)]})


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def register(self, value=None):
        return fireplay.register(CODE, value or spec(), self.root)

    def test_button_registration_and_local_accept_preserve_semantic_gate(self):
        value = row(assetType='button')
        record = self.register(spec(summary=response(value), brief=response(value)))
        self.assertEqual(record['state'], 'ready')
        self.assertEqual(fireplay.accept(CODE, self.root)['state'], 'accepted')
        value = row(assetType='button', description='Design a new poster. ' + URL)
        blocked = self.register(spec(summary=response(value), brief=response(value)))
        self.assertIn('translation_not_confirmed', blocked['blockers'])
        with self.assertRaises(ValueError):
            fireplay.accept(CODE, self.root)

    def test_wrapped_real_shaped_receipts_and_local_accept(self):
        value = spec()
        value['summary'] = {'structuredContent': {'data': value['summary']}}
        value['brief'] = {'content': [{'type': 'text', 'text': json.dumps(value['brief'])}]}
        record = self.register(value)
        self.assertEqual(record['state'], 'ready')
        self.assertEqual(record['sources'][0]['url'], URL)
        self.assertEqual(record['sources'][0]['index'], 1)
        self.assertEqual(record['targets'], ['en', 'ja'])
        accepted = fireplay.accept(CODE, self.root)
        self.assertEqual(accepted['state'], 'accepted')
        self.assertFalse(accepted['platform_claimed'])
        self.assertEqual(fireplay.accept(CODE, self.root), accepted)
        self.assertEqual(fireplay.status(CODE, self.root), accepted)

    def test_identity_timestamp_exact_code_and_video_mismatch(self):
        for changes in ({'id': str(uuid4())}, {'id': 'not-a-uuid'}, {'updatedAt': 'later'},
                        {'code': CODE + '0'}, {'assetType': 'video'}):
            value = spec(brief=response(row(**changes)))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.register(value)
        with self.assertRaises(ValueError):
            self.register(spec(summary=response(row(), row())))
        self.assertFalse((self.root / 'state').exists())

    def test_title_alone_does_not_classify_design_as_translation(self):
        value = row(description='Design a new poster. ' + URL)
        record = self.register(spec(summary=response(value), brief=response(value)))
        self.assertIn('translation_not_confirmed', record['blockers'])
        with self.assertRaises(ValueError):
            fireplay.accept(CODE, self.root)

    def test_missing_sources_targets_and_unknown_languages_block(self):
        for changes, blocker in [({'description': 'Translate poster with no image URL'}, 'image_sources_unresolved'),
                                 ({'locales': []}, 'target_locales_unresolved'),
                                 ({'locales': ['English', 'Klingon']}, 'target_locales_unresolved'),
                                 ({'status': 'done'}, 'platform_requirement_closed'),
                                 ({'status': 'cancelled'}, 'platform_requirement_closed'),
                                 ({'status': None}, 'platform_status_unresolved')]:
            value = row(**changes)
            with self.subTest(changes=changes):
                record = self.register(spec(summary=response(value), brief=response(value)))
                self.assertIn(blocker, record['blockers'])
                with self.assertRaises(ValueError):
                    fireplay.accept(CODE, self.root)

    def test_target_label_aliases_and_explicit_evidenced_host_decision(self):
        value = row(description='Please adapt the supplied image. Target languages: English, Japanese\n' + URL,
                    locales=[])
        decision = {'translation_intent': True, 'source_quote': 'Please adapt the supplied image.',
                    'explanation': 'Host read the brief and confirms text translation.', 'targets': ['English', 'ja'],
                    'sources': [{'url': URL, 'source_quote': URL, 'material_id': 'image-1'}]}
        record = self.register(spec(summary=response(value), brief=response(value), decision=decision))
        self.assertEqual(record['state'], 'ready')
        self.assertEqual(record['targets'], ['en', 'ja'])
        decision['source_quote'] = 'invented evidence'
        with self.assertRaises(ValueError):
            self.register(spec(summary=response(value), brief=response(value), decision=decision))

    def test_material_receipt_allows_extensionless_url_with_evidence(self):
        url = 'https://images.example.test/object/123'
        decision = {'sources': [{'url': url, 'material_id': '123', 'source_quote': url}]}
        record = self.register(spec(decision=decision,
            material_receipt={'data': {'items': [{'material_id': '123', 'url': url, 'type': 'image'}]}}))
        self.assertEqual(record['sources'][0]['url'], url)
        with self.assertRaises(ValueError):
            self.register(spec(decision=decision))

    def test_image_extension_only_and_no_automatic_attachment_guessing(self):
        for url in ('https://example.test/clip.mp4', 'https://example.test/object/123'):
            value = row(description='Translate ' + url)
            record = self.register(spec(summary=response(value), brief=response(value)))
            self.assertEqual(record['sources'], [])

    def test_summary_pages_can_register_exact_requirement(self):
        value = spec(summary_pages=[page(row())])
        del value['summary']
        self.assertEqual(self.register(value)['state'], 'ready')

    def test_revisions_preserve_accepted_content_and_downloads(self):
        first = self.register()
        accepted = fireplay.accept(CODE, self.root)
        downloaded = fireplay.record_download(CODE, 1,
            {'content_version': first['content_version'], 'local_path': '/fixture/source.png'}, self.root)
        snapshot = fireplay.request_folder(CODE, self.root) / 'snapshots' / ('accepted-' + first['content_version'] + '.json')
        original_bytes = snapshot.read_bytes()
        unchanged = spec()
        for key in ('summary', 'brief'):
            unchanged[key]['items'][0].update(updatedAt='2026-11-03T02:00:00Z', priority='P0')
        refresh = self.register(unchanged)
        self.assertEqual(refresh['state'], 'accepted')
        self.assertEqual(refresh['downloads'], downloaded['downloads'])
        newer = row(description='Translate updated image. ' + URL)
        revision = self.register(spec(summary=response(newer), brief=response(newer)))
        self.assertEqual(revision['state'], 'revision_pending')
        self.assertNotEqual(revision['content_version'], first['content_version'])
        self.assertEqual(revision['downloads'], {})
        self.assertEqual(snapshot.read_bytes(), original_bytes)
        self.assertIn(accepted['content_version'], revision['accepted_versions'])
        self.assertEqual(self.register(spec(summary=response(newer), brief=response(newer)))['state'], 'revision_pending')
        self.assertEqual(fireplay.accept(CODE, self.root)['state'], 'accepted')
        restored = self.register()
        self.assertEqual(restored['state'], 'accepted')
        self.assertEqual(restored['downloads'], downloaded['downloads'])

    def test_negative_intent_and_identity_replacement_are_blocked(self):
        value = row(description='Do not translate this image. ' + URL)
        record = self.register(spec(summary=response(value), brief=response(value)))
        self.assertFalse(record['translation_intent'])
        with self.assertRaises(ValueError):
            fireplay.accept(CODE, self.root)
        changed = row(id=str(uuid4()))
        with self.assertRaisesRegex(ValueError, 'UUID changed'):
            self.register(spec(summary=response(changed), brief=response(changed)))

    def test_exclusive_lock_and_download_version_gate(self):
        record = self.register()
        with self.assertRaises(ValueError):
            fireplay.record_download(CODE, 1, {'content_version': record['content_version']}, self.root)
        fireplay.accept(CODE, self.root)
        for index, version in ((2, record['content_version']), (1, 'stale')):
            with self.assertRaises(ValueError):
                fireplay.record_download(CODE, index, {'content_version': version}, self.root)
        lock = fireplay.request_folder(CODE, self.root) / '.lock'
        lock.write_text('active operation', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'locked'):
            fireplay.accept(CODE, self.root)
        self.assertEqual(lock.read_text(encoding='utf-8'), 'active operation')

    def test_path_traversal_and_connector_errors(self):
        for code in ('../escape', 'ABC-20261103-001/../../escape', CODE.lower()):
            with self.assertRaises(ValueError):
                fireplay.request_folder(code, self.root)
        with self.assertRaises(ValueError):
            self.register(spec(summary={'isError': True, 'items': [row()]}))


if __name__ == '__main__':
    unittest.main()
