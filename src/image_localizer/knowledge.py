"""Load curated project knowledge without treating unrelated cases as defaults."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .contracts import require


def load_knowledge(root: Path, *, source_sha256: str, tags: list[str] | None = None,
                   include_content: bool = True) -> dict:
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
        related = entry.get('source_sha256') == source_sha256 or bool(requested_tags.intersection(entry_tags))
        if not include_content and entry['kind'] == 'case' and not related:
            continue
        data = path.read_bytes()
        item = {**entry, 'sha256': hashlib.sha256(data).hexdigest()}
        if entry['kind'] == 'method':
            methods.append({**item, 'content': data.decode('utf-8')} if include_content else item)
        else:
            available.append(item)
            if related:
                cases.append({**item, 'content': data.decode('utf-8')} if include_content else item)
    return {'index': str(index_path), 'methods': methods, 'related_cases': cases, 'available_cases': available,
            'notice': 'Methods are conditional guidance, not verified capabilities. Partial cases retain unresolved feedback; current images still require actual inspection.'}


def audit_knowledge(root: Path) -> dict:
    root = root.resolve()
    knowledge = load_knowledge(root, source_sha256='')
    entries = knowledge['methods'] + knowledge['available_cases']
    counts = Counter(f"{entry['kind']}/{entry['status']}" for entry in entries)
    issues = []
    paths = set()
    for entry in entries:
        path = (root / entry['path']).resolve()
        if path in paths:
            issues.append({'entry': entry['id'], 'issue': 'duplicate_index_path'})
        paths.add(path)
        if reference := entry.get('feedback_path'):
            feedback = (root / reference).resolve()
            if not feedback.is_relative_to(root / 'knowledge') or not feedback.is_file():
                issues.append({'entry': entry['id'], 'issue': 'missing_or_invalid_feedback_path'})
    candidates = []
    staging = root / 'work/knowledge-candidates'
    for path in sorted(staging.glob('*.json')) if staging.resolve() == staging else []:
        try:
            require(path.resolve() == path, 'Candidate must be a local file')
            candidate = json.loads(path.read_bytes())
            for field in ('observation', 'applies_when', 'next_decision', 'evidence_path', 'evidence_sha256'):
                require(isinstance(candidate.get(field), str) and candidate[field].strip(), f'Missing {field}')
            evidence = (root / candidate['evidence_path']).resolve()
            require(evidence.is_relative_to(root) and evidence.is_file(), 'Evidence must be a project file')
            with evidence.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(actual == candidate['evidence_sha256'], 'Candidate evidence hash changed')
            candidates.append({'record': path.name, 'observation': candidate['observation'],
                               'next_decision': candidate['next_decision'], 'status': 'pending_review'})
        except (ValueError, OSError, TypeError, AttributeError) as error:
            issues.append({'candidate': path.name, 'issue': str(error)})
    return {'indexed': dict(counts), 'pending_candidates': candidates, 'issues': issues}
