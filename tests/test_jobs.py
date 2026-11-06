"""Integration checks for semantic, background and visual-review stage boundaries."""
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.cli import available_fonts, main
from image_localizer.jobs import initialize_job, prepare_job, render_job, check_job, verify_bundle
from image_localizer.pipeline import read_json, write_json, sha256
from image_localizer.layered import check_layered


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        font = next((Path(path) for path in available_fonts() if Path(path).name.lower() in ('arial.ttf', 'dejavusans.ttf')), None)
        if font is None:
            self.skipTest('A real Latin TTF is required')
        shutil.copyfile(font, self.root / 'font.ttf')
        image = Image.new('RGB', (100, 70), 'white')
        ImageDraw.Draw(image).text((15, 20), 'Old', fill='black')
        image.save(self.root / 'input.png')
        initialize_job(self.root / 'input.png', ['en', 'fr'], self.root / 'author')
        self.path = self.root / 'author' / 'job.json'
        self.project = read_json(self.path)
        self.project.update(background={'operations': [{'type': 'solid', 'bbox': [10, 10, 80, 45], 'color': 'white'}]},
                            protected_regions=[{'id': 'subject', 'bbox': [0, 0, 8, 70]}],
                            working_scale=2, view_scale=1,
                            visual_targets=[{'id': 'weight', 'bbox': [10, 10, 80, 45],
                                             'source_observation': 'Authored fixture uses dark text on white.',
                                             'criterion': 'Target must be legible at native size; synthetic fixture only.'}])
        for target in self.project['targets']:
            manifest_path = self.path.parent / target['manifest']
            manifest = read_json(manifest_path)
            manifest['analysis'] = {'complete': True, 'observations': ['Authored test fixture, not actual image approval.']}
            manifest['regions'] = [{'id': 'title', 'action': 'translate', 'source_text': 'Old', 'target_text': 'Hi',
                                    'bbox': [10, 10, 80, 45], 'confidence': 1, 'notes': 'Synthetic fixture',
                                    'style': {'font_path': str(self.root / 'font.ttf'), 'min_font_size': 8, 'max_font_size': 16},
                                    'repair': {'mode': 'solid', 'color': 'white'}}]
            write_json(manifest_path, manifest)
            target['region_map'] = [{'region_id': 'title', 'row_ids': ['title'], 'asset_texts': []}]
            target['rows'] = [{'id': 'title', 'text': 'Hi', 'font_path': str(self.root / 'font.ttf'),
                               'font_size': 16, 'center': [50, 33], 'color': '#333333'}]
        write_json(self.path, self.project)
        self.prepared = self.root / 'prepared'
        self.output = self.root / 'render'

    def prepare(self):
        return prepare_job(self.path, self.prepared)

    def approve_background_fixture(self):
        review = read_json(self.prepared / 'background-review-template.json')
        review.update(reviewer='synthetic test fixture; no visual approval', observations='Authored fixture background is white.')
        review['checks'] = {name: True for name in review['checks']}
        write_json(self.prepared / 'background-review.json', review)

    def render(self):
        self.prepare()
        self.approve_background_fixture()
        return render_job(self.prepared, self.output)

    def review_fixture(self, language='en'):
        path = self.output / language
        review = read_json(path / 'review-template.json')
        review.update(reviewer='synthetic fixture only', review_kind='self')
        review['checks'] = {name: True for name in review['checks']}
        review['stage_notes'] = {name: 'Synthetic test observation only' for name in review['stage_notes']}
        write_json(path / 'review.json', review)
        return review

    def test_init_does_not_recognize_or_translate(self):
        directory = self.root / 'fresh'
        result = initialize_job(self.root / 'input.png', ['ja', 'zh-CN'], directory)
        self.assertEqual(result['status'], 'pending')
        manifest = read_json(directory / 'languages/ja/manifest.json')
        self.assertFalse(manifest['analysis']['complete'])
        with self.assertRaises(ValueError):
            prepare_job(directory / 'job.json', self.root / 'unready')

    def test_safe_distinct_language_identifiers(self):
        for languages in [['../ja'], ['ja', 'JA'], [], ['zh-CN', '']]:
            with self.assertRaises(ValueError):
                initialize_job(self.root / 'input.png', languages, self.root / 'invalid')

    def test_background_must_be_actually_reviewed_before_lettering(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'pending')
        with self.assertRaisesRegex(ValueError, 'Background review is missing'):
            render_job(self.prepared, self.output)
        self.assertFalse(self.output.exists())
        self.approve_background_fixture()
        review_path = self.prepared / 'background-review.json'
        review = read_json(review_path)
        review['checks']['no_residual_text'] = None
        write_json(review_path, review)
        with self.assertRaises(ValueError):
            render_job(self.prepared, self.output)

    def test_all_language_reviews_stay_pending(self):
        result = self.render()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(check_job(self.output)['status'], 'pending')
        self.assertEqual(check_job(self.output)['user_acceptance'], 'not_recorded')
        self.assertEqual(sha256(self.output/'en/background.png'), sha256(self.output/'fr/background.png'))

    def test_frozen_inputs_do_not_depend_on_live_author_files(self):
        self.prepare()
        (self.root/'font.ttf').write_bytes(b'changed original font')
        write_json(self.path.parent/'languages/en/manifest.json', {'changed': True})
        self.approve_background_fixture()
        render_job(self.prepared, self.output)
        self.assertEqual(check_job(self.output)['status'], 'pending')
        for target in read_json(self.prepared/'job.json')['targets']:
            self.assertFalse(Path(target['rows'][0]['font_path']).is_absolute())

    def test_relocated_inputs_can_be_prepared_again_without_original_paths(self):
        self.prepare()
        relocated = self.root/'relocated'
        shutil.copytree(self.prepared, relocated)
        (self.root/'font.ttf').write_bytes(b'original no longer usable')
        second = self.root/'reproduced'
        prepare_job(relocated/'job.json', second)
        self.assertEqual(sha256(second/'background/background.png'), sha256(self.prepared/'background/background.png'))
        self.assertFalse((second/'background-review.json').exists())

    def test_changed_bundle_resource_is_rejected(self):
        self.prepare()
        path = next((self.prepared/'inputs').glob('*.ttf'))
        path.write_bytes(b'changed frozen resource')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_bundle(self.prepared)

    def test_background_review_change_invalidates_render_binding(self):
        self.render()
        path = self.prepared/'background-review.json'
        review = read_json(path)
        review['observations'] += ' modified'
        write_json(path, review)
        self.assertEqual(check_job(self.output)['status'], 'fail')
        self.assertEqual(check_layered(self.output/'en')['status'], 'fail')

    def test_semantic_mismatch_is_rejected(self):
        manifest_path = self.path.parent/'languages/en/manifest.json'
        manifest = read_json(manifest_path)
        manifest['source']['sha256'] = '0'*64
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            self.prepare()

    def test_unknown_job_fields_are_rejected(self):
        self.project['auto_approve'] = True
        write_json(self.path, self.project)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_generic_booleans_do_not_bypass_visual_comparison(self):
        self.render()
        self.review_fixture()
        self.assertEqual(check_layered(self.output/'en')['status'], 'pending')
        review = self.review_fixture()
        comparison = review['style_comparisons'][0]
        comparison.update(verdict='pass', target_observation='Test fixture text remained visible.',
                          evidence=['source.png', 'translated.png'])
        write_json(self.output/'en/review.json', review)
        self.assertEqual(check_layered(self.output/'en')['status'], 'pass')
        self.assertEqual(check_job(self.output)['status'], 'pending')

    def test_visual_fail_overrides_generic_style_pass(self):
        self.render()
        review = self.review_fixture()
        review['style_comparisons'][0]['verdict'] = 'fail'
        review['style_comparisons'][0]['target_observation'] = 'Known fixture defect.'
        write_json(self.output/'en/review.json', review)
        self.assertEqual(check_job(self.output)['status'], 'fail')

    def test_source_criteria_and_evidence_cannot_be_replaced(self):
        self.render()
        review = self.review_fixture()
        review['style_comparisons'][0].update(verdict='pass', target_observation='something',
                                              evidence=['source.png', '../old/translated.png'])
        write_json(self.output/'en/review.json', review)
        self.assertEqual(check_layered(self.output/'en')['status'], 'fail')
        review['style_comparisons'][0]['evidence'] = ['source.png', 'translated.png']
        review['style_comparisons'][0]['criterion'] = 'weakened requirement'
        write_json(self.output/'en/review.json', review)
        self.assertEqual(check_layered(self.output/'en')['status'], 'fail')

    def test_missing_language_does_not_silently_pass(self):
        self.render()
        record = read_json(self.output/'job-render.json')
        record['languages'] = ['en']
        write_json(self.output/'job-render.json', record)
        self.assertEqual(check_job(self.output)['status'], 'fail')

    def test_history_is_not_overwritten(self):
        self.render()
        before = sha256(self.output/'en/translated.png')
        with self.assertRaises(ValueError):
            prepare_job(self.path, self.prepared)
        with self.assertRaises(ValueError):
            render_job(self.prepared, self.output)
        self.assertEqual(sha256(self.output/'en/translated.png'), before)
    def test_wrong_display_text_is_not_accepted_as_semantic_translation(self):
        self.project['targets'][0]['rows'][0]['text'] = 'Wrong'
        write_json(self.path, self.project)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_copied_language_directory_cannot_impersonate_another_target(self):
        self.render()
        review = self.review_fixture()
        review['style_comparisons'][0].update(verdict='pass', target_observation='Authored fixture only.',
                                               evidence=['source.png', 'translated.png'])
        write_json(self.output/'en/review.json', review)
        self.assertEqual(check_layered(self.output/'en')['status'], 'pass')
        clone = self.root/'clone'
        shutil.copytree(self.output/'en', clone/'en')
        shutil.copytree(self.output/'en', clone/'fr')
        shutil.copyfile(self.output/'job-render.json', clone/'job-render.json')
        result = check_job(clone)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('another plan', result['reasons'][0])

    def test_bundle_cannot_drop_a_plan_from_its_frozen_inventory(self):
        self.prepare()
        path = self.prepared/'bundle.json'
        bundle = read_json(path)
        del bundle['files']['plan-en.json']
        write_json(path, bundle)
        plan_path = self.prepared/'plan-en.json'
        plan = read_json(plan_path)
        plan['rows'][0]['text'] = 'Wrong'
        write_json(plan_path, plan)
        with self.assertRaisesRegex(ValueError, 'omits dependency'):
            verify_bundle(self.prepared)

    def test_semantic_preservation_is_enforced_in_actual_composite(self):
        manifest_path = self.path.parent/'languages/en/manifest.json'
        manifest = read_json(manifest_path)
        manifest['regions'].append({'id': 'logo', 'action': 'preserve', 'source_text': 'Logo',
                                    'bbox': [90, 0, 10, 70], 'confidence': 1, 'notes': 'Keep unchanged'})
        write_json(manifest_path, manifest)
        asset = self.root/'black.png'
        Image.new('RGBA', (5, 5), 'black').save(asset)
        self.project['targets'][0]['assets'] = [{'id': 'decoration', 'path': str(asset),
                                                'kind': 'decoration', 'position': [92, 12]}]
        write_json(self.path, self.project)
        result = self.render()
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(check_job(self.output)['status'], 'fail')
        self.assertTrue(any(item['bbox'] == [90, 0, 10, 70]
                            for item in read_json(self.prepared/'plan-fr.json')['protected_regions']))

    def test_lower_level_semantic_plan_checks_actual_text_and_protection(self):
        from image_localizer.layered import load_plan
        self.prepare()
        plan = read_json(self.prepared/'plan-en.json')
        plan['rows'][0]['text'] = 'Wrong'
        wrong_path = self.prepared/'wrong-plan.json'
        write_json(wrong_path, plan)
        with self.assertRaisesRegex(ValueError, 'mapped lettering'):
            load_plan(wrong_path)
        plan['rows'][0]['text'] = 'Hi'
        manifest_path = self.prepared/plan['semantic_manifest']
        manifest = read_json(manifest_path)
        manifest['protected_regions'].append({'id': 'semantic-logo', 'bbox': [90, 0, 10, 70]})
        write_json(manifest_path, manifest)
        write_json(wrong_path, plan)
        with self.assertRaisesRegex(ValueError, 'semantic preserve regions'):
            load_plan(wrong_path)

    def test_lower_level_renderer_respects_job_background_gate(self):
        from image_localizer.layered import render_layered
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'background-review.json'):
            render_layered(self.prepared/'plan-en.json', self.prepared/'background', self.root/'bypass')
        self.assertFalse((self.root/'bypass').exists())


if __name__ == '__main__':
    unittest.main()
