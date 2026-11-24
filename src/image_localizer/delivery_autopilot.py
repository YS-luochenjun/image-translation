"""Read-only next-action planning. Does not render, upload or certify host vision."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .delivery_quality import assess_delivery
from .material_sizes import plan_sizes
from .pipeline import sha256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


MEMBER_FIELDS = ('source_id', 'language', 'member_id')


def _member(record):
    return tuple(record.get(field) for field in MEMBER_FIELDS)


def _publication_response(base, entry):
    if 'receipt_sha256' not in entry:
        return entry.get('response')
    operation_id = entry['operation_id']
    _require(str(UUID(operation_id)) == operation_id.lower(), 'Invalid publication receipt reference')
    data = (base / f'publication-{operation_id}.json').read_bytes()
    _require(hashlib.sha256(data).hexdigest() == entry['receipt_sha256'], 'Publication receipt hash mismatch')
    receipt = json.loads(data)
    _require(all(receipt[key] == entry[key] for key in ('operation_id', 'requirement_id', 'tool', 'state')),
             'Publication receipt identity mismatch')
    return receipt['response']


def _upload_index(base, requirement_id, records, expected):
    from .fireplay import _http, payload

    journal = base / 'publication-journal.jsonl'
    if not journal.exists():
        return {}, set(), False
    with journal.open(encoding='utf-8') as stream:
        entries = [json.loads(line) for line in stream if line.strip()]
    operations = {}
    for entry in entries:
        _require(entry['requirement_id'] == requirement_id, 'Publication journal requirement mismatch')
        operations[entry['operation_id']] = entry
    by_path = {(base / row['output_path']).resolve(): key
               for key, row in records.items() if row.get('output_path')}
    frozen, pending = {}, set()
    for entry in operations.values():
        if entry['tool'] != 'material_r2_upload_from_bytes' or len(entry.get('outputs', [])) != 1:
            continue
        output = entry['outputs'][0]
        key = _member(output)
        if key not in expected:
            key = by_path.get((base / output['path']).resolve())
        if key not in expected:
            continue
        if entry['state'] == 'received':
            response = _publication_response(base, entry)
            try:
                response = payload(response)
            except (ValueError, KeyError, TypeError):
                continue
            if response.get('success') is False:
                continue
            resource_ids, urls = _upload_values(response)
            resource_ids = {value for value in resource_ids if value.strip()}
            urls = {value for value in urls if _http(value)}
            if len(resource_ids) > 1 or len(urls) > 1:
                pending.add(key)
                continue
            if resource_ids or urls:
                _require(key not in frozen or frozen[key]['sha256'] == output['sha256'],
                         'Multiple uploaded byte versions need explicit member reconciliation')
                frozen[key] = {'path': output['path'], 'sha256': output['sha256'],
                               'resource_id': next(iter(resource_ids), None), 'operation_id': entry['operation_id']}
        elif entry['state'] == 'unknown':
            pending.add(key)
    unresolved = [entry for entry in operations.values() if entry['state'] == 'unknown']
    return frozen, pending, bool(unresolved)


def _upload_values(response):
    resource_ids, urls = set(), set()
    queue = [response]
    while queue:
        current = queue.pop()
        if isinstance(current, list):
            queue.extend(current)
        elif isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, str) and key in ('resourceId', 'resource_id'):
                    resource_ids.add(value)
                elif isinstance(value, str) and key in ('publicUrl', 'public_url'):
                    urls.add(value)
                elif isinstance(value, (list, dict)):
                    queue.append(value)
    return resource_ids, urls


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
        key = _member(record)
        _require(all(isinstance(value, str) and value for value in key) and key not in records,
                 'Duplicate or malformed member identity')
        records[key] = record
    expected = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']}
    frozen, pending_uploads, journal_unknown = _upload_index(base, requirement['id'], records, expected)
    unique_members = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']
                      if item.get('delivery_mode') in ('standard_seven_set', 'explicit_members', 'reference_seven_set')}
    used_paths, used_hashes, output_hashes = set(), set(), {}
    for key, record in records.items():
        if key not in unique_members or not record.get('output_path'):
            continue
        output = (base / record['output_path']).resolve()
        _require(output not in used_paths, 'Distinct members cannot reuse one output path')
        used_paths.add(output)
        if output.is_file():
            output_hash = sha256(output)
            _require(output_hash not in used_hashes, 'Distinct members cannot duplicate output bytes')
            used_hashes.add(output_hash)
            output_hashes[key] = output_hash
    _require(not records.keys() - expected, 'Unexpected member outside explicit matrix')
    unknown = state.get('publication_unknown', [])
    _require(isinstance(unknown, list), 'publication_unknown must be a list')
    actions = []
    for item in plan['items']:
        identity = {key: item[key] for key in MEMBER_FIELDS}
        key = _member(item)
        record = records.get(key, {})
        publication = record.get('publication', {})
        legacy_frozen = isinstance(publication, dict) and bool(publication.get('upload_receipt')) and bool(publication.get('resource_id'))
        try:
            if key in frozen:
                action = _frozen_action(identity, publication, frozen[key], requirement, base, bool(unknown))
            elif key in pending_uploads:
                action = {**identity, 'action': 'await_official_result',
                          'reason': 'Read back the original member upload before doing more work'}
            else:
                action = _next_member(item, record, requirement, sources, base, bool(unknown), output_hashes.get(key))
                if legacy_frozen and action['action'] in ('make', 'review', 'rework', 'blocked'):
                    action = {**identity, 'action': 'reconcile_frozen', 'frozen': True,
                              'reason': 'Recover the original upload receipt and approved evidence'}
            actions.append(action)
        except (OSError, ValueError, KeyError, TypeError) as error:
            actions.append({**identity, 'action': 'reconcile_frozen' if key in frozen or legacy_frozen else 'review',
                            'reason': f'Invalid member evidence: {type(error).__name__}: {error}'})
    complete = bool(actions) and all(row['action'] == 'delivery_verified' for row in actions)
    return {'requirement_id': requirement['id'], 'code': requirement['code'],
            'expected_members': len(actions), 'verified_members': sum(row['action'] == 'delivery_verified' for row in actions),
            'complete': complete, 'actions': actions, 'external_write': False,
            'publication_unknown': unknown or (['unresolved_publication_journal'] if journal_unknown else []),
            'verified_material_ids': sorted({records[_member(row)]['publication']['material_id']
                                            for row in actions if row['action'] == 'delivery_verified'}),
            'note': 'Host must execute and save real receipts. Scores/JSON cannot prove actual viewing or current platform state.'}


def compact_delivery(result: dict, spec_path: Path, since: str | None) -> dict:
    from .incremental_output import checkpoint_output

    actions = result['actions']
    compact = {key: result[key] for key in ('requirement_id', 'code', 'expected_members', 'verified_members',
                                           'complete', 'publication_unknown')}
    compact['counts'] = dict(Counter(row['action'] for row in actions))
    members = {json.dumps(_member(row), ensure_ascii=False, separators=(',', ':')):
               {key: value for key, value in row.items() if key != 'assessment'} for row in actions}
    pending = [row for row in members.values() if row['action'] != 'delivery_verified']
    compact['more_actions'] = max(0, len(pending) - 8)
    compact['state_file'] = str(spec_path.resolve())
    return checkpoint_output(compact, since, scope=f"delivery:{spec_path.resolve()}:{result['requirement_id']}",
                             directory=spec_path.resolve().parent, always=('code', 'complete'),
                             collections={'member_changes': members},
                             snapshot={'next_actions': pending[:8]})


def _frozen_action(identity, publication, evidence, requirement, base, unknown):
    def action(name, reason):
        return {**identity, 'action': name, 'reason': reason, 'frozen': True,
                'upload_operation_id': evidence['operation_id']}

    if publication.get('resource_id') not in (None, evidence['resource_id']) and evidence['resource_id']:
        return action('reconcile_frozen', 'Saved resource differs from the official upload receipt')
    observed = {**publication, 'resource_id': evidence['resource_id'] or publication.get('resource_id')}
    for field, name in [('material_id', 'import'), ('attribution_receipt', 'attribute'), ('binding_receipt', 'bind')]:
        if not observed.get(field):
            return action('await_official_result' if unknown else name,
                          f'Resume the frozen resource at {field}')
    fresh = observed.get('fresh_verification')
    if isinstance(fresh, dict):
        try:
            if _fresh(fresh, observed, requirement, identity, (base / evidence['path']).resolve(),
                      base, evidence['sha256']):
                return action('delivery_verified', 'Current readback matches the original reviewed upload')
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return action('verify', 'Read back the frozen resource, binding and downloaded bytes')


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
        if not scored or scored['status'] != 'ready_for_submission' or row['action'] not in ('upload', 'import'):
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


def _next_member(item, record, requirement, sources, base, unknown, output_hash=None):
    identity = {key: item[key] for key in MEMBER_FIELDS}

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
    _require(output_hash is None or review['artifact']['sha256'] == output_hash, 'Artifact changed')
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
    publication = record.get('publication', {})
    for key, name in [('upload_receipt', 'upload'), ('material_id', 'import'),
                      ('attribution_receipt', 'attribute'), ('binding_receipt', 'bind')]:
        if not publication.get(key):
            if unknown:
                return action('await_official_result', 'Resolve the unknown write or use verified member isolation for a disjoint operation')
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
        _require(isinstance(requirement.get(key), list)
                 and all(isinstance(value, str) and value for value in requirement[key]), 'Expected ownership IDs required')
        _require(isinstance(fresh.get(key), list)
                 and all(isinstance(value, str) and value for value in fresh[key]), 'Fresh ownership IDs must be arrays')
        _require(set(fresh[key]) == set(requirement[key]), 'Attribution/shared department mismatch')
    _require(bool(requirement['shared_dept_ids']), 'Shared department required')
    _require(not set(requirement['designer_ids']) & {'c74b62d9', 'b2fbcgab'},
             '罗晨钧 and 邹彬 are not eligible designers')
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
    return downloaded != artifact and sha256(downloaded) == sha
