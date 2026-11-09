"""Boundary tests using actual local image bytes, never fabricated visual scores."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.delivery_quality import CHECKS, WEIGHTS, assess_delivery


class DeliveryQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('source.png', 'candidate.png'):
            Image.new('RGB', (40, 30), 'white').save(self.base / name)
        self.review = {
            'requirement_id': 'req-1', 'source_id': 'source-1',
            'language': 'ja', 'member_id': 'source-1-ja-40x30',
            'source': self.binding('source.png'), 'artifact': self.binding('candidate.png'),
            'reviewer': {'identity': 'host-agent', 'kind': 'self'},
            'attestation': {'source_viewed': True, 'candidate_viewed': True,
                            'notes': 'Compared full source and candidate at 100%; title and price inspected.'},
            'dimensions': {name: {'score': 5, 'notes': 'Title, price and lower caption compared at 100%.'}
                           for name in WEIGHTS},
            'checks': {name: {'checked': True, 'notes': 'Source and candidate title/price areas agree.'}
                       for name in CHECKS},
            'requirements': [{'id': 'title-translation', 'checked': True,
                              'notes': 'Japanese title retains source meaning and fits its container.'}],
            'spec': {'width': 40, 'height': 30, 'max_bytes': 10000, 'formats': ['PNG']},
            'blocking_issues': [], 'minor_differences': [], 'targeted_reworks': 0,
        }

    def binding(self, name):
        return {'path': name, 'sha256': hashlib.sha256((self.base / name).read_bytes()).hexdigest()}

    def assess(self):
        return assess_delivery(self.review, self.base)

    def scores(self, values):
        for name, value in zip(WEIGHTS, values):
            self.review['dimensions'][name]['score'] = value

    def test_minor_differences_pass_and_stop_at_80(self):
        self.scores([5, 4, 3, 4, 3])  # 30 + 20 + 12 + 12 + 6
        self.review['minor_differences'] = ['Lower nonidentity background has a softer shadow.']
        result = self.assess()
        self.assertEqual(80, result['score'])
        self.assertEqual('ready_for_submission', result['status'])
        self.assertTrue(result['stop_refining'])
        self.assertEqual(self.review['minor_differences'], result['minor_differences'])
        self.assertEqual('host_declared', result['visual_assessment'])
        self.assertEqual('not_assessed', result['user_acceptance'])

    def test_each_dimension_floor_is_required_despite_high_total(self):
        for name, below in zip(WEIGHTS, [4, 3, 2, 3, 2]):
            with self.subTest(name=name):
                self.scores([5] * 5)
                self.review['dimensions'][name]['score'] = below
                self.assertEqual('rework', self.assess()['status'])

    def test_70_boundary_and_two_rework_limit_affect_only_member(self):
        self.scores([4, 4, 3, 3, 3])  # exactly 71
        self.assertEqual(71, self.assess()['score'])
        self.review['dimensions']['composition']['score'] = 2.5
        self.assertEqual(70, self.assess()['score'])
        for rounds in (0, 1):
            self.review['targeted_reworks'] = rounds
            self.assertEqual('rework', self.assess()['status'])
        self.review['targeted_reworks'] = 2
        result = self.assess()
        self.assertEqual('blocked', result['status'])
        self.assertEqual('member', result['scope'])
        self.assertEqual(self.review['member_id'], result['member_id'])
        other = deepcopy(self.review)
        other['member_id'] = 'other'
        other['targeted_reworks'] = 0
        self.assertEqual('rework', assess_delivery(other, self.base)['status'])
        self.scores([5] * 5)
        self.assertEqual('ready_for_submission', self.assess()['status'])

    def test_below_70_fails_gate_but_allows_two_targeted_repairs(self):
        self.scores([2.5] * 5)
        for rounds in (0, 1, 2):
            self.review['targeted_reworks'] = rounds
            result = self.assess()
            self.assertEqual(50, result['score'])
            self.assertEqual('blocked', result['status'])
            self.assertEqual('blocked' if rounds == 2 else 'rework', result['next_action'])

    def test_wrong_number_blocks_submission_but_can_be_corrected(self):
        self.review['checks']['amounts_numbers']['checked'] = False
        self.review['checks']['amounts_numbers']['notes'] = 'Candidate price is 99; clear source price is 19.'
        result = self.assess()
        self.assertEqual(100, result['score'])
        self.assertEqual('blocked', result['status'])
        self.assertEqual('rework', result['next_action'])
        self.review['targeted_reworks'] = 2
        self.assertEqual('blocked', self.assess()['next_action'])

    def test_photo_concealment_cannot_be_offset_by_high_readability_score(self):
        # Models still supply the observation; this checks that scoring cannot waive it.
        self.review['checks']['no_visible_seams'] = {
            'checked': False,
            'notes': 'A wide dark blurred band removes dress and road texture in the text area.',
        }
        result = self.assess()
        self.assertEqual(100, result['score'])
        self.assertEqual('blocked', result['status'])
        self.assertEqual('rework', result['next_action'])
        self.review['targeted_reworks'] = 2
        self.assertEqual('blocked', self.assess()['next_action'])

    def test_unresolved_facts_block_without_guessing_even_at_high_score(self):
        self.review['unresolved_facts'] = ['Source price digit is illegible; cannot confirm 19 or 79.']
        for rounds in (0, 1, 2):
            self.review['targeted_reworks'] = rounds
            result = self.assess()
            self.assertEqual('blocked', result['status'])
            self.assertEqual('blocked', result['next_action'])
            self.assertFalse(result['stop_refining'])
        for value in (None, True, 'unknown', ['']):
            self.review['unresolved_facts'] = value
            with self.assertRaises(ValueError):
                self.assess()

    def test_hard_checks_and_explicit_issues_block_even_at_100(self):
        for name in CHECKS:
            with self.subTest(name=name):
                self.review['checks'][name]['checked'] = False
                self.assertEqual('blocked', self.assess()['status'])
                self.review['checks'][name]['checked'] = True
        self.review['blocking_issues'] = ['Price incorrectly changed from 19 to 99.']
        self.assertEqual('blocked', self.assess()['status'])

    def test_image_bytes_changed_with_old_sha_need_review(self):
        Image.new('RGB', (40, 30), 'red').save(self.base / 'candidate.png')
        result = self.assess()
        self.assertEqual('needs_review', result['status'])
        self.assertIn('artifact:', result['reasons'][0])
        self.assertIn('SHA256 mismatch', result['reasons'][0])

    def test_stale_source_hash_needs_review(self):
        Image.new('RGB', (40, 30), 'red').save(self.base / 'source.png')
        self.assertEqual('needs_review', self.assess()['status'])

    def test_actual_dimensions_byte_limit_and_format_are_checked(self):
        for field, value in [('width', 41), ('height', 31), ('max_bytes', 1), ('formats', ['JPEG'])]:
            with self.subTest(field=field):
                previous = self.review['spec'][field]
                self.review['spec'][field] = value
                self.assertEqual('blocked', self.assess()['status'])
                self.review['spec'][field] = previous

    def test_missing_corrupt_or_truncated_image_cannot_pass(self):
        (self.base / 'candidate.png').unlink()
        self.assertEqual('needs_review', self.assess()['status'])
        for data in (b'not an image', (self.base / 'source.png').read_bytes()[:40]):
            (self.base / 'candidate.png').write_bytes(data)
            self.review['artifact'] = self.binding('candidate.png')
            self.assertEqual('needs_review', self.assess()['status'])

    def test_missing_fields_cannot_default_to_pass(self):
        for name in list(self.review):
            with self.subTest(name=name):
                original = self.review.pop(name)
                with self.assertRaises(ValueError):
                    self.assess()
                self.review[name] = original

    def test_invalid_numeric_types_and_nonfinite_values(self):
        for value in (True, False, float('nan'), float('inf'), -1, 6, '5', None):
            with self.subTest(value=value):
                self.review['dimensions']['semantic_accuracy']['score'] = value
                with self.assertRaises(ValueError):
                    self.assess()
        self.scores([5] * 5)
        for value in (True, 1.0, -1, 3, None):
            self.review['targeted_reworks'] = value
            with self.assertRaises(ValueError):
                self.assess()

    def test_boolean_fields_are_strict_and_observations_required(self):
        self.review['checks']['amounts_numbers']['checked'] = 1
        with self.assertRaises(ValueError):
            self.assess()
        self.review['checks']['amounts_numbers']['checked'] = True
        self.review['checks']['amounts_numbers']['notes'] = ' '
        with self.assertRaises(ValueError):
            self.assess()
        self.review['checks']['amounts_numbers']['notes'] = 'Both show 19.'
        self.review['attestation']['source_viewed'] = 1
        with self.assertRaises(ValueError):
            self.assess()
        self.review['attestation']['source_viewed'] = False
        self.assertEqual('needs_review', self.assess()['status'])

    def test_requirements_cannot_be_empty_false_duplicate_or_unobserved(self):
        requirement = deepcopy(self.review['requirements'][0])
        for values in ([], [dict(requirement, notes='')], [requirement, requirement]):
            self.review['requirements'] = values
            with self.assertRaises(ValueError):
                self.assess()
        self.review['requirements'] = [dict(requirement, checked=False)]
        self.assertEqual('blocked', self.assess()['status'])

    def test_input_and_image_bytes_are_unchanged(self):
        before = deepcopy(self.review)
        files = {path: path.read_bytes() for path in self.base.iterdir()}
        self.assess()
        self.assertEqual(before, self.review)
        self.assertEqual(files, {path: path.read_bytes() for path in self.base.iterdir()})

    def test_unexpected_exception_propagates_never_passes(self):
        with patch('image_localizer.delivery_quality.Image.open', side_effect=RuntimeError('unexpected')):
            with self.assertRaises(RuntimeError):
                self.assess()

    def test_claimed_pass_does_not_replace_missing_evidence(self):
        self.review.update(status='pass', user_approved=True, score=100)
        self.review['attestation']['candidate_viewed'] = False
        self.assertEqual('needs_review', self.assess()['status'])


if __name__ == '__main__':
    unittest.main()
