"""Evaluation evidence safety, provenance and intentionally pending visual review."""
import copy
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import evaluation
from image_localizer.evaluation import build_comparison, run_benchmark


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Image.new('RGBA', (24, 20), 'white')
        source.save(self.root / 'source.png')
        source.putpixel((16, 10), (0, 0, 0, 255))
        source.save(self.root / 'candidate.png')
        self.spec = {'schema_version': 1, 'source': 'source.png',
                     'candidates': [{'id': 'translated', 'path': 'candidate.png'}],
                     'regions': [{'id': 'title', 'bbox': [12, 0, 12, 20]}],
                     'protected_regions': [{'id': 'portrait', 'bbox': [0, 0, 12, 20]}]}
        self.spec_path = self.root / 'spec.json'
        self.output = self.root / 'comparison'
        self.save()

    def save(self):
        self.spec_path.write_text(json.dumps(self.spec), encoding='utf-8')

    def build(self):
        return build_comparison(self.spec_path, self.output)

    def test_translation_changes_descriptive_protection_is_exact_and_review_pending(self):
        result = self.build()
        self.assertEqual(result['technical'], 'pass')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['candidates'][0]['changed_pixels'], 1)
        self.assertEqual(result['candidates'][0]['protected_changed_pixels'], {'portrait': 0})
        with Image.open(self.output / 'differences/000.png') as mask:
            self.assertEqual(mask.getpixel((16, 10)), 255)
            self.assertEqual(sum(mask.histogram()[1:]), 1)
        with Image.open(self.output / 'board.png') as board:
            self.assertEqual(board.size, (512, 84))
            self.assertEqual(board.getpixel((256 + 16, 64 + 10)), (0, 0, 0))
            self.assertEqual(board.getpixel((16, 64 + 10)), (255, 255, 255))
        with Image.open(self.output / 'regions/000.png') as crop:
            self.assertEqual(crop.size, (512, 84))
        self.assertEqual(result['binding']['spec']['sha256'], digest(self.spec_path))
        self.assertEqual(result['binding']['source']['sha256'], digest(self.root / 'source.png'))
        for relative, expected in result['artifact_sha256'].items():
            self.assertEqual(digest(self.output / relative), expected)
        review = json.loads((self.output / 'review-template.json').read_text(encoding='utf-8'))
        self.assertEqual(len(review['candidates'][0]['regions']), 2)
        for region in review['candidates'][0]['regions']:
            for row in region['style_comparison']:
                self.assertIsNone(row['source_observation'])
                self.assertIsNone(row['target_observation'])
                self.assertIsNone(row['verdict'])

    def test_protected_alpha_only_change_really_fails(self):
        with Image.open(self.root / 'candidate.png') as image:
            candidate = image.copy()
        candidate.putpixel((2, 3), (255, 255, 255, 254))
        candidate.save(self.root / 'candidate.png')
        result = self.build()
        self.assertEqual(result['technical'], 'fail')
        self.assertEqual(result['candidates'][0]['protected_changed_pixels'], {'portrait': 1})
        self.assertEqual(result['visual_review'], 'pending')

    def test_missing_and_invalid_images_do_not_create_output(self):
        for data in [None, b'not an image']:
            with self.subTest(data=data):
                path = self.root / 'candidate.png'
                if data is None:
                    path.unlink()
                else:
                    path.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_size_exif_and_animation_rejected(self):
        Image.new('RGB', (23, 20)).save(self.root / 'candidate.png')
        with self.assertRaisesRegex(ValueError, 'same canvas'):
            self.build()
        image = Image.new('RGB', (24, 20))
        exif = Image.Exif()
        exif[274] = 6
        image.save(self.root / 'candidate.png', exif=exif)
        with self.assertRaisesRegex(ValueError, 'EXIF'):
            self.build()
        image.save(self.root / 'candidate.gif', save_all=True,
                   append_images=[Image.new('RGB', image.size, 'red')])
        self.spec['candidates'][0]['path'] = 'candidate.gif'
        self.save()
        with self.assertRaisesRegex(ValueError, 'single-frame'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_inputs_read_only_and_existing_history_never_rewritten(self):
        paths = [self.spec_path, self.root / 'source.png', self.root / 'candidate.png']
        before = {path: digest(path) for path in paths}
        for path in paths:
            path.chmod(stat.S_IREAD)
        try:
            self.build()
            history = {p: digest(p) for p in self.output.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                self.build()
            self.assertEqual(before, {path: digest(path) for path in paths})
            self.assertEqual(history, {path: digest(path) for path in history})
        finally:
            for path in paths:
                path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_forged_adjacent_review_never_imported_or_auto_passed(self):
        (self.root / 'review.json').write_text('{"status":"pass","style":true}')
        self.assertEqual(self.build()['visual_review'], 'pending')
        review_path = self.output / 'review-template.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        review['status'] = 'pass'
        review_path.write_text(json.dumps(review), encoding='utf-8')
        second = build_comparison(self.spec_path, self.root / 'new-comparison')
        self.assertEqual(second['status'], 'pending')
        self.spec['review'] = {'status': 'pass'}
        self.save()
        with self.assertRaises(ValueError):
            build_comparison(self.spec_path, self.root / 'third-comparison')

    def test_strict_malformed_contracts(self):
        original = copy.deepcopy(self.spec)
        mutations = [
            lambda s: s.update(schema_version=True),
            lambda s: s.update(notes='claim pass'),
            lambda s: s.update(regions=[]),
            lambda s: s.update(candidates=[]),
            lambda s: s.update(source='https://example.com/x.png'),
            lambda s: s['regions'][0].update(bbox=[0, 0, True, 1]),
            lambda s: s['regions'][0].update(bbox=[0, 0, 25, 1]),
            lambda s: s['regions'][0].update(bbox=[0, 0, 1.0, 1]),
            lambda s: s['candidates'][0].update(id='../escape'),
            lambda s: s['candidates'].append(copy.deepcopy(s['candidates'][0])),
            lambda s: s['regions'].append(copy.deepcopy(s['regions'][0])),
            lambda s: s['candidates'][0].update(score=1),
            lambda s: s.update(protected_regions=None),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.spec = copy.deepcopy(original)
                mutate(self.spec)
                self.save()
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())
        for malformed in ['[]', '{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}', '{']:
            self.spec_path.write_text(malformed)
            with self.assertRaises(ValueError):
                self.build()

    def test_safety_budgets_preflight_without_output(self):
        for constant in ['MAX_SPEC_BYTES', 'MAX_FILE_BYTES', 'MAX_IMAGE_PIXELS',
                         'MAX_TOTAL_INPUT_PIXELS', 'MAX_OUTPUT_PIXELS']:
            with self.subTest(constant=constant), patch.object(evaluation, constant, 1):
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())
        self.spec['candidates'] = [{'id': f'c{i}', 'path': 'candidate.png'} for i in range(9)]
        self.save()
        with self.assertRaises(ValueError):
            self.build()
        self.spec['candidates'] = [{'id': 'c', 'path': 'candidate.png'}]
        self.spec['regions'] = [{'id': f'r{i}', 'bbox': [0, 0, 1, 1]} for i in range(33)]
        self.save()
        with self.assertRaises(ValueError):
            self.build()

    def benchmark_spec(self):
        return {'schema_version': 1, 'cases': [
            {'id': 'synthetic-damage', 'source': 'source.png', 'candidate': 'candidate.png',
             'protected_regions': [{'id': 'all', 'bbox': [0, 0, 24, 20]}],
             'expected_technical': 'fail', 'known_visual_defects': []}]}

    def test_benchmark_protection_failure_and_true_historical_flat_type_remain_distinct(self):
        spec = self.benchmark_spec()
        evidence = Path(__file__).resolve().parents[1] / 'knowledge/cases/trueself-20261030/evidence'
        spec['cases'].append({
            'id': 'trueself-old-flat-type', 'source': str(evidence / 'source.png'),
            'candidate': str(evidence / 'chinese-before.png'),
            'protected_regions': [{'id': 'portrait', 'bbox': [0, 0, 182, 250]}],
            'expected_technical': 'pass',
            'known_visual_defects': ['Historical user feedback: flat letter faces and insufficient soft shadow; '
                                     'requires actual visual review, not automatically detected.']})
        self.spec_path.write_text(json.dumps(spec), encoding='utf-8')
        before = {p: digest(p) for p in evidence.iterdir() if p.is_file()}
        result = run_benchmark(self.spec_path, self.output)
        self.assertEqual(result['technical_expectations'], 'pass')
        self.assertEqual([c['technical'] for c in result['cases']], ['fail', 'pass'])
        self.assertEqual(result['visual_review'], 'pending')
        old = result['cases'][1]
        self.assertTrue(old['known_visual_defects'])
        self.assertIn('requires actual visual review', old['known_visual_defects_provenance'])
        self.assertEqual(old['binding']['source']['sha256'],
                         'f5c1fad0cd3665a920a01a28050f9d0fe2f204bd609d522422fc1d0b2b77f861')
        self.assertEqual(before, {p: digest(p) for p in before})
        with self.assertRaises(ValueError):
            run_benchmark(self.spec_path, self.output)

    def test_input_mutation_during_emission_cannot_produce_completed_report(self):
        original_board = evaluation._board

        def mutate_after_board(panels, path):
            original_board(panels, path)
            Image.new('RGBA', (24, 20), 'red').save(self.root / 'candidate.png')

        with patch.object(evaluation, '_board', side_effect=mutate_after_board):
            with self.assertRaisesRegex(ValueError, 'Input changed'):
                self.build()
        self.assertFalse((self.output / 'report.json').exists())

    def test_benchmark_strict_contract_and_case_limit(self):
        original = self.benchmark_spec()
        mutations = [
            lambda s: s.update(cases=[]),
            lambda s: s.update(cases=s['cases'] * 13),
            lambda s: s['cases'][0].update(expected_technical='approved'),
            lambda s: s['cases'][0].update(known_visual_defects='flat'),
            lambda s: s['cases'][0].update(review={'status': 'pass'}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                spec = copy.deepcopy(original)
                mutation(spec)
                self.spec_path.write_text(json.dumps(spec))
                with self.assertRaises(ValueError):
                    run_benchmark(self.spec_path, self.output)
                self.assertFalse(self.output.exists())

    def test_benchmark_invalid_and_aggregate_budget(self):
        spec = self.benchmark_spec()
        self.spec_path.write_text(json.dumps(spec))
        with patch.object(evaluation, 'MAX_BENCHMARK_PIXELS', 1):
            with self.assertRaisesRegex(ValueError, 'aggregate'):
                run_benchmark(self.spec_path, self.output)
        self.assertFalse(self.output.exists())
        spec['cases'][0]['expected_technical'] = 'pass'
        self.spec_path.write_text(json.dumps(spec))
        result = run_benchmark(self.spec_path, self.output)
        self.assertEqual(result['technical_expectations'], 'fail')
        self.assertEqual(result['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
