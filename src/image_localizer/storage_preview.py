"""Inventory repeated local media and fonts before any storage maintenance."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .pipeline import sha256


GROUPS = {
    'image': frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff', '.tif', '.avif'}),
    'font': frozenset({'.ttc', '.ttf', '.otf', '.otc'}),
}
FOLDERS = ('work/fireplay-production', 'runs', 'state/fireplay')


def _confirmed_demands(root):
    confirmed = set()
    for path in (root / 'state' / 'fireplay').glob('*/status-finish.json'):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(state, dict) and state.get('state') == 'confirmed' and state.get('code') == path.parent.name:
                confirmed.add(path.parent.name)
        except (OSError, ValueError):
            continue
    return confirmed


def preview_storage(root: Path, limit: int) -> dict:
    if not 0 <= limit <= 100:
        raise ValueError('Storage preview limit must be between 0 and 100')
    confirmed = _confirmed_demands(root)
    buckets = defaultdict(list)
    folder_bytes = Counter()
    inodes = set()
    for folder in FOLDERS:
        base = root / folder
        for path in base.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            folder_bytes[folder] += info.st_size
            kind = next((category for category, extensions in GROUPS.items() if path.suffix.lower() in extensions), None)
            inode = (info.st_dev, info.st_ino)
            if kind and info.st_size and inode not in inodes:
                inodes.add(inode)
                buckets[kind, info.st_size].append(path)
    summaries = {kind: {'groups': 0, 'extra_copies': 0, 'logical_extra_bytes': 0} for kind in GROUPS}
    groups = []
    for (kind, size), paths in buckets.items():
        if len(paths) <= 1:
            continue
        by_hash = defaultdict(list)
        for path in paths:
            try:
                by_hash[sha256(path)].append(path.relative_to(root))
            except FileNotFoundError:
                continue
        for digest, copies in by_hash.items():
            if len(copies) <= 1:
                continue
            extra = size * (len(copies) - 1)
            summaries[kind]['groups'] += 1
            summaries[kind]['logical_extra_bytes'] += extra
            summaries[kind]['extra_copies'] += len(copies) - 1
            scopes = sum(len(path.parts) > 2 and path.parts[2] in confirmed
                         for path in copies if path.parts[:2] == ('work', 'fireplay-production'))
            groups.append({'kind': kind, 'sha256': digest, 'copies': len(copies), 'logical_extra_bytes': extra,
                           'copies_in_confirmed_demands': scopes, 'examples': [str(path) for path in sorted(copies)[:3]]})
    groups.sort(key=lambda row: (-row['logical_extra_bytes'], row['sha256']))
    return {'mode': 'inventory', 'observed_at': datetime.now(timezone.utc).isoformat(),
            'folder_logical_bytes': {folder: folder_bytes[folder] for folder in FOLDERS},
            'duplicates': summaries, 'confirmed_demands': len(confirmed), 'largest_groups': groups[:limit]}
