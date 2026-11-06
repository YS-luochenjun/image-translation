"""Network-free contract tests for accepted Fireplay source downloads."""
from copy import deepcopy
import hashlib
import io
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer import fireplay_download as download
from image_localizer.pipeline import read_json

OPEN_URL = download._open_url


def picture(fmt='PNG', **kwargs):
    stream = io.BytesIO()
    Image.new('RGB', (12, 9), 'red').save(stream, format=fmt, **kwargs)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, body=b'', status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'state': 'accepted', 'content_version': 'v1', 'targets': ['ja', 'fr'],
                       'sources': [{'index': 1, 'url': 'https://images.example/source?signature=secret'}],
                       'downloads': {}}
        self.folder = self.root / 'state/fireplay/REQ-1'
        self.data = picture()
        self.network = self.enterContext(patch.object(download, '_open_url', side_effect=lambda *args: Response(self.data)))
        self.enterContext(patch.object(download.socket, 'getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]))
        self.enterContext(patch.object(download.fireplay, 'load_record', side_effect=lambda *args: deepcopy(self.record)))
        self.enterContext(patch.object(download.fireplay, 'request_folder', return_value=self.folder))
        self.save = self.enterContext(patch.object(download.fireplay, 'record_download', side_effect=self.record_download))

    def record_download(self, code, index, entry, root):
        self.record['downloads'][str(index)] = deepcopy(entry)

    def run_download(self):
        return download.download_source('REQ-1', 1, self.root)

    def assert_clean(self):
        self.assertEqual(list(self.folder.glob('sources/*.tmp')), [])
        self.assertEqual(list(self.folder.glob('sources/.*.tmp')), [])

    def test_opener_disables_proxies_and_pins_validated_ip(self):
        from unittest.mock import Mock
        opener = Mock()
        opener.open.return_value = Response(self.data)
        with patch.object(download.request, 'build_opener', return_value=opener) as build:
            OPEN_URL('http://images.example/file?signature=secret', ['93.184.216.34'])
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        req = opener.open.call_args.args[0]
        self.assertNotIn('Authorization', req.headers)
        self.assertNotIn('Cookie', req.headers)
        handler = next(item for item in handlers if type(item).__name__ == 'HTTP')
        captured = {}
        def capture(factory, req):
            captured['connection'] = factory('images.example', timeout=download.TIMEOUT)
        with patch.object(handler, 'do_open', side_effect=capture):
            handler.http_open(req)
        with patch.object(download.socket, 'create_connection') as connect:
            captured['connection'].connect()
        connect.assert_called_once_with(('93.184.216.34', 80), download.TIMEOUT)

    def test_redirect_rejects_embedded_credentials(self):
        self.network.side_effect = [Response(status=302, headers={
            'Location': 'https://user:secret@images.example/other'})]
        with self.assertRaisesRegex(ValueError, 'userinfo') as caught:
            self.run_download()
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(self.network.call_count, 1)
        self.assert_clean()

    def test_actual_formats_ignore_suffix_and_content_type(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(fmt=fmt):
                self.record['downloads'] = {}
                self.data = picture(fmt)
                entry = self.run_download()
                self.assertEqual(entry['format'], fmt)
                self.assertEqual((entry['width'], entry['height']), (12, 9))
                self.assertEqual(entry['sha256'], hashlib.sha256(self.data).hexdigest())
                self.assertEqual(entry['bytes'], len(self.data))
                self.assertTrue(Path(entry['local_path']).is_relative_to(self.folder))
                self.assertNotIn('secret', str(entry))
                self.assert_clean()

    def test_policy_rejects_unsafe_urls(self):
        for url in ('file:///x', 'ftp://images.example/x', 'https://user:pass@images.example/x',
                    'https://images.example/x#part', 'https://images.example/x#', 'https://images.example/\nx',
                    'http://images.example:invalid/x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                download._public_addresses(url)
        self.network.assert_not_called()

    def test_policy_rejects_any_nonpublic_dns_answer(self):
        for ip in ('127.0.0.1', '10.1.2.3', '169.254.169.254', '0.0.0.0', '192.0.2.1',
                   '224.0.0.1', '::1', 'fc00::1', 'fe80::1', '::ffff:127.0.0.1'):
            with self.subTest(ip=ip), patch.object(download.socket, 'getaddrinfo', return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]), self.assertRaises(ValueError):
                self.run_download()
            self.assert_clean()
        self.network.assert_not_called()

    def test_redirect_is_revalidated(self):
        self.network.side_effect = [Response(status=302, headers={'Location': 'http://private.example/x'})]
        def lookup(host, *args, **kwargs):
            ip = '127.0.0.1' if host == 'private.example' else '93.184.216.34'
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]
        with patch.object(download.socket, 'getaddrinfo', side_effect=lookup), self.assertRaises(ValueError):
            self.run_download()
        self.assertEqual(self.network.call_count, 1)
        self.assert_clean()

    def test_redirect_chain_and_limit(self):
        self.network.side_effect = [Response(status=302, headers={'Location': '/other'}), Response(self.data)]
        self.run_download()
        self.assertEqual(self.network.call_args_list[1].args[0], 'https://images.example/other')
        self.record['downloads'] = {}
        self.network.side_effect = lambda *args: Response(status=302, headers={'Location': '/again'})
        with self.assertRaisesRegex(ValueError, 'redirect limit'):
            self.run_download()
        self.assert_clean()

    def test_invalid_and_animated_images_cleanup(self):
        animated = io.BytesIO()
        Image.new('RGB', (12, 9), 'red').save(animated, format='PNG', save_all=True,
                                           append_images=[Image.new('RGB', (12, 9), 'blue')])
        for body in (b'<html>not image</html>', b'', self.data[:35], picture('GIF'), animated.getvalue()):
            with self.subTest(size=len(body)):
                self.data = body
                with self.assertRaises(ValueError):
                    self.run_download()
                self.assert_clean()
        self.save.assert_not_called()

    def test_declared_and_streaming_byte_limits(self):
        with patch.object(download, 'MAX_BYTES', len(self.data) - 1), self.assertRaisesRegex(ValueError, 'byte limit'):
            self.run_download()
        self.network.side_effect = lambda *args: Response(self.data, headers={'Content-Length': str(download.MAX_BYTES + 1)})
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            self.run_download()
        self.assert_clean()

    def test_mismatch_hash_size_dimensions(self):
        for field, value in (('sha256', '0' * 64), ('bytes', 999), ('width', 13), ('height', 10)):
            with self.subTest(field=field):
                self.record['sources'][0] = {'index': 1, 'url': 'https://images.example/x', field: value}
                with self.assertRaisesRegex(ValueError, field + ' mismatch'):
                    self.run_download()
                self.assert_clean()
        self.save.assert_not_called()

    def test_pixel_and_decompression_limits(self):
        with patch.object(download, 'MAX_PIXELS', 100), self.assertRaisesRegex(ValueError, 'pixel limit'):
            self.run_download()
        with patch.object(Image, 'MAX_IMAGE_PIXELS', 100), self.assertRaisesRegex(ValueError, 'safety limits'):
            self.run_download()
        self.assert_clean()

    def test_transport_errors_are_redacted_and_cleanup(self):
        self.network.side_effect = URLError('https://images.example/?signature=secret')
        with self.assertRaises(ValueError) as caught:
            self.run_download()
        self.assertNotIn('secret', str(caught.exception))
        self.assert_clean()

    def test_response_length_and_encoding(self):
        for headers in ({'Content-Length': 'nonsense'}, {'Content-Length': str(len(self.data) + 1)},
                        {'Content-Encoding': 'gzip'}):
            with self.subTest(headers=headers):
                self.network.side_effect = lambda *args: Response(self.data, headers=headers)
                with self.assertRaises(ValueError):
                    self.run_download()
                self.assert_clean()

    def test_reuse_checks_hash_and_never_replaces_tampered_source(self):
        entry = self.run_download()
        self.network.reset_mock()
        self.assertEqual(self.run_download(), entry)
        self.network.assert_not_called()
        path = Path(entry['local_path'])
        stream = io.BytesIO()
        Image.new('RGB', (12, 9), 'blue').save(stream, format='PNG')
        path.write_bytes(stream.getvalue())
        with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
            self.run_download()
        self.assertEqual(path.read_bytes(), stream.getvalue())
        self.network.assert_not_called()

    def test_prior_file_survives_receipt_failure_and_retry(self):
        self.save.side_effect = ValueError('receipt failure')
        with self.assertRaises(ValueError):
            self.run_download()
        files = list((self.folder / 'sources').glob('source-*'))
        self.assertEqual(len(files), 1)
        original = files[0].read_bytes()
        self.save.side_effect = self.record_download
        self.run_download()
        self.assertEqual(files[0].read_bytes(), original)
        self.assert_clean()

    def test_state_index_and_path_constraints(self):
        for state in ('pending', 'ready', 'cancelled'):
            self.record['state'] = state
            with self.assertRaisesRegex(ValueError, 'accepted'):
                self.run_download()
        self.record['state'] = 'accepted'
        for index in (0, -1, True, '../x', 2):
            with self.assertRaises(ValueError):
                download.download_source('REQ-1', index, self.root)
        self.network.assert_not_called()

    def test_initialize_unreviewed_targets_and_provenance(self):
        output = self.root / 'job'
        result = download.initialize_source_job('REQ-1', 1, output, self.root)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['languages'], ['ja', 'fr'])
        origin = read_json(output / 'fireplay-source.json')
        self.assertEqual(origin['source']['sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(origin['content_version'], 'v1')
        self.assertNotIn('secret', str(origin))
        for target in ('ja', 'fr'):
            manifest = read_json(output / f'languages/{target}/manifest.json')
            self.assertFalse(manifest['analysis']['complete'])
            self.assertEqual(manifest['regions'], [])
        self.network.reset_mock()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            download.initialize_source_job('REQ-1', 1, output, self.root)
        self.network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
