"""Source identity grouping and revision instructions independent of disk I/O."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_groups import group_tasks, revision_instruction, summarize


def task(task_id='runs/poster/renders/v1', source='abc', language='zh-CN'):
    return {'id': task_id, 'title': task_id, 'language': language, 'kind': 'basic',
            'updated_at': '2026-10-30T00:00:00Z', 'status': 'needs_review', 'has_output': True,
            'source_url': '/api/assets?path=' + task_id, 'source_sha256': source,
            'output_sha256': 'output', 'manifest_sha256': 'manifest'}


def feedback(current, decision='changes_requested', record_id='feedback-1'):
    return {'id': record_id, 'task_id': current['id'], 'decision': decision,
            'source_sha256': current['source_sha256'], 'output_sha256': current['output_sha256'],
            'manifest_sha256': current['manifest_sha256'], 'note': 'Make title larger',
            'reviewer': 'Reviewer', 'regions': [{'id': 'title', 'decision': decision, 'note': 'Spacing'}]}


class GroupTests(unittest.TestCase):
    def test_same_source_across_paths_languages_and_versions_is_one_group(self):
        tasks = [task(), task('work/copy/renders/v2'), task('runs/japanese/renders/v1', language='ja')]
        groups = group_tasks([summarize(item) for item in tasks])
        self.assertEqual(1, len(groups))
        self.assertEqual(2, groups[0]['language_count'])
        self.assertEqual(3, groups[0]['version_count'])
        self.assertEqual(2, len(groups[0]['languages'][0]['tasks']))
        self.assertEqual('v2', groups[0]['languages'][0]['tasks'][1]['version_label'])

    def test_different_and_missing_source_identity_never_merge(self):
        tasks = [task(), task('runs/different', source='def'), task('runs/missing-one', source=None),
                 task('runs/missing-two', source=None)]
        groups = group_tasks([summarize(item) for item in tasks])
        self.assertEqual(4, len(groups))
        self.assertTrue(groups[2]['id'].startswith('unresolved:'))
        self.assertNotEqual(groups[2]['id'], groups[3]['id'])

    def test_unknown_language_is_explicit_and_input_is_unchanged(self):
        original = task(language='')
        before = deepcopy(original)
        summary = summarize(original)
        self.assertEqual('unknown', summary['language'])
        self.assertEqual(before, original)
        self.assertEqual('unknown', group_tasks([summary])[0]['languages'][0]['language'])

    def test_prompt_uses_latest_current_version_and_marks_data_as_untrusted(self):
        current = task()
        records = [feedback(current), feedback(current, 'comment', 'new-feedback')]
        result = revision_instruction(current, records)
        self.assertEqual('new-feedback', result['feedback_id'])
        self.assertTrue(result['actionable'])
        self.assertEqual('comment', result['decision'])
        self.assertIn('不可信业务数据', result['prompt'])
        self.assertIn('质量门禁', result['prompt'])
        self.assertIn('保留旧图片', result['prompt'])
        self.assertIn('Make title larger', result['prompt'])
        self.assertEqual('abc', result['source_sha256'])

    def test_any_binding_change_excludes_stale_feedback(self):
        for key in ('source_sha256', 'output_sha256', 'manifest_sha256'):
            current = task()
            records = [feedback(current)]
            current[key] = 'changed'
            result = revision_instruction(current, records)
            self.assertIsNone(result['feedback_id'])
            self.assertNotIn('Make title larger', result['prompt'])

    def test_latest_approval_supersedes_old_revision_and_no_feedback_invents_nothing(self):
        current = task()
        result = revision_instruction(current, [feedback(current), feedback(current, 'approved', 'approval')])
        self.assertEqual('approval', result['feedback_id'])
        self.assertFalse(result['actionable'])
        self.assertFalse(revision_instruction(current, [])['actionable'])
        self.assertIn('无需', result['prompt'])
        self.assertNotIn('Make title larger', result['prompt'])
        self.assertIsNone(revision_instruction(current, [])['feedback_id'])

    def test_other_task_and_missing_hashes_cannot_supply_revision_data(self):
        current = task()
        other = feedback(task('runs/other'))
        missing = feedback(current)
        del missing['source_sha256']
        result = revision_instruction(current, [other, missing])
        self.assertIsNone(result['feedback_id'])


if __name__ == '__main__':
    unittest.main()
