"""Synthetic integration tests for measured production, never paid image calls."""
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import production_timing
from image_localizer.jobs import render_job, verify_bundle
from image_localizer.layered import prepare_layered, render_layered, check_layered
from image_localizer.pipeline import render, read_json, sha256
from image_localizer.review_desk import ReviewDesk
import test_jobs
import test_layered


class ProductionTimingTests(unittest.TestCase):
    def job_fixture(self):
        fixture = test_jobs.JobTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepared = fixture.root / 'runs' / 'prepared'
        fixture.output = fixture.root / 'runs' / 'native'
        return fixture

    def layered_fixture(self):
        fixture = test_layered.LayeredTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_native_render_records_languages_and_monitor_timing(self):
        fixture = self.job_fixture()
        fixture.render()
        record = read_json(fixture.output / 'render-timing.json')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['duration_scope'], 'render')
        self.assertEqual(record['artifact_sha256']['en/translated.png'], sha256(fixture.output / 'en/translated.png'))
        self.assertGreaterEqual(record['duration_seconds'], 0)
        self.assertIsNotNone(datetime.fromisoformat(record['started_at']).tzinfo)
        tasks = ReviewDesk(fixture.root).monitor.snapshot(fresh=True)['tasks']
        language = next(task for task in tasks if task['id'] == 'runs/native/en')
        self.assertEqual(language['timing_status'], 'collected')
        self.assertEqual(language['duration_scope'], 'render')
        self.assertIsNotNone(language['duration_seconds'])
        self.assertIsNone(language['agent_translation_duration_seconds'])
        detail = ReviewDesk(fixture.root).detail('runs/native/en')
        self.assertEqual(detail['duration_seconds'], language['duration_seconds'])
        self.assertEqual(detail['timing_status'], 'collected')
        self.assertEqual(check_layered(fixture.output / 'en')['status'], 'pending')

    def test_native_failure_has_error_evidence_without_success_or_output(self):
        fixture = self.job_fixture()
        fixture.prepare()
        with self.assertRaisesRegex(ValueError, 'Background review is missing'):
            render_job(fixture.prepared, fixture.output)
        self.assertFalse(fixture.output.exists())
        failures = list((fixture.output.parent / '.render-timings').glob('*.json'))
        self.assertEqual(len(failures), 1)
        record = read_json(failures[0])
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(record['output_produced'])
        self.assertEqual(record['artifact_sha256'], {})
        self.assertEqual(record['partial_artifact_sha256'], {})
        self.assertIn('Background review is missing', record['error']['message'])
        self.assertGreaterEqual(record['duration_seconds'], 0)

    def test_native_midway_failure_records_only_partial_output_evidence(self):
        fixture = self.job_fixture()
        fixture.prepare()
        fixture.approve_background_fixture()
        from image_localizer import jobs
        actual = jobs.render_layered

        def interrupted(plan, background, output):
            if output.name == 'fr':
                raise RuntimeError('synthetic second language failure')
            return actual(plan, background, output)

        with patch.object(jobs, 'render_layered', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'synthetic second language'):
                render_job(fixture.prepared, fixture.output)
        self.assertFalse((fixture.output / 'job-render.json').exists())
        self.assertFalse((fixture.output / 'render-timing.json').exists())
        record = read_json(next((fixture.output.parent / '.render-timings').glob('*.json')))
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['artifact_sha256'], {})
        self.assertEqual(record['partial_artifact_sha256'], {
            'en/translated.png': sha256(fixture.output / 'en/translated.png')})

    def test_basic_midway_failure_does_not_claim_partial_image_success(self):
        fixture = self.job_fixture()
        run = fixture.path.parent / 'languages/en'

        def interrupted(source, manifest, output, background):
            output.mkdir(parents=True)
            (output / 'translated.png').write_bytes(b'partial synthetic output')
            raise RuntimeError('synthetic render interruption')

        with patch('image_localizer.rendering.render_image', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'synthetic render interruption'):
                render(run)
        record = read_json(next((run / 'renders/.render-timings').glob('*.json')))
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(record['output_produced'])
        self.assertEqual(record['artifact_sha256'], {})
        self.assertIn('translated.png', record['partial_artifact_sha256'])
        self.assertFalse((run / 'current.json').exists())

    def test_timing_sidecar_changes_do_not_change_bundle_or_quality_gates(self):
        fixture = self.job_fixture()
        fixture.render()
        bundle = sha256(fixture.prepared / 'bundle.json')
        background_timing = fixture.prepared / 'background/render-timing.json'
        background_timing.write_text('{}', encoding='utf-8')
        verify_bundle(fixture.prepared)
        self.assertEqual(bundle, sha256(fixture.prepared / 'bundle.json'))
        receipt = sha256(fixture.output / 'en/receipt.json')
        (fixture.output / 'en/render-timing.json').write_text('{}', encoding='utf-8')
        self.assertEqual(check_layered(fixture.output / 'en')['status'], 'pending')
        self.assertEqual(receipt, sha256(fixture.output / 'en/receipt.json'))

    def test_basic_render_uses_monotonic_seconds_and_bound_monitor_evidence(self):
        fixture = self.job_fixture()
        run = fixture.path.parent / 'languages/en'
        with patch.object(production_timing, 'perf_counter', side_effect=[100.0, 102.5]):
            result = render(run)
        output = Path(result['output_dir'])
        record = read_json(output / 'render-timing.json')
        self.assertEqual(record['duration_seconds'], 2.5)
        # Place the authored run under the monitor's documented roots.
        from image_localizer.project_monitor import EvidenceReader, _render_timing
        reader = EvidenceReader(fixture.root)
        hashes = {'output_sha256': sha256(output / 'translated.png')}
        fields = _render_timing(reader, output, run / 'manifest.json', None, hashes)
        self.assertEqual(fields['duration_seconds'], 2.5)
        (output / 'translated.png').write_bytes(b'changed candidate')
        hashes['output_sha256'] = sha256(output / 'translated.png')
        fields = _render_timing(EvidenceReader(fixture.root), output, run / 'manifest.json', None, hashes)
        self.assertIsNone(fields['duration_seconds'])
        self.assertEqual(fields['timing_status'], 'stale_or_invalid')

    def test_historical_render_is_explicitly_uncollected(self):
        fixture = self.job_fixture()
        fixture.render()
        (fixture.output / 'en/render-timing.json').unlink()
        task = next(task for task in ReviewDesk(fixture.root).monitor.snapshot(fresh=True)['tasks']
                    if task['id'] == 'runs/native/en')
        for field in ('duration_seconds', 'duration_scope', 'timing_started_at', 'timing_finished_at'):
            self.assertIsNone(task[field])
        self.assertEqual(task['timing_status'], 'not_collected')

    def test_layered_background_has_own_scope_and_failed_rerun_preserves_receipts(self):
        fixture = self.layered_fixture()
        fixture.render()
        background = read_json(fixture.prepared / 'render-timing.json')
        self.assertEqual(background['duration_scope'], 'background')
        receipt = sha256(fixture.output / 'receipt.json')
        timing = sha256(fixture.output / 'render-timing.json')
        with self.assertRaisesRegex(ValueError, 'Output already exists'):
            render_layered(fixture.plan_path, fixture.prepared, fixture.output)
        self.assertEqual(receipt, sha256(fixture.output / 'receipt.json'))
        self.assertEqual(timing, sha256(fixture.output / 'render-timing.json'))
        self.assertEqual(check_layered(fixture.output)['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
