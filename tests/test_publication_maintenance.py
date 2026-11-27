import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from image_localizer.legacy_storage import compact_legacy_fonts
from image_localizer.publication_timing import publication_timing


class PublicationMaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'project'
        self.production = self.root / 'work/fireplay-production'
        self.folder = self.production / 'TEST-20261123-001'
        self.folder.mkdir(parents=True)

    def test_closed_font_compaction_preserves_paths_content_metadata_and_open_demands(self):
        eligible = self.folder / 'publication-journal.jsonl'
        eligible.write_text('{"operation_id":"known","state":"received"}\n')
        pending = self.production / 'TEST-20261123-002'
        pending.mkdir()
        (pending / eligible.name).write_text('{"operation_id":"unknown","state":"unknown"}\n')
        assignments = self.root / 'work/fireplay-monitor/assignments.json'
        assignments.parent.mkdir()
        assignments.write_text(json.dumps({'requirements': {directory.name: {
            'code': directory.name, 'state': 'verified_complete', 'delivery_verified_by_controller': True,
        } for directory in (self.folder, pending)}}))
        paths = [self.folder / 'first.ttf', self.folder / 'second.ttf', pending / 'third.ttf']
        for path in paths:
            path.write_bytes(b'font data')
        paths[1].chmod(0o640)
        os.utime(paths[1], (1_700_000_000, 1_700_000_000))
        preview = compact_legacy_fonts(self.root)
        self.assertEqual((1, 1, 9), tuple(preview[key] for key in
                                         ('eligible_demands', 'duplicate_fonts', 'duplicate_logical_bytes')))
        if sys.platform != 'darwin':
            return
        for path in paths[:2]:
            subprocess.run(('/usr/bin/xattr', '-w', 'test.local.font', path.name, str(path)), check=True)
        original = [path.stat() for path in paths]
        receipt = self.base / 'receipt.jsonl'
        protected = (assignments.read_bytes(), eligible.read_bytes())
        result = compact_legacy_fonts(self.root, receipt)
        if not result['cloned_fonts'] and any('Filesystem did not create' in line for line in receipt.read_text().splitlines()):
            self.skipTest('Temporary filesystem does not support cloning')
        self.assertEqual(1, result['cloned_fonts'])
        afterward = [path.stat() for path in paths]
        self.assertNotEqual(original[1].st_ino, afterward[1].st_ino)
        self.assertEqual(3, len({item.st_ino for item in afterward}))
        self.assertEqual(original[2].st_ino, afterward[2].st_ino)
        self.assertEqual(original[1].st_mode, afterward[1].st_mode)
        self.assertEqual(original[1].st_mtime_ns, afterward[1].st_mtime_ns)
        self.assertEqual(b'second.ttf\n', subprocess.check_output(
            ('/usr/bin/xattr', '-p', 'test.local.font', str(paths[1]))))
        self.assertEqual([b'font data'] * 3, [path.read_bytes() for path in paths])
        self.assertEqual(protected, (assignments.read_bytes(), eligible.read_bytes()))
        self.assertEqual('summary', json.loads(receipt.read_text().splitlines()[-1])['event'])
        with self.assertRaises(FileExistsError):
            compact_legacy_fonts(self.root, receipt)

    def test_publication_timing_keeps_parallel_batches_and_unverified_outcomes_separate(self):
        def stamp(seconds):
            return f'2026-11-26T08:00:{seconds:02d}Z'

        base64 = {'tool': 'material_r2_upload_from_bytes', 'operation_id': str(uuid4())}
        presign = {'tool': 'material_r2_presign_upload', 'requirement_id': str(uuid4()), 'batch_id': str(uuid4()),
                   'presign_timing': {'started_at': stamp(2), 'finished_at': stamp(4)}}
        response = {'publicUrl': 'https://example.invalid/image.png', 'resourceId': str(uuid4())}
        entries = [{**base64, 'state': 'unknown', 'at': stamp(0)},
                   {**base64, 'state': 'received', 'at': stamp(3), 'response': {'isError': True}}]
        for start, end in ((5, 9), (6, 10)):
            operation = str(uuid4())
            received = {**presign, 'operation_id': operation, 'state': 'received',
                        'response': {**response, 'transfer': {'method': 'PUT',
                                                             'started_at': stamp(start), 'finished_at': stamp(end)}}}
            receipt = self.folder / f'publication-{operation}.json'
            receipt.write_text(json.dumps(received))
            entries.append({**{key: value for key, value in received.items() if key != 'response'},
                            'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()})
        entries.append({**presign, 'batch_id': str(uuid4()), 'operation_id': str(uuid4()), 'state': 'unknown'})
        entries.append({**presign, 'batch_id': str(uuid4()), 'operation_id': str(uuid4()), 'state': 'received',
                        'receipt_sha256': 'invalid'})
        (self.folder / 'publication-journal.jsonl').write_text(''.join(json.dumps(entry) + '\n' for entry in entries))
        result = publication_timing(self.root)
        self.assertEqual({'failed': 1}, result['base64']['outcomes'])
        self.assertEqual(3, result['base64']['intent_to_receipt']['median_seconds'])
        batch = result['presigned']
        self.assertEqual({'uploaded': 1, 'unknown': 2}, batch['batch_outcomes'])
        self.assertEqual({'uploaded': 2, 'unknown': 1, 'invalid_receipt': 1}, batch['member_outcomes'])
        self.assertEqual(2, batch['presign_call']['median_seconds'])
        self.assertEqual(2, batch['put_attempt']['samples'])
        self.assertEqual(5, batch['fully_timed_batch_put_span']['median_seconds'])
