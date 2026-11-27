"""Compact byte-identical font snapshots in verified legacy deliveries."""
from collections import defaultdict
from contextlib import ExitStack
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import uuid4

from .artifact_copy import clone_artifact
from .fireplay import _lock
from .storage_preview import GROUPS


FONT_EXTENSIONS = frozenset({'.ttc', '.ttf', '.otf', '.otc'})
CODE = re.compile(r'[A-Z][A-Z0-9]{1,15}-\d{8}-\d{3,6}')
COPYFILE_METADATA_NOFOLLOW = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 18) | (1 << 19)
WORK_IMAGE_DIRECTORIES = frozenset({
    'artifacts', 'backgrounds', 'first-proof', 'fresh-downloads', 'jobs', 'lettering',
    'preview', 'previews', 'proofs', 'scratch', 'second-proof', 'stages',
})


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _eligible(production, assignments, *, locked=False):
    rows = json.loads(assignments)['requirements']
    eligible = []
    for row in rows.values():
        code = row.get('code')
        if not isinstance(code, str) or not CODE.fullmatch(code):
            continue
        if row.get('state') != 'verified_complete' or row.get('delivery_verified_by_controller') is not True:
            continue
        if (row.get('pending_operation') or row.get('pending_chat_operation')
                or row.get('platform_status') == 'in_progress'
                or any(row.get(field) in ('active', 'running', 'inProgress', 'dispatched') for field in
                       ('last_chat_status', 'last_observed_thread_status', 'last_turn_status',
                        'last_observed_turn_status'))):
            continue
        demand = production / code
        journal = demand / 'publication-journal.jsonl'
        if demand.is_symlink() or journal.is_symlink() or not journal.is_file():
            continue
        locks = ('.lock', 'delivery.lock') if locked else ('.lock', 'publication.lock', 'delivery.lock')
        if any(os.path.lexists(demand / name) for name in locks):
            continue
        with journal.open(encoding='utf-8') as stream:
            entries = [json.loads(line) for line in stream if line.strip()]
        states = {entry['operation_id']: entry['state'] for entry in entries}
        if entries and set(states.values()) == {'received'}:
            eligible.append(demand)
    return sorted(set(eligible))


def _groups(eligible, *, current=False, images_only=False):
    by_size = defaultdict(list)
    for demand in eligible:
        frozen = set()
        if current:
            for entry in (demand / 'publication-journal.jsonl').read_text(encoding='utf-8').splitlines():
                if entry.strip():
                    for output in json.loads(entry).get('outputs', []):
                        if output.get('path'):
                            frozen.add((demand / output['path']).resolve())
        for path in demand.rglob('*'):
            extension = path.suffix.lower()
            relative = path.relative_to(demand)
            image = (current and extension in GROUPS['image'] and path not in frozen and
                     (relative.parts[0] in WORK_IMAGE_DIRECTORIES
                      or re.fullmatch(r'render-\d+', relative.parts[0])))
            if not (image or (extension in FONT_EXTENSIONS and not images_only)) or path.is_symlink():
                continue
            info = path.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size:
                by_size[(info.st_dev, info.st_size, 'image' if image else 'font')].append(path)
    for paths in by_size.values():
        if len(paths) < 2:
            continue
        by_hash = defaultdict(list)
        for path in sorted(paths):
            by_hash[_digest(path)].append(path)
        for digest, copies in by_hash.items():
            if len(copies) > 1:
                yield digest, copies


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _metadata(info):
    return (info.st_mode, info.st_uid, info.st_gid, info.st_mtime_ns, info.st_flags)


def _xattrs(path):
    return subprocess.run(('/usr/bin/xattr', '-lxs', str(path)), check=True, capture_output=True).stdout


def _copy_metadata(source, destination):
    library = ctypes.CDLL(None, use_errno=True)
    copyfile = library.copyfile
    copyfile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32)
    copyfile.restype = ctypes.c_int
    if copyfile(os.fsencode(source), os.fsencode(destination), None, COPYFILE_METADATA_NOFOLLOW):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), source)


def _replace_with_clone(source, destination, digest):
    original = destination.lstat()
    if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1 or _digest(destination) != digest:
        raise ValueError('Legacy font changed during compaction')
    temporary = destination.with_name(f'.compact-font-{uuid4().hex}')
    try:
        if not clone_artifact(source, temporary):
            raise ValueError('Filesystem did not create a copy-on-write clone')
        if _digest(temporary) != digest:
            raise ValueError('Cloned font differs from the original')
        _copy_metadata(destination, temporary)
        copied = temporary.lstat()
        if (_metadata(original) != _metadata(copied) or copied.st_ino == original.st_ino
                or _xattrs(destination) != _xattrs(temporary)):
            raise ValueError('Clone failed metadata or inode validation')
        if _identity(destination.lstat()) != _identity(original):
            raise ValueError('Legacy font changed before atomic replacement')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _free_bytes(root):
    info = os.statvfs(root)
    return info.f_bavail * info.f_frsize


def compact_legacy_fonts(root: Path, receipt: Path | None = None, *, current=False, images_only=False) -> dict:
    root = root.resolve(strict=True)
    production = root / 'work' / 'fireplay-production'
    assignments = root / 'work' / 'fireplay-monitor' / 'assignments.json'
    if not production.is_dir() or production.resolve() != production or assignments.resolve() != assignments:
        raise ValueError('Expected a legacy Fireplay project with local production and assignments')
    assignments_snapshot = assignments.read_bytes()
    def eligible_demands(*, locked=False):
        demands = _eligible(production, assignments_snapshot, locked=locked)
        if not current:
            return demands
        from .fireplay import load_record, request_folder
        from .fireplay_reuse import _confirmed_identity
        from .pipeline import read_json

        confirmed = []
        for demand in demands:
            try:
                folder = request_folder(demand.name, root)
                finish = folder / 'status-finish.json'
                hold = folder / 'retention-hold.json'
                if (not (folder / '.lock').exists() and finish.resolve() == finish
                        and (not hold.exists() or not read_json(hold).get('active'))
                        and _confirmed_identity(read_json(finish), load_record(demand.name, root))):
                    confirmed.append(demand)
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return confirmed

    eligible = eligible_demands()
    receipt = receipt.resolve() if receipt is not None else None
    if receipt is not None and (sys.platform != 'darwin' or (receipt.is_relative_to(root) and
                                not (current and receipt.is_relative_to(root / 'work/storage-retention')))):
        raise ValueError('Compaction needs macOS and an allowed new audit path')
    result = {'mode': 'compact' if receipt else 'preview', 'project': str(root), 'eligible_demands': len(eligible),
              'groups': 0, 'duplicate_fonts': 0, 'duplicate_images': 0, 'duplicate_logical_bytes': 0,
              'cloned_fonts': 0, 'cloned_images': 0, 'cloned_logical_bytes': 0, 'skipped': 0}
    with ExitStack() as stack:
        audit = None
        if receipt is not None:
            if current:
                stack.enter_context(_lock(assignments.parent))
            for demand in eligible:
                stack.enter_context(_lock(demand, 'publication.lock'))
            if (assignments.read_bytes() != assignments_snapshot
                    or not set(eligible).issubset(eligible_demands(locked=True))):
                raise ValueError('Legacy demand eligibility changed while acquiring locks')
            receipt.parent.mkdir(parents=True, exist_ok=True)
            audit = stack.enter_context(receipt.open('x', encoding='utf-8'))
            result['before_available_bytes'] = _free_bytes(root)
            audit.write(json.dumps({'event': 'start', 'project': str(root),
                                    'at': datetime.now(timezone.utc).isoformat()}) + '\n')
            audit.flush()
        for digest, copies in _groups(eligible, current=current, images_only=images_only):
            source, *redundant = copies
            size = source.stat().st_size
            kind = 'fonts' if source.suffix.lower() in FONT_EXTENSIONS else 'images'
            result['groups'] += 1
            result[f'duplicate_{kind}'] += len(redundant)
            result['duplicate_logical_bytes'] += len(redundant) * size
            if audit is None:
                continue
            for destination in redundant:
                record = {'source': str(source.relative_to(root)), 'path': str(destination.relative_to(root)),
                          'sha256': digest, 'bytes': size}
                try:
                    _replace_with_clone(source, destination, digest)
                except (OSError, ValueError, subprocess.CalledProcessError) as error:
                    result['skipped'] += 1
                    audit.write(json.dumps({'event': 'skipped', 'reason': str(error), **record}) + '\n')
                else:
                    result[f'cloned_{kind}'] += 1
                    result['cloned_logical_bytes'] += size
                    audit.write(json.dumps({'event': 'cloned', **record}) + '\n')
                audit.flush()
        if audit is not None:
            result['after_available_bytes'] = _free_bytes(root)
            result['available_bytes_change'] = result['after_available_bytes'] - result['before_available_bytes']
            result['receipt'] = str(receipt)
            audit.write(json.dumps({'event': 'summary', **result}) + '\n')
            audit.flush()
            os.fsync(audit.fileno())
    return result
