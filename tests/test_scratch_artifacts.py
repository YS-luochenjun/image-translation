import shutil
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.pipeline import read_json, write_json
from image_localizer.scratch_artifacts import scratch_preview, stage_model_output


class ScratchArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.code = 'KEY-20261127-022'
        self.source = self.root / 'host-output.png'
        Image.new('RGB', (100, 70), 'red').save(self.source)

    def test_model_bytes_are_shared_while_receipts_preserve_each_operation(self):
        alternate = self.root / 'other-output.jpg'
        shutil.copyfile(self.source, alternate)
        results = [stage_model_output(self.root, self.code, image, provider='image_gen.imagegen',
                                      operation=operation, members=members)
                   for image, operation, members in ((self.source, 'call-1', ['source-2', 'source-1']),
                                                     (alternate, 'call-2', ['source-3']))]
        first, second = results
        self.assertEqual(second['mode'], 'reused')
        self.assertEqual(first['path'], second['path'])
        self.assertTrue(first['path'].endswith('.png'))
        self.assertEqual(len(list(Path(first['path']).parent.glob('*.png'))), 1)
        self.assertEqual(read_json(Path(first['receipt']))['members'], ['source-1', 'source-2'])
        self.assertEqual(read_json(Path(second['receipt']))['operation'], 'call-2')
        Path(first['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'destination differs'):
            stage_model_output(self.root, self.code, self.source, provider='image_gen.imagegen',
                               operation='call-1', members=['source-2', 'source-1'])

    def test_preview_recipe_and_input_bytes_control_reuse(self):
        alternate = self.root / 'same.png'
        shutil.copyfile(self.source, alternate)
        spec = self.root / 'preview.json'
        items = [{'path': self.source.name, 'crop': [5, 0, 50, 40], 'label': '01'}]
        write_json(spec, {'items': items})
        generated = scratch_preview(self.root, self.code, spec)
        items[0]['path'] = alternate.name
        write_json(spec, {'items': items})
        reused = scratch_preview(self.root, self.code, spec)
        self.assertEqual(reused['mode'], 'reused')
        self.assertEqual(generated['path'], reused['path'])
        items[0]['crop'][0] = 6
        write_json(spec, {'items': items})
        recropped = scratch_preview(self.root, self.code, spec)
        self.assertNotEqual(generated['path'], recropped['path'])
        Image.new('RGB', (100, 70), 'blue').save(alternate)
        changed = scratch_preview(self.root, self.code, spec)
        self.assertNotEqual(recropped['path'], changed['path'])
        Path(changed['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Cached preview evidence changed'):
            scratch_preview(self.root, self.code, spec)
