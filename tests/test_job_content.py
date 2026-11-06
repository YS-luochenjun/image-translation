"""Semantic/lettering correspondence and immutable cross-language protections."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.job_content import merged_protections, validate_region_map


class RegionMapTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'regions': [{'id': 'title', 'action': 'translate', 'target_text': 'Hello world'}]}
        self.target = {
            'rows': [{'id': 'first', 'text': 'Hello'}, {'id': 'second', 'text': 'world'}],
            'assets': [],
            'region_map': [{'region_id': 'title', 'row_ids': ['first', 'second'], 'asset_texts': []}]}

    def validate(self):
        validate_region_map(self.target, self.manifest)

    def test_actual_rows_must_equal_semantic_text_in_declared_order(self):
        before = copy.deepcopy((self.target, self.manifest))
        self.validate()
        self.assertEqual((self.target, self.manifest), before)
        self.target['rows'][0]['text'] = 'Wrong'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.validate()
        self.target = copy.deepcopy(before[0])
        self.target['region_map'][0]['row_ids'].reverse()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.validate()

    def test_missing_region_missing_row_and_unused_text_asset_rejected(self):
        self.manifest['regions'].append({'id': 'subtitle', 'action': 'translate', 'target_text': 'Missing'})
        with self.assertRaisesRegex(ValueError, 'every translate region'):
            self.validate()
        self.manifest['regions'].pop()
        self.target['rows'].append({'id': 'unmapped', 'text': 'EXTRA'})
        with self.assertRaisesRegex(ValueError, 'Every actual row'):
            self.validate()
        self.target['rows'].pop()
        for kind in ['text', 'compound']:
            with self.subTest(kind=kind):
                self.target['assets'] = [{'id': 'unmapped', 'kind': kind}]
                with self.assertRaisesRegex(ValueError, 'Every text/compound asset'):
                    self.validate()

    def test_asset_only_and_mixed_content_have_exact_declared_text(self):
        self.target = {'rows': [], 'assets': [{'id': 'bitmap', 'kind': 'text'}],
                       'region_map': [{'region_id': 'title', 'row_ids': [],
                                       'asset_texts': [{'asset_id': 'bitmap', 'text': 'Hello world'}]}]}
        self.validate()
        self.target['rows'] = [{'id': 'prefix', 'text': 'Hello'}]
        mapping = self.target['region_map'][0]
        mapping['row_ids'] = ['prefix']
        mapping['asset_texts'][0]['text'] = 'world'
        self.target['assets'][0]['kind'] = 'compound'
        self.validate()
        mapping['asset_texts'][0]['text'] = 'Wrong'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.validate()
        mapping['asset_texts'][0]['text'] = ' \n\t'
        with self.assertRaisesRegex(ValueError, 'nonempty text'):
            self.validate()

    def test_zero_translations_requires_empty_rows_map_and_no_text_assets(self):
        self.manifest = {'regions': [{'id': 'brand', 'action': 'preserve'}]}
        self.target = {'rows': [], 'assets': [{'id': 'ornament', 'kind': 'decoration'}], 'region_map': []}
        self.validate()
        self.target['rows'] = [{'id': 'unexpected', 'text': 'EXTRA'}]
        with self.assertRaises(ValueError):
            self.validate()
        self.target['rows'] = []
        self.target['assets'][0]['kind'] = 'text'
        with self.assertRaises(ValueError):
            self.validate()
        self.target['assets'] = []
        self.target['region_map'] = [{'region_id': 'brand', 'row_ids': [], 'asset_texts': []}]
        with self.assertRaises(ValueError):
            self.validate()

    def test_nfc_whitespace_and_only_four_cjk_punctuation_characters_are_ignored(self):
        self.manifest['regions'][0]['target_text'] = 'Caf\u00e9，世。界、；'
        self.target['rows'] = [{'id': 'first', 'text': 'Cafe\u0301\n 世\t界\u3000'}]
        self.target['region_map'][0]['row_ids'] = ['first']
        self.validate()
        self.manifest['regions'][0]['target_text'] = 'A，B。C、D；E'
        self.target['rows'][0]['text'] = 'A B C D E'
        self.validate()

    def test_numeric_decimal_negative_percent_and_ascii_punctuation_changes_rejected(self):
        pairs = [('-1.50%', '1.50%'), ('1.50%', '150%'), ('1.50%', '1.50'),
                 ('(10)', '10'), ('1,000', '1000'), ('a;b', 'ab'), ('a:b', 'ab'),
                 ('a/b', 'ab'), ('a+b', 'ab'), ('a-b', 'ab'), ('a.b', 'ab'),
                 ('a!b', 'ab'), ('a?b', 'ab'), ('1.5', '1.6'), ('10', '１０'),
                 ('-1', '−1'), ('a！b', 'ab'), ('a：b', 'ab')]
        self.target['rows'] = [{'id': 'first', 'text': ''}]
        self.target['region_map'][0]['row_ids'] = ['first']
        for semantic, actual in pairs:
            with self.subTest(semantic=semantic, actual=actual):
                self.manifest['regions'][0]['target_text'] = semantic
                self.target['rows'][0]['text'] = actual
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    self.validate()
        self.manifest['regions'][0]['target_text'] = '价格，-1.50%；(10)'
        self.target['rows'][0]['text'] = '价 格 -1.50% (10)'
        self.validate()

    def test_all_row_and_asset_ids_share_one_unique_namespace(self):
        self.target['assets'] = [{'id': 'first', 'kind': 'text'}]
        with self.assertRaisesRegex(ValueError, 'Duplicate row/asset ID'):
            self.validate()
        self.target['assets'] = [{'id': 'first', 'kind': 'decoration'}]
        with self.assertRaisesRegex(ValueError, 'Duplicate row/asset ID'):
            self.validate()
        self.target['assets'] = []
        self.target['rows'][1]['id'] = 'first'
        with self.assertRaisesRegex(ValueError, 'Duplicate row/asset ID'):
            self.validate()
        self.target['rows'] = []
        self.target['assets'] = [{'id': 'same', 'kind': 'text'}, {'id': 'same', 'kind': 'compound'}]
        with self.assertRaisesRegex(ValueError, 'Duplicate row/asset ID'):
            self.validate()

    def test_repeated_region_and_repeated_content_references_rejected(self):
        self.target['region_map'].append(copy.deepcopy(self.target['region_map'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate region mapping'):
            self.validate()
        self.target['region_map'].pop()
        self.target['region_map'][0]['row_ids'].append('first')
        with self.assertRaisesRegex(ValueError, 'Row referenced more than once'):
            self.validate()
        self.target = {'rows': [], 'assets': [{'id': 'bitmap', 'kind': 'text'}],
                       'region_map': [{'region_id': 'title', 'row_ids': [], 'asset_texts': [
                           {'asset_id': 'bitmap', 'text': 'Hello'}, {'asset_id': 'bitmap', 'text': 'world'}]}]}
        with self.assertRaisesRegex(ValueError, 'Asset referenced more than once'):
            self.validate()

    def test_cross_region_reuse_is_rejected(self):
        self.manifest['regions'].append({'id': 'second_title', 'action': 'translate', 'target_text': 'Hello world'})
        self.target['region_map'].append({**copy.deepcopy(self.target['region_map'][0]), 'region_id': 'second_title'})
        with self.assertRaisesRegex(ValueError, 'Row referenced more than once'):
            self.validate()

    def test_unknown_preserve_and_decoration_references_and_malformed_input_rejected(self):
        original = copy.deepcopy(self.target)
        mutations = [
            lambda t: t.pop('region_map'),
            lambda t: t.update(region_map={}),
            lambda t: t['region_map'][0].update(extra=True),
            lambda t: t['region_map'][0].update(region_id='not-present'),
            lambda t: t['region_map'][0].update(row_ids=['not-present']),
            lambda t: t['region_map'][0].update(row_ids='first'),
            lambda t: t['region_map'][0].update(row_ids=[], asset_texts=[]),
            lambda t: t['region_map'][0].update(asset_texts=[{'asset_id': 'ornament', 'text': 'Hello'}]),
            lambda t: t['region_map'][0].update(asset_texts=[{'asset_id': 'x', 'text': 'Hi', 'extra': 1}]),
            lambda t: t['rows'][0].update(text=''),
        ]
        for mutation in mutations:
            self.target = copy.deepcopy(original)
            self.target['assets'] = [{'id': 'ornament', 'kind': 'decoration'}]
            mutation(self.target)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.validate()


class ProtectionUnionTests(unittest.TestCase):
    def test_manifest_explicit_and_preserve_boxes_join_project_protection(self):
        project = [{'id': 'portrait', 'bbox': [0, 0, 10, 20]}]
        manifests = [('en', {'protected_regions': [{'id': 'logo', 'bbox': [10, 0, 5, 5]}],
                             'regions': [{'id': 'preserve_label', 'action': 'preserve', 'bbox': [15, 0, 5, 5]},
                                         {'id': 'title', 'action': 'translate', 'bbox': [10, 5, 10, 15]}]}),
                     ('fr', {'protected_regions': [], 'regions': [
                         {'id': 'footnote', 'action': 'preserve', 'bbox': [10, 15, 10, 5]}]})]
        original = copy.deepcopy((project, manifests))
        union = merged_protections(project, manifests, (20, 20))
        self.assertEqual(len(union), 4)
        self.assertEqual([item['id'] for item in union], ['portrait', 'logo', 'preserve_label', 'footnote'])
        self.assertEqual((project, manifests), original)
        union[0]['bbox'][0] = 999
        union[1]['id'] = 'changed'
        union[2]['bbox'][1] = 999
        self.assertEqual((project, manifests), original)

    def test_bbox_deduplication_and_all_id_collision_suffixes_are_deterministic(self):
        project = [{'id': 'keep', 'bbox': [0, 0, 2, 2]}, {'id': 'keep-2', 'bbox': [2, 0, 2, 2]}]
        manifests = [('ja', {'protected_regions': [{'id': 'same-box', 'bbox': [0, 0, 2, 2]}],
                             'regions': [{'id': 'keep', 'action': 'preserve', 'bbox': [4, 0, 2, 2]},
                                         {'id': 'keep-3', 'action': 'preserve', 'bbox': [6, 0, 2, 2]}]}),
                     ('zh-CN', {'regions': [{'id': 'keep', 'action': 'preserve', 'bbox': [8, 0, 2, 2]}]})]
        result = merged_protections(project, manifests, (10, 10))
        self.assertEqual(result, merged_protections(project, manifests, (10, 10)))
        self.assertEqual([item['id'] for item in result], ['keep', 'keep-2', 'keep-3', 'keep-3-2', 'keep-4'])
        self.assertEqual(len({tuple(item['bbox']) for item in result}), 5)
        self.assertEqual(len({item['id'] for item in result}), 5)
        self.assertEqual(merged_protections([], [], (10, 10)), [])

    def test_invalid_protection_boxes_and_structures_are_rejected(self):
        for bbox in [[0, 0, 11, 1], [0, 0, True, 1], [-1, 0, 1, 1], [0, 0, 0, 1], [0, 0, 1.0, 1]]:
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                merged_protections([], [('ja', {'regions': [{'id': 'x', 'action': 'preserve', 'bbox': bbox}]})], (10, 10))
        for project, manifests, size in [(None, [], (10, 10)), ([], None, (10, 10)),
                                         ([], [('ja', {'regions': []})], (True, 10)),
                                         ([], [('ja', {'regions': []})], (0, 10)),
                                         ([], [('ja', {'regions': [], 'protected_regions': None})], (10, 10)),
                                         ([], [('ja', {'regions': [{'action': 'preserve', 'bbox': [0, 0, 1, 1]}]})], (10, 10))]:
            with self.subTest(project=project, manifests=manifests, size=size), self.assertRaises(ValueError):
                merged_protections(project, manifests, size)


if __name__ == '__main__':
    unittest.main()
