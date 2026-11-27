"""Verify recovery attaches proof to the original operation and never retries PUT."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.fireplay_upload_reconcile import reconcile_upload


class UploadReconcileTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.root = Path(temporary)
        self.code = 'TEST-20261126-001'
        self.folder = self.root / 'work' / 'fireplay-production' / self.code
        self.folder.mkdir(parents=True)
        self.journal = self.folder / 'publication-journal.jsonl'
        self.operation = str(uuid4())
        self.bytes = b'original reviewed image'
        self.digest = hashlib.sha256(self.bytes).hexdigest()
        self.pending = {
            'operation_id': self.operation, 'code': self.code, 'requirement_id': str(uuid4()),
            'tool': 'material_r2_presign_upload', 'state': 'unknown',
            'outputs': [{'sha256': self.digest, 'path': 'reviewed.png'}],
            'put_bytes': len(self.bytes), 'put_started_at': '2026-11-26T12:00:00Z',
            'put_target': {'resourceId': str(uuid4()), 'publicUrl': 'https://example.invalid/result.png'},
        }
        self.journal.write_text(json.dumps(self.pending) + '\n', encoding='utf-8')
        self.fetch = self.enterContext(patch('image_localizer.fireplay_upload_reconcile.transfer_public_bytes'))
        self.fetch.side_effect = self.stream

    def stream(self, url, consume, *, max_bytes):
        self.assertEqual(url, self.pending['put_target']['publicUrl'])
        self.assertEqual(max_bytes, len(self.bytes))
        consume(self.bytes)
        return len(self.bytes)

    def test_recovers_exact_object_using_same_operation(self):
        result = reconcile_upload(self.code, self.operation, self.root)
        self.assertEqual(result['operation_id'], self.operation)
        receipt = Path(result['receipt']).read_bytes()
        self.assertEqual(json.loads(receipt)['response']['transfer']['method'], 'GET')
        states = [json.loads(line) for line in self.journal.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([entry['state'] for entry in states], ['unknown', 'received'])
        self.assertEqual(states[1]['receipt_sha256'], hashlib.sha256(receipt).hexdigest())

    def test_keeps_operation_unknown_when_bytes_differ(self):
        self.bytes = b'wrong reviewed image'
        self.pending['put_bytes'] = len(self.bytes)
        self.journal.write_text(json.dumps(self.pending) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            reconcile_upload(self.code, self.operation, self.root)
        self.assertEqual(len(self.journal.read_text(encoding='utf-8').splitlines()), 1)
        self.assertFalse(list(self.folder.glob('publication-*.json')))
