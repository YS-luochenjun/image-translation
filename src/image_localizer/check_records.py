"""Persist CLI gate observations without changing or executing visual review."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

MAX_FILES = 1024
MAX_BYTES = 256 * 1024 * 1024
MAX_JSON = 512 * 1024
COMMANDS = {'check-layered', 'check-job'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _plain(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked check dependency is unsupported')
    return path


class _Snapshot:
    def __init__(self):
        self.files, self.warnings, self.used = {}, [], 0

    def warn(self, message):
        if len(self.warnings) < 50:
            self.warnings.append(message)

    def capture(self, path, *, document=False):
        try:
            path = _plain(path)
            key = str(path)
            if key in self.files:
                if document and self.files[key] is not None:
                    return self.document(path)
                return None
            if len(self.files) >= MAX_FILES:
                raise ValueError('Check dependency count budget exceeded')
            self.files[key] = None
            if not path.exists():
                return None  # Absence is a binding, especially an absent review.json.
            size = path.stat().st_size
            if not path.is_file() or self.used + size > MAX_BYTES:
                raise ValueError('Check dependency byte budget exceeded or non-file dependency')
            self.used += size
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            self.files[key] = digest.hexdigest()
            return self.document(path) if document else None
        except (OSError, ValueError, TypeError) as error:
            self.warn(str(error))
            return None

    def document(self, path):
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                raise ValueError('Check JSON budget exceeded')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Check dependency must be a JSON object')
            if hashlib.sha256(raw).hexdigest() != self.files[str(path)]:
                raise ValueError('Check dependency changed while reading')
            return value
        except (OSError, ValueError, RecursionError) as error:
            self.warn(str(error))
            return None

    def local(self, root, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            self.warn('Invalid relative check dependency')
            return None
        path = Path(os.path.abspath(root / relative))
        if not path.is_relative_to(root):
            self.warn('Escaped check dependency')
            return None
        return path

    def layered(self, root):
        receipt = self.capture(root / 'receipt.json', document=True)
        self.capture(root / 'review.json')
        if receipt is None:
            self.warn('Render receipt unavailable')
            return
        for field in ('artifact_sha256', 'input_sha256'):
            values = receipt.get(field)
            if not isinstance(values, dict) or len(values) > MAX_FILES:
                self.warn('Invalid or excessive receipt dependencies')
                continue
            for name in values:
                if field == 'artifact_sha256':
                    path = self.local(root, name)
                elif isinstance(name, str) and Path(name).is_absolute():
                    path = Path(name)
                else:
                    self.warn('Receipt input must use its absolute dependency path')
                    path = None
                if path is not None:
                    self.capture(path)


def dependency_snapshot(command, output):
    """Capture actual gate dependencies; journals and provenance-only origins are excluded."""
    if command not in COMMANDS:
        raise ValueError('Unsupported recorded check')
    root = _plain(output)
    snapshot = _Snapshot()
    if command == 'check-layered':
        snapshot.layered(root)
    else:
        record = snapshot.capture(root / 'job-render.json', document=True)
        if record is None:
            snapshot.warn('Job render record unavailable')
        else:
            prepared_value, languages = record.get('prepared'), record.get('languages')
            if not isinstance(prepared_value, str) or not Path(prepared_value).is_absolute():
                snapshot.warn('Invalid prepared job path')
            else:
                prepared = Path(os.path.abspath(prepared_value))
                bundle = snapshot.capture(prepared / 'bundle.json', document=True)
                snapshot.capture(prepared / 'background-review.json')
                if bundle is not None and isinstance(bundle.get('files'), dict) and len(bundle['files']) <= MAX_FILES:
                    for name in bundle['files']:
                        path = snapshot.local(prepared, name)
                        if path is not None:
                            snapshot.capture(path)
                else:
                    snapshot.warn('Bundle dependency inventory unavailable')
            if not isinstance(languages, list) or len(languages) > 20:
                snapshot.warn('Invalid language inventory')
            else:
                for language in languages:
                    if not isinstance(language, str) or not language or language in ('.', '..') or '/' in language or '\\' in language:
                        snapshot.warn('Invalid language path')
                        continue
                    snapshot.layered(root / language)
    return {'files': snapshot.files, 'complete': not snapshot.warnings, 'warnings': snapshot.warnings}


def record_check(command, output, check):
    """Run the existing read-only checker and append one immutable observation."""
    root = _plain(output)
    started = _now()
    before = dependency_snapshot(command, root)
    result = check(output)
    after = dependency_snapshot(command, root)
    record = {
        'schema_version': 1, 'kind': 'gate.check', 'command': command,
        'node_id': 'quality.layered' if command == 'check-layered' else 'quality.job',
        'output': str(root), 'started_at': started, 'finished_at': _now(),
        'result': result, 'dependencies': after,
        'snapshot_stable': before['complete'] and after['complete'] and before['files'] == after['files'],
        'notice': 'Historical gate observation; not a new visual review or user acceptance. Recheck dependencies before displaying as current.',
    }
    directory = _plain(root / 'gate-checks')
    if not root.is_dir():
        return result
    directory.mkdir(exist_ok=True)
    identifier = uuid.uuid4().hex
    path = directory / (identifier + '.json')
    temporary = directory / ('.' + identifier + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Publish complete bytes exclusively; an existing record can never be overwritten.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def record_is_current(record):
    """Return false for changed/missing/incomplete evidence; never infer session ownership."""
    try:
        if record.get('schema_version') != 1 or record.get('kind') != 'gate.check' or record.get('snapshot_stable') is not True:
            return False
        current = dependency_snapshot(record['command'], Path(record['output']))
        return current['complete'] and current['files'] == record['dependencies']['files']
    except (OSError, ValueError, KeyError, TypeError):
        return False
