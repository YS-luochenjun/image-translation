"""CLI dispatch and full receipt -> download -> pending job integration."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import cli, fireplay, fireplay_download

CODE = 'IMG-20261103-001'


class FireplayIntegrationTests(unittest.TestCase):
    def test_discovery_command_returns_reference_query(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['fireplay-discover']), 0)
        result = json.loads(out.getvalue())
        self.assertFalse(result['complete'])
        self.assertEqual(result['host_actions'][0]['arguments']['asset_type'], 'image')

    def test_cli_dispatches_accept_download_and_job(self):
        for name, module, function, suffix in [
            ('fireplay-accept', fireplay, 'accept', []),
            ('fireplay-download', fireplay_download, 'download_source', ['--image', '2']),
            ('fireplay-job', fireplay_download, 'initialize_source_job', ['--image', '2', '--output', 'runs/test'])]:
            with self.subTest(command=name), patch.object(module, function, return_value={'state': 'test'}) as operation:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main([name, CODE, *suffix]), 0)
                self.assertEqual(operation.call_args.args[0], CODE)
                if suffix:
                    self.assertEqual(operation.call_args.args[1], 2)

    def test_cli_invalid_receipt_has_controlled_error(self):
        with patch.object(fireplay, 'accept', side_effect=ValueError('missing receipt')):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(cli.main(['fireplay-accept', CODE]), 1)
            self.assertEqual(json.loads(err.getvalue())['status'], 'error')

    def test_receipt_to_verified_original_and_pending_multilingual_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = {'id': '4237002a-f29b-4dc5-b1a1-778f76a41994', 'code': CODE,
                   'updatedAt': '2026-11-03T01:00:00Z', 'status': 'pending', 'priority': 'P0',
                   'assetType': 'image', 'title': 'Translate image',
                   'description': 'Translate the poster. https://images.example.test/poster.png',
                   'refText': '', 'locales': ['en-US', 'ja', 'Japanese']}
            receipt = {'items': [row], 'has_more': False, 'next_cursor': None}
            fireplay.register(CODE, {'summary': receipt, 'brief': deepcopy(receipt)}, root)
            fireplay.accept(CODE, root)
            image = io.BytesIO()
            Image.new('RGB', (32, 24), 'green').save(image, format='PNG')

            class Response(io.BytesIO):
                status = 200
                headers = {}

            with patch.object(fireplay_download, '_public_addresses', return_value=['93.184.216.34']), \
                 patch.object(fireplay_download, '_open_url', side_effect=lambda *args: Response(image.getvalue())) as network:
                result = fireplay_download.initialize_source_job(CODE, 1, root / 'job', root)
                fireplay_download.download_source(CODE, 1, root)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(result['languages'], ['en-US', 'ja'])
            state = fireplay.status(CODE, root)
            self.assertFalse(state['platform_claimed'])
            self.assertEqual(state['downloads']['1']['bytes'], len(image.getvalue()))
            self.assertEqual(state['downloads']['1']['width'], 32)
            manifest = json.loads((root / 'job/languages/ja/manifest.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['analysis']['complete'])
            self.assertTrue((root / 'job/fireplay-source.json').is_file())

    def test_language_codes_keep_supported_regions_and_unknowns_blocked(self):
        self.assertEqual(fireplay._locale('zh-Hant'), 'zh-Hant')
        self.assertEqual(fireplay._locale('en_US'), 'en-US')
        self.assertEqual(fireplay._locale('th'), 'th')
        self.assertIsNone(fireplay._locale('../ja'))
        self.assertIsNone(fireplay._locale('Klingon'))


if __name__ == '__main__':
    unittest.main()
