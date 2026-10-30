"""Load curated project knowledge without treating unrelated cases as defaults."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import require


def load_knowledge(root: Path, *, source_sha256: str, tags: list[str] | None = None) -> dict:
    root = root.resolve()
    index_path = root / 'knowledge/index.json'
    index = json.loads(index_path.read_text(encoding='utf-8'))
    require(isinstance(index, dict) and index.get('version') == 1, 'Unsupported knowledge index')
    entries = index.get('entries')
    require(isinstance(entries, list), 'Knowledge entries must be a list')
    require(tags is None or isinstance(tags, list) and all(isinstance(tag, str) for tag in tags), 'content_tags must be strings')
    requested_tags = set(tags or [])
    methods, cases, available, seen = [], [], [], set()
    for entry in entries:
        require(isinstance(entry, dict), 'Knowledge entry must be an object')
        identity = entry.get('id')
        require(isinstance(identity, str) and identity and identity not in seen, 'Knowledge IDs must be unique')
        seen.add(identity)
        relative = entry.get('path')
        require(isinstance(relative, str) and relative, 'Knowledge path is required')
        path = (root / relative).resolve()
        require(path.is_relative_to(root / 'knowledge') and path.suffix == '.md', 'Knowledge path must stay inside knowledge Markdown files')
        require(entry.get('kind') in ('method', 'case'), 'Invalid knowledge kind')
        require(entry.get('status') in ('active', 'provisional', 'partial'), 'Invalid knowledge status')
        entry_tags = entry.get('tags', [])
        require(isinstance(entry_tags, list) and all(isinstance(tag, str) for tag in entry_tags), 'Knowledge tags must be strings')
        data = path.read_bytes()
        item = {**entry, 'sha256': hashlib.sha256(data).hexdigest()}
        if entry['kind'] == 'method':
            methods.append({**item, 'content': data.decode('utf-8')})
        else:
            available.append(item)
            if entry.get('source_sha256') == source_sha256 or requested_tags.intersection(entry_tags):
                cases.append({**item, 'content': data.decode('utf-8')})
    return {'index': str(index_path), 'methods': methods, 'related_cases': cases, 'available_cases': available,
            'notice': 'Methods are conditional guidance, not verified capabilities. Partial cases retain unresolved feedback; current images still require actual inspection.'}
