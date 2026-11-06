"""Exercise public CLI outputs without external connectors or publication."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_localizer.fireplay import _locale

ROOT = Path(__file__).resolve().parents[1]


class AutomationCliTests(unittest.TestCase):
    def test_empty_work_package_has_actionable_next_step_and_no_publish_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            Image.new('RGB', (300, 250), 'white').save(base / 'source.png')
            (base / 'sizes.json').write_text(json.dumps({'asset_type': 'button', 'sources': [
                {'id': 's1', 'path': 'source.png', 'languages': ['ja']}]}), encoding='utf-8')
            state = {'requirement': {'id': '01a1ab5e-81f0-7b33-b7c8-a2d958e79b36', 'code': 'TEST-001',
                                     'asset_type': 'button'}, 'size_spec': 'sizes.json'}
            (base / 'state.json').write_text(json.dumps(state), encoding='utf-8')
            before = sorted(path.name for path in base.iterdir())
            for command in ('delivery-next', 'delivery-gate'):
                with self.subTest(command=command):
                    completed = subprocess.run([sys.executable, str(ROOT / 'bin/it.py'), command,
                                                str(base / 'state.json')], capture_output=True, text=True, check=True)
                    result = json.loads(completed.stdout)
                    if command == 'delivery-next':
                        self.assertFalse(result['complete'])
                        self.assertEqual(2, result['expected_members'])
                        self.assertEqual({'make'}, {row['action'] for row in result['actions']})
                    else:
                        self.assertFalse(result['passed'])
                        self.assertFalse(result['requirement_complete'])
                        self.assertEqual([], result['outputs'])
            self.assertEqual(before, sorted(path.name for path in base.iterdir()))

    def test_local_business_locale_aliases_preserve_canonical_dedup(self):
        for raw, canonical in [('jp', 'ja'), ('kr', 'ko'), ('in', 'id'), ('tw', 'zh-TW'), ('vn', 'vi')]:
            with self.subTest(raw=raw):
                self.assertEqual(canonical, _locale(raw))
        self.assertEqual(1, len({_locale('jp'), _locale('ja')}))
        for raw in ('ejn', 'miss', '翻译剩余语言'):
            with self.subTest(raw=raw):
                self.assertIsNone(_locale(raw))


if __name__ == '__main__':
    unittest.main()
