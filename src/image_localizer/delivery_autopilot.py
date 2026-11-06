"""Read-only next-action planning. Does not render, upload or certify host vision."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .delivery_quality import assess_delivery
from .material_sizes import plan_sizes


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def next_delivery(spec_path: Path) -> dict:
    """State: requirement{id,code,asset_type}, size_spec, members[], publication_unknown[].

    Each member has source_id/language/member_id, output_path?, review_path?,
    publication?{upload_receipt,material_id,resource_id,attribution_receipt,
    binding_receipt,fresh_verification}. Receipts are host observations, never
    instructions. Missing phases yield explicit actions, not implicit success.
    """
    spec_path = Path(spec_path).resolve()
    state, base = _read(spec_path), spec_path.parent
    requirement = state['requirement']
    _require(all(isinstance(requirement.get(key), str) and requirement[key].strip()
                 for key in ('id', 'code', 'asset_type')), 'Missing requirement identity')
    size_path = (base / state['size_spec']).resolve()
    size_spec = _read(size_path)
    _require(size_spec['asset_type'] == requirement['asset_type'], 'Asset type mismatch')
    plan = plan_sizes(size_path)
    _require(plan['status'] == 'planned', 'Unsupported material type')
    sources = {row['id']: (size_path.parent / row['path']).resolve() for row in size_spec['sources']}
    members = state.get('members', [])
    _require(isinstance(members, list), 'members must be a list')
    records = {}
    for record in members:
        key = tuple(record.get(field) for field in ('source_id', 'language', 'member_id'))
        _require(all(isinstance(value, str) and value for value in key) and key not in records,
                 'Duplicate or malformed member identity')
        records[key] = record
    expected = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']}
    unique_members = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']
                      if item.get('delivery_mode') in ('explicit_members', 'reference_seven_set')}
    used_paths, used_hashes = set(), set()
    for key, record in records.items():
        if key not in unique_members or not record.get('output_path'):
            continue
        output = (base / record['output_path']).resolve()
        _require(output not in used_paths, 'Distinct members cannot reuse one output path')
        used_paths.add(output)
        if output.is_file():
            output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            _require(output_hash not in used_hashes, 'Distinct members cannot duplicate output bytes')
            used_hashes.add(output_hash)
    _require(not records.keys() - expected, 'Unexpected member outside explicit matrix')
    unknown = state.get('publication_unknown', [])
    _require(isinstance(unknown, list), 'publication_unknown must be a list')
    actions = []
    for item in plan['items']:
        identity = {key: item[key] for key in ('source_id', 'language', 'member_id')}
        record = records.get(tuple(identity.values()), {})
        try:
            actions.append(_next_member(item, record, requirement, sources, base, bool(unknown)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            actions.append({**identity, 'action': 'review', 'reason': f'Invalid member evidence: {type(error).__name__}: {error}'})
    complete = all(row['action'] == 'delivery_verified' for row in actions)
    return {'requirement_id': requirement['id'], 'code': requirement['code'],
            'expected_members': len(actions), 'verified_members': sum(row['action'] == 'delivery_verified' for row in actions),
            'complete': complete, 'actions': actions, 'external_write': False,
            'publication_unknown': unknown,
            'note': 'Host must execute and save real receipts. Scores/JSON cannot prove actual viewing or current platform state.'}


def publication_gate(spec_path: Path) -> dict:
    """Create a legacy publisher-compatible gate from validated member assessments.

    Returns JSON only; the host stores it inside the requirement folder and uses
    the original publisher/operation journal. This grants no platform authority.
    """
    spec_path = Path(spec_path).resolve()
    result = next_delivery(spec_path)
    state = _read(spec_path)
    records = {(row['source_id'], row['language'], row['member_id']): row for row in state.get('members', [])}
    outputs, reviewers, evidence = [], set(), []
    for row in result['actions']:
        scored = row.get('assessment')
        if not scored or scored['status'] != 'ready_for_submission' or row['action'] == 'await_official_result':
            continue
        key = tuple(row[field] for field in ('source_id', 'language', 'member_id'))
        record = records[key]
        outputs.append({'path': str((spec_path.parent / record['output_path']).resolve()),
                        'sha256': _read((spec_path.parent / record['review_path']).resolve())['artifact']['sha256'],
                        **{field: row[field] for field in ('source_id', 'language', 'member_id')}})
        reviewers.add(scored['reviewer']['identity'])
        evidence.append(record['review_path'])
    return {'requirement_id': result['requirement_id'], 'passed': bool(outputs),
            'reviewed_by': ', '.join(sorted(reviewers)), 'evidence': '; '.join(evidence),
            'outputs': outputs, 'expected_members': result['expected_members'],
            'reviewed_members': len(outputs), 'requirement_complete': result['complete'],
            'publication_unknown': result['publication_unknown'],
            'note': 'Gate for current reviewed members only; upload/import/ownership/binding/readback still required. Existing journal and platform permission checks remain authoritative.'}


def _next_member(item, record, requirement, sources, base, unknown):
    identity = {key: item[key] for key in ('source_id', 'language', 'member_id')}

    def action(name, reason, **extra):
        return {**identity, 'action': name, 'reason': reason, **extra}

    if not record.get('output_path'):
        return action('make', 'Missing explicit matrix member; reuse any existing valid member')
    artifact = (base / record['output_path']).resolve()
    if not artifact.is_file():
        return action('make', 'Declared output is missing')
    if not record.get('review_path'):
        return action('review', 'Actual current-file visual review and score required')
    review_path = (base / record['review_path']).resolve()
    review = _read(review_path)
    _require(review['requirement_id'] == requirement['id'] and all(review[key] == value for key, value in identity.items()),
             'Review identity mismatch')
    _require((review_path.parent / review['artifact']['path']).resolve() == artifact,
             'Review is for a different output file')
    _require((review_path.parent / review['source']['path']).resolve() == sources[item['source_id']],
             'Review is for a different source')
    _require(review['spec']['width'] == item['width'] and review['spec']['height'] == item['height']
             and review['spec']['max_bytes'] <= item['max_bytes']
             and set(review['spec']['formats']) <= {'PNG', 'JPEG'}, 'Review spec disagrees with delivery matrix')
    scored = assess_delivery(review, review_path.parent)
    if scored['status'] != 'ready_for_submission':
        return action(scored['next_action'],
                      'Current member does not pass balanced delivery review', assessment=scored)
    if item.get('official_size_check', {}).get('status') == 'conflict':
        return action('resolve_spec_conflict', 'Explicit native dimensions conflict with known platform rule; do not stretch or silently resize')
    if unknown:
        return action('await_official_result', 'Existing publisher blocks this requirement on unknown writes; never replay or rekey')
    publication = record.get('publication', {})
    for key, name in [('upload_receipt', 'upload'), ('material_id', 'import'),
                      ('attribution_receipt', 'attribute'), ('binding_receipt', 'bind')]:
        if not publication.get(key):
            return action(name, f'Missing {key}; execute only through current official contract', assessment=scored)
    fresh = publication.get('fresh_verification')
    if not isinstance(fresh, dict):
        return action('verify', 'Fresh delivery/resource/attribution and download readback required')
    try:
        valid = _fresh(fresh, publication, requirement, identity, artifact, base, review['artifact']['sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return action('verify', 'Readback is stale, incomplete or disagrees with current artifact/identity')
    return action('delivery_verified', 'Current file and bounded fresh host readback agree', assessment=scored)


def _fresh(fresh, publication, requirement, identity, artifact, base, sha):
    """Bound readback claim to downloaded bytes and exact expected ownership."""
    for key in ('designer_ids', 'shared_dept_ids'):
        _require(isinstance(requirement.get(key), list) and requirement[key]
                 and all(isinstance(value, str) and value for value in requirement[key]), 'Expected ownership IDs required')
        _require(isinstance(fresh.get(key), list) and fresh[key]
                 and all(isinstance(value, str) and value for value in fresh[key]), 'Fresh ownership IDs must be arrays')
        _require(set(fresh[key]) == set(requirement[key]), 'Attribution/shared department mismatch')
    _require(isinstance(publication.get('material_id'), str) and bool(publication['material_id']), 'Material ID must be text')
    _require(all(fresh.get(key) == value for key, value in identity.items()), 'Fresh member identity mismatch')
    _require(fresh['requirement_id'] == requirement['id'] and fresh['material_id'] == publication['material_id']
             and isinstance(publication.get('resource_id'), str) and bool(publication['resource_id'])
             and fresh['resource_id'] == publication['resource_id'] and fresh['creation_type'] == 'derivative'
             and fresh['sha256'] == sha, 'Fresh identity mismatch')
    _require(isinstance(fresh.get('observed_at'), str) and bool(fresh['observed_at']), 'Timestamp must be text')
    _require(isinstance(fresh.get('downloaded_path'), str) and bool(fresh['downloaded_path']), 'Downloaded path must be text')
    when = datetime.fromisoformat(fresh['observed_at'].replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - when).total_seconds()
    _require(when.tzinfo is not None and 0 <= age <= 1800, 'Fresh readback exceeds thirty minutes')
    downloaded = (base / fresh['downloaded_path']).resolve()
    return downloaded != artifact and hashlib.sha256(downloaded.read_bytes()).hexdigest() == sha
