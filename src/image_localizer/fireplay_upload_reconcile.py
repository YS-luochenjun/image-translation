"""Verify the exact object from an uncertain PUT without sending another write."""
import hashlib
import json
import os
from datetime import datetime, timezone
from uuid import UUID

from . import fireplay
from .contracts import require
from .fireplay_download import transfer_public_bytes


def reconcile_upload(code, operation_id, root):
    require(str(UUID(operation_id)) == operation_id.lower(), 'Upload operation requires an exact UUID')
    root = root.resolve()
    folder = root / 'work' / 'fireplay-production' / code
    require(fireplay.request_folder(code, root).name == code and folder.is_dir()
            and folder.resolve().is_relative_to(root.resolve()), 'Invalid publication directory')
    journal = folder / 'publication-journal.jsonl'
    with fireplay._lock(folder, 'publication.lock'):
        with journal.open(encoding='utf-8') as stream:
            entries = [json.loads(line) for line in stream if line.strip()]
        matches = [entry for entry in entries if entry['operation_id'] == operation_id]
        require(matches, 'Upload operation is not in the original journal')
        entry = matches[-1]
        target = entry.get('put_target', {})
        outputs = entry.get('outputs', [])
        require(entry['code'] == code and entry['state'] == 'unknown'
                and entry['tool'] == 'material_r2_presign_upload' and len(outputs) == 1
                and entry.get('put_started_at') and isinstance(entry.get('put_bytes'), int)
                and 0 < entry['put_bytes'] <= 50 * 1024 * 1024
                and fireplay._http(target.get('publicUrl'))
                and str(UUID(target['resourceId'])) == target['resourceId'].lower(),
                'Operation is not an uncertain PUT with a recorded resource')
        digest = hashlib.sha256()
        count = transfer_public_bytes(target['publicUrl'], digest.update, max_bytes=entry['put_bytes'])
        require(count == entry['put_bytes'] and digest.hexdigest() == outputs[0]['sha256'],
                'Original R2 object does not match the reviewed file')
        received = {**entry, 'state': 'received', 'response': {**target, 'transfer': {
            'method': 'GET', 'http_status': 200, 'sha256': digest.hexdigest(), 'bytes': count,
        }}}
        receipt_path = folder / f'publication-{operation_id}.json'
        if receipt_path.exists():
            original = json.loads(receipt_path.read_bytes())
            response = original.get('response', {})
            transfer = response.get('transfer', {})
            require(all(original.get(key) == value for key, value in entry.items() if key not in ('state', 'at'))
                    and original.get('state') == 'received'
                    and all(response.get(key) == value for key, value in target.items())
                    and transfer.get('sha256') == digest.hexdigest()
                    and transfer.get('bytes') == count and transfer.get('http_status') in (200, 201, 204),
                    'Existing receipt conflicts with the original PUT and verified R2 bytes')
        else:
            fireplay._write(receipt_path, received, immutable=True)
        recorded = {**entry, 'state': 'received', 'at': datetime.now(timezone.utc).isoformat(),
                    'receipt_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
        with journal.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(recorded, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    return {'operation_id': operation_id, 'status': 'uploaded', 'resource_id': target['resourceId'],
            'receipt': str(receipt_path)}
