"""Keep the shipped bilingual sample independent of author-machine paths."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from image_localizer.jobs import _load_job, prepare_job
from image_localizer.lettering import check_font_coverage
from image_localizer.pipeline import sha256

ROOT = Path(__file__).resolve().parents[1]


class PortableExampleTests(unittest.TestCase):
    def test_sample_is_relative_and_all_actual_glyphs_are_present(self):
        path = ROOT/'examples/trueself-job.json'
        project, languages = _load_job(path)
        self.assertEqual(languages, ['zh-CN', 'ja'])
        for value in [project['source'], *project['background'].values(), *project['support_files']]:
            self.assertFalse(Path(value).is_absolute())
            self.assertTrue((path.parent/value).resolve().is_relative_to(ROOT))
            self.assertTrue((path.parent/value).is_file())
        for target in project['targets']:
            self.assertFalse(Path(target['manifest']).is_absolute())
            for row in target['rows']:
                self.assertFalse(Path(row['font_path']).is_absolute())
                result = check_font_coverage(row['font_path'], row['text'], base_dir=path.parent)
                self.assertEqual(result['status'], 'covered')

    def test_demo_font_cannot_silently_supply_arbitrary_unbundled_text(self):
        with self.assertRaisesRegex(ValueError, 'missing Unicode glyphs'):
            check_font_coverage(ROOT/'examples/assets/fonts/demo-serif-sc.otf', '龘')

    def test_frozen_sample_can_be_reprepared_after_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare_job(ROOT/'examples/trueself-job.json', root/'first')
            shutil.copytree(root/'first', root/'relocated')
            prepare_job(root/'relocated/job.json', root/'second')
            self.assertEqual(sha256(root/'first/background/background.png'),
                             sha256(root/'second/background/background.png'))
            self.assertFalse((root/'second/background-review.json').exists())


if __name__ == '__main__':
    unittest.main()
