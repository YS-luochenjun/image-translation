"""Freeze verified reuse evidence and retire local production media by age."""
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import fireplay
from .contracts import require
from .fireplay_capacity import monitor_directory
from .pipeline import read_json, sha256
from .storage_preview import GROUPS


MEMBER_FIELDS = ('source_id', 'language', 'member_id', 'width', 'height', 'delivery_mode',
                 'material_id', 'resource_id', 'output_sha256')
IDENTITY_FIELDS = ('code', 'requirement_id', 'content_version')
MEDIA = GROUPS['image'] | GROUPS['font'] | {'.zip', '.psd', '.xcf'}
LOCAL_ZONE = ZoneInfo('America/Los_Angeles')
LOCKS = ('.lock', 'delivery.lock', 'publication.lock')


def _safe(root, path):
    require(path.is_relative_to(root) and path.resolve() == path
            and not any(part.is_symlink() for part in (path, *path.parents)),
            'Retention path escapes its workspace or uses a link')
    return path


def _optional(path, default):
    return read_json(path) if path.exists() else default


def _identity(record):
    result = {field: record[field] for field in IDENTITY_FIELDS}
    require(re.fullmatch('[a-f0-9]{64}', result['content_version']) is not None, 'Invalid content version')
    return result


def archive_path(folder, record):
    return folder / f"reuse-archive-{_identity(record)['content_version']}.json.gz"


def _retired_path(folder, record):
    return archive_path(folder, record).with_suffix('.retired.json')


def archive_retired(folder, record):
    path = _retired_path(folder, record)
    return (path.exists() and not archive_path(folder, record).exists()
            and read_json(_safe(folder, path))['identity'] == _identity(record))


def archive_on_hold(folder):
    return _optional(_safe(folder, folder / 'retention-hold.json'), {}).get('active') is True


def read_archive(path, record, confirmed):
    _safe(path.parent, path)
    with gzip.open(path, 'rb') as stream:
        envelope = json.load(stream)
    data = envelope['data']
    require(data['schema_version'] == 1 and envelope['sha256'] == fireplay._hash(data)
            and data['identity'] == _identity(record)
            and data['closeout_sha256'] == fireplay._hash(confirmed), 'Reuse archive evidence mismatch')
    known = {row['sha256'] for row in record['downloads'].values()
             if row['content_version'] == record['content_version']}
    rows = data['members']
    require(len(rows) == confirmed['verified_members'] == len({item['delivery']['resource_id'] for item in rows})
            and {item['delivery']['material_id'] for item in rows} == set(confirmed['verified_material_ids'])
            and all(item['source_sha256'] in known for item in rows), 'Reuse archive does not cover the confirmed matrix')
    return data


def _archive(root, folder, record, confirmed, *, apply):
    from .fireplay_reuse import _verified_history

    path = archive_path(folder, record)
    if path.exists():
        read_archive(path, record, confirmed)
        return 'ready'
    identity = _identity(record)
    snapshot = read_json(_safe(root, folder / 'snapshots' / f"{identity['content_version']}.json"))
    content = snapshot['content']
    asset_type = content['assetType']
    require(fireplay._hash(snapshot) == identity['content_version']
            and content['id'] == identity['requirement_id'] and content['code'] == identity['code']
            and asset_type in ('image', 'button'), 'Registered source snapshot changed')
    rows = _verified_history(root, record, confirmed, asset_type, archive=True)
    data = {'schema_version': 1, 'identity': identity, 'asset_type': asset_type,
            'closeout_sha256': fireplay._hash(confirmed), 'members': [
                {'source_sha256': digest, 'delivery': {field: row[field] for field in MEMBER_FIELDS}}
                for digest, row in rows]}
    if apply:
        temp = path.with_name(f'.reuse-archive-{uuid4().hex}.tmp')
        try:
            with temp.open('xb') as stream:
                stream.write(gzip.compress(json.dumps({'data': data, 'sha256': fireplay._hash(data)},
                                                       ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
                stream.flush()
                os.fsync(stream.fileno())
            read_archive(temp, record, confirmed)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    return 'created' if apply else 'eligible'


def _date(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Retention requires an explicit timezone')
    return parsed.astimezone(LOCAL_ZONE).date()


def _owned_roots(root, folder, record):
    code = record['code']
    bases = [folder / 'sources', root / 'work/fireplay-production' / code]
    for proof in (root / 'runs').glob('*/fireplay-source.json'):
        if proof.resolve() != proof:
            continue
        try:
            source = read_json(proof)
            if (source.get('code') == code and source.get('content_version') == record['content_version']
                    and source.get('source') == record['downloads'].get(str(source.get('source_index')))):
                bases.append(proof.parent)
        except (ValueError, OSError):
            continue
    return [_safe(root, path) for path in bases]


def _case_reference(root, code, owned):
    tokens = [str(path.relative_to(root)).encode() for path in owned]
    for path in (root / 'knowledge/cases').rglob('*'):
        if path.suffix in ('.json', '.md') and path.is_file() and not path.is_symlink():
            content = path.read_bytes()
            if code.encode() in content or any(token in content for token in tokens):
                return True
    return False


def _shared_reference(root, owned, intake):
    tokens = [str(path.relative_to(root)).encode() + b'/' for path in owned]
    for folder in ('work/fireplay-production', 'runs', 'state/fireplay'):
        for path in (root / folder).rglob('*'):
            if path.suffix not in ('.json', '.jsonl', '.md', '.py', '.mjs') or not path.is_file():
                continue
            if path.is_symlink() or any(path.is_relative_to(base) for base in (*owned, intake)):
                continue
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                continue
            if any(token in content for token in tokens):
                return True
    return False


def _media(root, owned, cutoff):
    for base in owned:
        for path in sorted(base.rglob('*')):
            if (path.suffix.lower() in MEDIA and not path.is_symlink() and path.is_file()
                    and path.resolve() == path and path.stat().st_mtime < cutoff):
                yield _safe(root, path)


def _move(root, source, destination):
    _safe(root, source)
    _safe(root, destination)
    require(not destination.exists(), 'A file already exists at the retention destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def _retention_bucket(root, record, kind, now):
    return root / 'work/storage-retention/quarantine' / _identity(record)['code'] / (
        f"{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{kind}-{uuid4().hex}")


def _retire(root, paths, record, kind, now):
    bucket = _safe(root, _retention_bucket(root, record, kind, now))
    entries = []
    for path in paths:
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        require((before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_ino, after.st_size, after.st_mtime_ns), 'Artifact changed while hashing')
        entries.append({'path': str(path.relative_to(root)), 'bytes': after.st_size, 'sha256': digest,
                        'inode': after.st_ino, 'mtime_ns': after.st_mtime_ns})
    bucket.mkdir(parents=True)
    fireplay._write(bucket / 'receipt.json', {'identity': _identity(record), 'kind': kind,
                                            'moved_at': now.isoformat(), 'files': entries})
    for path, entry in zip(paths, entries):
        info = path.stat()
        require((info.st_ino, info.st_size, info.st_mtime_ns) ==
                (entry['inode'], entry['bytes'], entry['mtime_ns']), 'Artifact changed before quarantine')
        _move(root, path, bucket / 'files' / path.relative_to(root))


def _protected(root, folder, production, record, *, locked=False):
    if archive_on_hold(folder):
        return 'manual_hold'
    if locked and any((production / lock).exists() for lock in ('.lock', 'delivery.lock')):
        return 'active_lock'
    if not locked and (any((production / lock).exists() for lock in LOCKS) or (folder / '.lock').exists()):
        return 'active_lock'
    monitor = monitor_directory(root)
    identifier = record['requirement_id']
    assignment = _optional(_safe(root, monitor / 'assignments.json'), {'requirements': {}})['requirements'].get(identifier, {})
    admission = _optional(_safe(root, monitor / 'admissions.json'), {'requirements': {}})['requirements'].get(identifier, {})
    if (admission.get('state') == 'reserved'
            or any(assignment.get(field) for field in ('pending_operation', 'pending_chat_operation', 'unknown_operation'))
            or any(assignment.get(field) in ('active', 'running', 'inProgress', 'dispatched')
                   for field in ('last_chat_status', 'last_observed_thread_status', 'last_turn_status',
                                 'last_observed_turn_status'))
            or assignment.get('state') not in (None, 'verified_complete')
            or assignment.get('platform_status') not in (None, 'done')):
        return 'active_or_unconfirmed_assignment'
    from .delivery_autopilot import _journal_operations, _recovered_uploads

    operations = _journal_operations(production, identifier)
    recovered = _recovered_uploads(production, {'id': identifier, 'code': record['code']}, operations)
    if any(operation.get('state') in ('unknown', 'prepared') and operation_id not in recovered
           for operation_id, operation in operations.items()):
        return 'unsettled_publication'
    return None


def maintain_storage(root, *, code=None, apply=False, archive_only=False, now=None):
    from .fireplay_reuse import _confirmed_identity

    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    policy = read_json(root / 'config/storage-retention.json')
    media_days, archive_days, quarantine_days = (policy[key] for key in ('media_days', 'archive_days', 'quarantine_days'))
    require(all(type(days) is int for days in (media_days, archive_days, quarantine_days))
            and 0 < media_days < archive_days and quarantine_days > 0, 'Invalid retention periods')
    folders = [fireplay.request_folder(code, root)] if code else sorted((root / 'state/fireplay').iterdir())
    report = {'mode': 'apply' if apply else 'preview', 'policy': policy, 'demands': []}
    for folder in folders:
        finish = folder / 'status-finish.json'
        if not finish.is_file():
            continue
        row = {'code': folder.name}
        report['demands'].append(row)
        try:
            _safe(root, finish)
            production = _safe(root, root / 'work/fireplay-production' / folder.name)
            with ExitStack() as stack:
                if apply:
                    monitor = _safe(root, monitor_directory(root))
                    stack.enter_context(fireplay._lock(monitor))
                    stack.enter_context(fireplay._lock(folder))
                    if production.is_dir():
                        stack.enter_context(fireplay._lock(production, 'publication.lock'))
                record = fireplay.load_record(folder.name, root)
                confirmed = read_json(finish)
                if not _confirmed_identity(confirmed, record):
                    row['state'] = 'unconfirmed_current_version'
                    continue
                if archive_retired(folder, record):
                    row['state'] = 'retired'
                    continue
                protected = _protected(root, folder, production, record, locked=apply)
                if protected and protected != 'active_or_unconfirmed_assignment':
                    row['state'] = protected
                    continue
                row['archive'] = _archive(root, folder, record, confirmed, apply=apply)
                if protected or archive_only:
                    row['state'] = protected or 'archived'
                    continue
                hold = _optional(folder / 'retention-hold.json', {})
                reference = max(_date(confirmed['readback_at']),
                                _date(hold.get('released_at', confirmed['readback_at'])))
                age = (_date(now.isoformat()) - reference).days
                row['age_days'] = age
                row['state'] = 'retained'
                if age < media_days:
                    continue
                owned = _owned_roots(root, folder, record)
                if _case_reference(root, folder.name, owned) or _shared_reference(root, owned, folder):
                    row['state'] = 'referenced_evidence'
                    continue
                cutoff = now.timestamp() - media_days * 86400
                files = list(_media(root, owned, cutoff))
                row['media_files'] = len(files)
                row['media_logical_bytes'] = sum(path.stat().st_size for path in files)
                if files and apply:
                    _retire(root, files, record, 'media', now)
                remaining = any(_media(root, owned, float('inf')))
                row['state'] = ('media_quarantined' if apply else 'media_due') if files else (
                    'media_in_grace' if remaining else 'media_retired')
                if age >= archive_days and not remaining:
                    row['state'] = 'archive_due'
                    if apply:
                        path = archive_path(folder, record)
                        marker = _retired_path(folder, record)
                        fireplay._write(marker, {'identity': _identity(record), 'retired_at': now.isoformat()})
                        try:
                            _retire(root, [path], record, 'archive', now)
                        except (OSError, ValueError):
                            marker.unlink()
                            raise
                        row['state'] = 'archive_quarantined'
        except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
            row.update(state='kept_for_review', reason=str(error))
    return report


def retention_hold(root, code, reason, *, release=False):
    root = root.resolve()
    folder = fireplay.request_folder(code, root)
    require(folder.is_dir() and isinstance(reason, str) and reason.strip(), 'An existing demand and hold reason are required')
    with fireplay._lock(folder):
        path = _safe(root, folder / 'retention-hold.json')
        now = datetime.now(timezone.utc).isoformat()
        previous = _optional(path, {})
        require(not release or previous.get('active'), 'There is no active retention hold')
        result = {'code': code, 'active': not release, 'reason': reason,
                  'released_at' if release else 'held_at': now}
        fireplay._write(path, result)
        return result


def manage_quarantine(root, code=None, *, purge=False, apply=False, now=None):
    from .fireplay_reuse import _confirmed_identity

    root = root.resolve()
    now = now or datetime.now(timezone.utc)
    days = read_json(root / 'config/storage-retention.json')['quarantine_days']
    base = _safe(root, root / 'work/storage-retention/quarantine')
    if code is not None:
        fireplay.request_folder(code, root)
        base = _safe(root, base / code)
    pattern = '*/receipt.json' if code is not None else '*/*/receipt.json'
    result = {'mode': 'purge' if purge else 'restore', 'apply': apply, 'operations': []}
    for receipt in sorted(base.glob(pattern), reverse=True):
        data = read_json(_safe(root, receipt))
        folder = fireplay.request_folder(data['identity']['code'], root)
        require(receipt.parent.parent.name == folder.name and data['kind'] in ('media', 'archive'),
                'Quarantine receipt does not match its directory')
        production = _safe(root, root / 'work/fireplay-production' / folder.name)
        operation = {'code': folder.name, 'kind': data['kind'], 'files': 0, 'bytes': 0}
        result['operations'].append(operation)
        with ExitStack() as stack:
            if apply:
                stack.enter_context(fireplay._lock(_safe(root, monitor_directory(root))))
                stack.enter_context(fireplay._lock(folder))
                if production.is_dir():
                    stack.enter_context(fireplay._lock(production, 'publication.lock'))
            record = fireplay.load_record(folder.name, root)
            hold = _optional(folder / 'retention-hold.json', {})
            if data['identity'] != _identity(record) and purge:
                operation['state'] = 'version_changed'
                continue
            if purge:
                confirmed = read_json(_safe(root, folder / 'status-finish.json'))
                start = max(_date(data['moved_at']), _date(hold.get('released_at', data['moved_at'])))
                if (not _confirmed_identity(confirmed, record)
                        or _protected(root, folder, production, record, locked=apply)
                        or (_date(now.isoformat()) - start).days < days):
                    operation['state'] = 'retained'
                    continue
                if data['kind'] == 'media':
                    owned = _owned_roots(root, folder, record)
                    if _case_reference(root, folder.name, owned) or _shared_reference(root, owned, folder):
                        operation['state'] = 'referenced_evidence'
                        continue
            pairs = []
            for entry in data['files']:
                target = _safe(root, root / entry['path'])
                staged = _safe(root, receipt.parent / 'files' / entry['path'])
                require(staged.is_relative_to(receipt.parent / 'files'), 'Invalid quarantine file path')
                if data['kind'] == 'archive':
                    require(target == archive_path(folder, data['identity']), 'Unexpected quarantined archive path')
                else:
                    require(target.suffix.lower() in MEDIA and
                            (target.is_relative_to(production) or target.is_relative_to(folder / 'sources')
                             or target.is_relative_to(root / 'runs')), 'Unexpected quarantined media path')
                if not staged.is_file():
                    continue
                if not purge:
                    require(not target.exists(), f'Restore destination already exists: {target}')
                require(sha256(staged) == entry['sha256'], f'Quarantined artifact changed: {staged.name}')
                pairs.append((staged, target, entry['bytes']))
            if apply and not purge and pairs:
                fireplay._write(_safe(root, folder / 'retention-hold.json'), {
                    'code': folder.name, 'active': True, 'reason': 'restored_from_quarantine', 'held_at': now.isoformat()})
            for staged, target, size in pairs:
                if apply and purge:
                    staged.unlink()
                elif apply:
                    _move(root, staged, target)
                operation['files'] += 1
                operation['bytes'] += size
            if apply and not purge and pairs and data['kind'] == 'archive':
                _retired_path(folder, data['identity']).unlink(missing_ok=True)
            operation['state'] = ('purged' if purge else 'restored') if apply else 'eligible'
    return result
