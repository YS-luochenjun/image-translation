"""Executed CLI gate evidence and freshness regressions; no model calls."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import check_records
from image_localizer.cli import main
from image_localizer.layered import check_layered, prepare_layered, render_layered
from image_localizer.pipeline import sha256


class CheckRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='check-record-test-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'render'
        self.output.mkdir()
        self.input = self.root / 'font.bin'
        self.input.write_bytes(b'synthetic dependency, not an actual font')
        self.layered_fixture(self.output)

    @staticmethod
    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def layered_fixture(self, output):
        output.mkdir(exist_ok=True)
        (output / 'translated.png').write_bytes(b'synthetic output dependency')
        self.write_json(output / 'plan.json', {'fixture': True})
        self.write_json(output / 'receipt.json', {
            'artifact_sha256': {name: sha256(output / name)
                                for name in ('translated.png', 'plan.json')},
            'input_sha256': {str(self.input): sha256(self.input)},
        })

    def job_fixture(self):
        prepared = self.root / 'prepared'
        prepared.mkdir()
        self.write_json(prepared / 'job.json', {'fixture': 'frozen job'})
        self.write_json(prepared / 'background-review.json', {'observations': 'fixture'})
        origin = self.root / 'author.json'
        self.write_json(origin, {'fixture': 'provenance only'})
        self.write_json(prepared / 'bundle.json', {
            'files': {'job.json': sha256(prepared / 'job.json')},
            'origin_inputs': {str(origin): sha256(origin)},
        })
        for language in ('en', 'fr'):
            self.layered_fixture(self.output / language)
        self.write_json(self.output / 'job-render.json', {
            'prepared': str(prepared), 'languages': ['en', 'fr'],
        })
        return prepared, origin

    def records(self):
        return sorted((self.output / 'gate-checks').glob('*.json'))

    def record(self, command='check-layered', check=None):
        result = {'status': 'pass', 'user_acceptance': 'not_recorded'}
        before = set(self.records())
        returned = check_records.record_check(command, self.output, check or (lambda _: result))
        created = set(self.records()) - before
        self.assertEqual(len(created), 1)
        return returned, self.read_json(created.pop())

    def invoke_cli(self, command):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([command, str(self.output)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_preserves_exact_results_and_status_exit_codes(self):
        for command, target in [('check-layered', 'image_localizer.layered.check_layered'),
                                ('check-job', 'image_localizer.jobs.check_job')]:
            if command == 'check-job':
                self.job_fixture()
            for status, expected_code in [('pending', 2), ('fail', 1), ('pass', 0)]:
                with self.subTest(command=command, status=status):
                    result = {'status': status, 'user_acceptance': 'not_recorded',
                              'reasons': ['synthetic executed-check fixture']}
                    before = set(self.records())
                    with patch(target, return_value=result) as checker:
                        code, stdout, stderr = self.invoke_cli(command)
                    checker.assert_called_once_with(self.output)
                    self.assertEqual(code, expected_code)
                    self.assertEqual(stdout, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
                    self.assertEqual(stderr, '')
                    created = set(self.records()) - before
                    self.assertEqual(len(created), 1)
                    record = self.read_json(created.pop())
                    self.assertEqual(record['result'], result)
                    self.assertEqual(record['command'], command)
                    self.assertTrue(record['snapshot_stable'])
                    self.assertTrue(check_records.record_is_current(record))

    def test_repeated_checks_are_append_only_and_do_not_bind_their_journal(self):
        before = {str(path): path.read_bytes() for path in self.output.iterdir()}
        self.record()
        first_path = self.records()[0]
        first_bytes = first_path.read_bytes()
        first = self.read_json(first_path)
        self.record()
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertTrue(check_records.record_is_current(first))
        for path, content in before.items():
            self.assertEqual(Path(path).read_bytes(), content)
        self.assertFalse(any('gate-checks' in path for path in first['dependencies']['files']))

    def test_missing_review_then_creation_and_note_edit_invalidate_old_records(self):
        _, missing = self.record()
        review = self.output / 'review.json'
        self.assertIsNone(missing['dependencies']['files'][str(review)])
        self.write_json(review, {'notes': 'first actual observation'})
        self.assertFalse(check_records.record_is_current(missing))
        _, present = self.record()
        self.assertTrue(check_records.record_is_current(present))
        self.write_json(review, {'notes': 'changed observation; same output bytes'})
        self.assertFalse(check_records.record_is_current(present))

    def test_artifact_input_and_receipt_changes_invalidate_record(self):
        for path in (self.input, self.output / 'translated.png', self.output / 'receipt.json'):
            with self.subTest(dependency=path.name):
                original = path.read_bytes()
                _, record = self.record()
                path.write_bytes(original + b'changed')
                self.assertFalse(check_records.record_is_current(record))
                path.write_bytes(original)
                self.assertTrue(check_records.record_is_current(record))

    def test_mutation_during_check_never_produces_current_pass(self):
        def mutate(_):
            self.input.write_bytes(b'changed during actual checker execution')
            return {'status': 'pass'}

        returned, record = self.record(check=mutate)
        self.assertEqual(returned, {'status': 'pass'})
        self.assertEqual(record['result'], returned)
        self.assertFalse(record['snapshot_stable'])
        self.assertFalse(check_records.record_is_current(record))

    def test_missing_input_appearance_invalidates_historical_observation(self):
        self.input.unlink()
        _, record = self.record(check=lambda _: {'status': 'fail', 'reasons': ['missing input']})
        self.assertEqual(record['result']['status'], 'fail')
        self.assertIsNone(record['dependencies']['files'][str(self.input)])
        self.input.write_bytes(b'restored dependency')
        self.assertFalse(check_records.record_is_current(record))

    def test_job_binds_both_languages_and_prepared_files_but_not_origins(self):
        prepared, origin = self.job_fixture()
        _, record = self.record('check-job')
        files = record['dependencies']['files']
        expected = [self.output / 'job-render.json', prepared / 'bundle.json',
                    prepared / 'job.json', prepared / 'background-review.json']
        for language in ('en', 'fr'):
            expected.extend(self.output / language / name
                            for name in ('receipt.json', 'review.json', 'translated.png', 'plan.json'))
        for path in expected:
            self.assertIn(str(path), files)
        self.assertNotIn(str(origin), files)
        origin.unlink()
        self.assertTrue(check_records.record_is_current(record))
        for path in (prepared / 'job.json', prepared / 'bundle.json',
                     prepared / 'background-review.json', self.output / 'fr' / 'translated.png'):
            with self.subTest(dependency=str(path)):
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                self.assertFalse(check_records.record_is_current(record))
                path.write_bytes(original)
        self.write_json(self.output / 'fr' / 'review.json', {'notes': 'new French observation'})
        self.assertFalse(check_records.record_is_current(record))

    def test_dependency_budgets_never_claim_complete_current_evidence(self):
        for limit, value in [('MAX_FILES', 1), ('MAX_BYTES', 1), ('MAX_JSON', 1)]:
            with self.subTest(budget=limit), patch.object(check_records, limit, value):
                _, record = self.record()
                self.assertFalse(record['dependencies']['complete'])
                self.assertTrue(record['dependencies']['warnings'])
                self.assertFalse(record['snapshot_stable'])
                self.assertFalse(check_records.record_is_current(record))

    def test_escaped_bundle_and_artifact_paths_cannot_be_captured(self):
        prepared, _ = self.job_fixture()
        outside = self.root / 'outside.json'
        self.write_json(outside, {'unrelated': True})
        bundle = self.read_json(prepared / 'bundle.json')
        bundle['files']['../outside.json'] = sha256(outside)
        self.write_json(prepared / 'bundle.json', bundle)
        snapshot = check_records.dependency_snapshot('check-job', self.output)
        self.assertFalse(snapshot['complete'])
        self.assertNotIn(str(outside), snapshot['files'])
        receipt = self.read_json(self.output / 'receipt.json')
        receipt['artifact_sha256']['../outside.json'] = sha256(outside)
        self.write_json(self.output / 'receipt.json', receipt)
        snapshot = check_records.dependency_snapshot('check-layered', self.output)
        self.assertFalse(snapshot['complete'])
        self.assertNotIn(str(outside), snapshot['files'])

    def test_publication_failure_returns_cli_error_without_partial_record(self):
        self.record()
        existing = {path: path.read_bytes() for path in self.records()}
        with patch('image_localizer.layered.check_layered', return_value={'status': 'pass'}), \
                patch.object(check_records.os, 'link', side_effect=OSError('synthetic publish failure')):
            code, stdout, stderr = self.invoke_cli('check-layered')
        self.assertEqual(code, 1)
        self.assertEqual(stdout, '')
        self.assertEqual(json.loads(stderr)['status'], 'error')
        self.assertIn('synthetic publish failure', stderr)
        self.assertEqual(set(self.records()), set(existing))
        self.assertFalse(list((self.output / 'gate-checks').glob('*.tmp')))
        for path, content in existing.items():
            self.assertEqual(path.read_bytes(), content)

    def test_serialization_failure_leaves_no_partial_json_or_temporary_file(self):
        with self.assertRaises(ValueError):
            self.record(check=lambda _: {'status': 'pass', 'invalid_number': float('nan')})
        self.assertEqual(list((self.output / 'gate-checks').iterdir()), [])

    def test_real_layered_core_is_read_only_and_cli_records_pending_then_pass(self):
        source = self.root / 'source.png'
        Image.new('RGB', (20, 15), 'white').save(source)
        plan = self.root / 'plan.json'
        self.write_json(plan, {
            'schema_version': 1, 'source': 'source.png', 'source_sha256': sha256(source),
            'background': {'operations': []}, 'rows': [], 'assets': [], 'protected_regions': [],
            'working_scale': 1, 'view_scale': 1,
        })
        prepared = self.root / 'real-background'
        actual = self.root / 'real-render'
        prepare_layered(plan, prepared)
        render_layered(plan, prepared, actual)
        self.output = actual
        before = {path: path.read_bytes() for path in actual.iterdir()}
        self.assertEqual(check_layered(actual)['status'], 'pending')
        self.assertEqual({path: path.read_bytes() for path in actual.iterdir()}, before)
        code, stdout, _ = self.invoke_cli('check-layered')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)['status'], 'pending')
        pending_path = self.records()[0]
        pending_bytes = pending_path.read_bytes()
        pending = self.read_json(pending_path)
        review = self.read_json(actual / 'review-template.json')
        review.update(reviewer='synthetic fixture, not production approval')
        review['checks'] = {key: True for key in review['checks']}
        review['stage_notes'] = {key: 'Authored fixture observation' for key in review['stage_notes']}
        self.write_json(actual / 'review.json', review)
        self.assertEqual(check_layered(actual)['status'], 'pass')
        code, stdout, stderr = self.invoke_cli('check-layered')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(json.loads(stdout)['status'], 'pass')
        self.assertEqual(len(self.records()), 2)
        self.assertFalse(check_records.record_is_current(pending))
        self.assertEqual(pending_path.read_bytes(), pending_bytes)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
