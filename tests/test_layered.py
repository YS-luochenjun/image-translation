"""End-to-end guardrails for reusable stage execution and review provenance."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.layered import prepare_layered, render_layered, check_layered
from image_localizer.pipeline import sha256, read_json, write_json


class LayeredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        image = Image.new('RGB', (40, 30), 'white')
        image.putpixel((1, 1), (0, 0, 0))
        image.save(self.root / 'source.png')
        self.plan = {'schema_version': 1, 'source': 'source.png', 'source_sha256': sha256(self.root / 'source.png'),
                     'background': {'operations': [{'type': 'solid', 'bbox': [0, 0, 3, 3], 'color': 'white'}]},
                     'rows': [], 'assets': [], 'protected_regions': [{'id': 'product', 'bbox': [30, 20, 5, 5]}]}
        self.plan_path = self.root / 'plan.json'
        self.prepared = self.root / 'background'
        self.output = self.root / 'render'
        self.save_plan()

    def save_plan(self):
        write_json(self.plan_path, self.plan)

    def render(self):
        prepare_layered(self.plan_path, self.prepared)
        return render_layered(self.plan_path, self.prepared, self.output)

    def review(self):
        review = read_json(self.output / 'review-template.json')
        review['reviewer'] = 'test fixture; synthetic review, not image approval'
        review['checks'] = {name: True for name in review['checks']}
        review['stage_notes'] = {name: 'Synthetic test observation' for name in review['stage_notes']}
        if 'advisory_reviews' in review:
            review['advisory_reviews'] = {name: True for name in review['advisory_reviews']}
        write_json(self.output / 'review.json', review)
        return review

    def test_artifacts_are_pending_until_actual_review(self):
        result = self.render()
        self.assertEqual(result['technical'], 'pass')
        self.assertEqual(check_layered(self.output)['status'], 'pending')
        self.review()
        self.assertEqual(check_layered(self.output)['status'], 'pass')
        self.assertEqual(check_layered(self.output)['user_acceptance'], 'not_recorded')

    def test_same_stage_directory_never_overwritten(self):
        self.render()
        before = sha256(self.output / 'translated.png')
        with self.assertRaises(ValueError):
            render_layered(self.plan_path, self.prepared, self.output)
        with self.assertRaises(ValueError):
            prepare_layered(self.plan_path, self.prepared)
        self.assertEqual(sha256(self.output / 'translated.png'), before)

    def test_changed_rows_can_reuse_background_but_do_not_inherit_review(self):
        self.render()
        self.review()
        # Asset changes affect only composition; no background rebuild required.
        Image.new('RGBA', (2, 2), '#ff000080').save(self.root / 'asset.png')
        self.plan['assets'] = [{'id': 'badge', 'path': 'asset.png', 'position': [10, 10], 'kind': 'decoration'}]
        self.save_plan()
        second = self.root / 'second'
        render_layered(self.plan_path, self.prepared, second)
        self.assertEqual(check_layered(second)['status'], 'pending')
        self.assertFalse((second / 'review.json').exists())
        # The historic render no longer has matching live plan input.
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_changed_repair_requires_new_background(self):
        prepare_layered(self.plan_path, self.prepared)
        self.plan['background']['operations'][0]['color'] = '#ffffee'
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'prepare a new background'):
            render_layered(self.plan_path, self.prepared, self.output)

    def test_background_and_receipt_tampering_detected(self):
        prepare_layered(self.plan_path, self.prepared)
        Image.new('RGBA', (40, 30), 'black').save(self.prepared / 'background.png')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            render_layered(self.plan_path, self.prepared, self.output)

    def test_external_background_requires_accurate_mask(self):
        Image.new('RGBA', (40, 30), 'red').save(self.root / 'clean.png')
        Image.new('L', (40, 30), 0).save(self.root / 'mask.png')
        self.plan['background'] = {'path': 'clean.png', 'mask': 'mask.png'}
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'outside'):
            prepare_layered(self.plan_path, self.prepared)
        self.assertFalse(self.prepared.exists())

    def test_background_cannot_touch_protected_subject(self):
        self.plan['background']['operations'].append({'type': 'solid', 'bbox': [30, 20, 1, 1], 'color': 'red'})
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'protected'):
            prepare_layered(self.plan_path, self.prepared)

    def test_asset_cannot_silently_overwrite_protected_subject(self):
        Image.new('RGBA', (2, 2), 'red').save(self.root / 'asset.png')
        self.plan['assets'] = [{'id': 'badge', 'path': 'asset.png', 'position': [30, 20], 'kind': 'decoration'}]
        self.save_plan()
        result = self.render()
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_changed_output_or_input_fails_current_review(self):
        self.render()
        self.review()
        Image.new('RGBA', (40, 30), 'red').save(self.output / 'translated.png')
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_stale_review_hash_rejected(self):
        self.render()
        review = self.review()
        review['output_sha256'] = '0' * 64
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_review_false_and_unresolved_major_block(self):
        self.render()
        review = self.review()
        review['checks']['semantic'] = False
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'fail')
        review['checks']['semantic'] = True
        review['issues'] = [{'severity': 'major', 'note': 'Unresolved glyph defect'}]
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_review_requires_all_stage_observations(self):
        self.render()
        review = self.review()
        review['stage_notes']['background'] = ''
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'pending')

    def test_receipt_cannot_omit_integrity_maps(self):
        self.render()
        write_json(self.output / 'receipt.json', {'stage': 'render', 'schema_version': 1, 'technical': 'pass'})
        self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_invalid_json_object_shapes_fail_cleanly(self):
        write_json(self.plan_path, [])
        with self.assertRaises(ValueError):
            prepare_layered(self.plan_path, self.prepared)
        self.save_plan()
        self.render()
        good = read_json(self.output / 'review-template.json')
        for invalid in [[], {**good, 'reviewer': 'fixture', 'checks': []},
                        {**good, 'reviewer': 'fixture', 'stage_notes': []}]:
            write_json(self.output / 'review.json', invalid)
            self.assertEqual(check_layered(self.output)['status'], 'fail')

    def test_multiframe_external_mask_rejected(self):
        Image.new('RGBA', (40, 30), 'white').save(self.root / 'clean.png')
        first = Image.new('L', (40, 30), 255)
        first.save(self.root / 'mask.tiff', save_all=True, append_images=[Image.new('L', (40, 30), 0)])
        self.plan['background'] = {'path': 'clean.png', 'mask': 'mask.tiff'}
        self.save_plan()
        with self.assertRaisesRegex(ValueError, 'single-frame'):
            prepare_layered(self.plan_path, self.prepared)

    def test_asset_warnings_require_specific_review(self):
        Image.new('RGBA', (4, 2), 'red').save(self.root / 'asset.png')
        self.plan['assets'] = [{'id': 'badge', 'path': 'asset.png', 'position': [10, 10], 'size': [2, 2], 'kind': 'decoration'}]
        self.save_plan()
        self.render()
        review = self.review()
        review['advisory_reviews'] = {}
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'pending')
        review['advisory_reviews'] = {key: True for key in review['advisories']}
        write_json(self.output / 'review.json', review)
        self.assertEqual(check_layered(self.output)['status'], 'pass')

    def test_unknown_fields_bad_scale_and_source_mismatch_rejected(self):
        original = copy.deepcopy(self.plan)
        for patch in [{'bogus': True}, {'working_scale': 1.5}, {'source_sha256': '0' * 64}]:
            self.plan = {**original, **patch}
            self.save_plan()
            with self.assertRaises(ValueError):
                prepare_layered(self.plan_path, self.prepared)


if __name__ == '__main__':
    unittest.main()
