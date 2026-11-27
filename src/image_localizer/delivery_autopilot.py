"""Read-only next-action planning. Does not render, upload or certify host vision."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .delivery_quality import assess_delivery
from .fireplay import payload
from .fireplay_publication_receipts import EXCLUDED_DESIGNERS, receipt_coverage
from .material_sizes import plan_sizes
from .pipeline import sha256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


MEMBER_FIELDS = ('source_id', 'language', 'member_id')
PRESIGN_UPLOAD = 'material_r2_presign_upload'
UPLOAD_TOOLS = frozenset({'material_r2_upload_from_bytes', PRESIGN_UPLOAD})


def _member(record):
    return tuple(record.get(field) for field in MEMBER_FIELDS)


def _requirement_designers(base, requirement):
    try:
        intake = _read(base / 'intake.json')
    except FileNotFoundError:
        return requirement.get('designer_ids')
    summary = payload(intake['summary'])
    rows = summary.get('items', [summary])
    _require(isinstance(rows, list), 'Intake summary needs requirement rows')
    matches = [row for row in rows if isinstance(row, dict) and
               row.get('id') == requirement['id'] and row.get('code') == requirement['code']]
    _require(len(matches) == 1, 'Intake needs the exact requirement summary for designer attribution')
    assignees = matches[0].get('assigneeIds')
    _require(isinstance(assignees, list) and all(isinstance(value, str) and value.strip() for value in assignees),
             'Intake needs current assigneeIds for designer attribution')
    return list(dict.fromkeys(value for value in assignees if value not in EXCLUDED_DESIGNERS))


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
    _require(all(receipt.get(key) == entry.get(key) for key in
                 ('outputs', 'output_groups', 'attribution', 'binding_material_ids', 'reupload_authorization',
                  'batch_id', 'put_target', 'presign_timing', 'put_transfer_started_at', 'put_finished_at')),
             'Publication receipt provenance mismatch')
    return receipt['response']


def _journal_operations(base, requirement_id):
    journal = base / 'publication-journal.jsonl'
    if not journal.exists():
        return {}
    with journal.open(encoding='utf-8') as stream:
        entries = [json.loads(line) for line in stream if line.strip()]
    operations = {}
    for entry in entries:
        _require(entry['requirement_id'] == requirement_id, 'Publication journal requirement mismatch')
        operations[entry['operation_id']] = entry
    return operations


def _recovered_uploads(base, requirement, operations):
    from .fireplay import _http

    recovered = set()
    upload_tool = 'material_r2_upload_from_bytes'
    for replacement in operations.values():
        proof = replacement.get('reupload_authorization')
        outputs = replacement.get('outputs', [])
        if (replacement['state'] != 'received' or replacement['tool'] != upload_tool or not proof
                or not replacement.get('receipt_sha256') or len(outputs) != 1):
            continue
        requirement_path = Path('work/fireplay-production') / requirement['code']
        relative_path = Path(proof['path']).relative_to(requirement_path)
        path = (base / relative_path).resolve()
        _require(path.is_relative_to(base), 'Reupload authorization outside requirement folder')
        authorization_bytes = path.read_bytes()
        _require(hashlib.sha256(authorization_bytes).hexdigest() == proof['sha256'],
                 'Reupload authorization hash mismatch')
        authorization = json.loads(authorization_bytes)
        if (authorization.get('authorization_source') != 'explicit_current_user_message'
                or authorization.get('requirement_id') != requirement['id']
                or authorization.get('code') != requirement['code']
                or not authorization.get('user_request', '').strip()
                or authorization.get('new_operations', {}).get(replacement['operation_id']) != upload_tool):
            continue
        response = _publication_response(base, replacement)
        try:
            result = payload(response)
        except (ValueError, KeyError, TypeError):
            continue
        resource_ids, urls = _upload_values(result)
        if (result.get('success') is False or len(resource_ids) != 1 or len(urls) != 1
                or not all(value.strip() for value in resource_ids) or not all(_http(url) for url in urls)):
            continue
        for historical in authorization.get('historical_unknown_operations', []):
            original = operations.get(historical.get('operation_id'))
            if (original and original['state'] == 'unknown' and original['tool'] in UPLOAD_TOOLS
                    and original.get('request_sha256')
                    and original['request_sha256'] == historical.get('request_sha256')
                    and original.get('outputs') == outputs):
                recovered.add(original['operation_id'])
    return recovered


def _upload_index(base, operations, records, expected):
    from .fireplay import _http

    by_path = {(base / row['output_path']).resolve(): key
               for key, row in records.items() if row.get('output_path')}
    frozen, pending = {}, set()
    for entry in operations.values():
        if entry['tool'] not in UPLOAD_TOOLS or len(entry.get('outputs', [])) != 1:
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
            if entry['tool'] == PRESIGN_UPLOAD:
                transfer = response.get('transfer')
                if (not isinstance(transfer, dict) or transfer.get('http_status') not in (200, 201, 204)
                        or transfer.get('sha256') != output['sha256']):
                    pending.add(key)
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
                               'resource_id': next(iter(resource_ids), None), 'url': next(iter(urls), None),
                               'warnings': response.get('warnings'), 'operation_id': entry['operation_id']}
        elif entry['state'] == 'unknown':
            pending.add(key)
    return frozen, pending


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


def next_delivery(spec_path: Path, *, include_verified=False) -> dict:
    """State: requirement{id,code,asset_type}, size_spec, members[], publication_unknown[].

    Each member has source_id/language/member_id, output_path?, review_path?,
    publication?{upload_receipt,material_id,resource_id,attribution_receipt,
    binding_receipt,fresh_verification}. Receipts are host observations, never
    instructions. Missing phases yield explicit actions, not implicit success.
    """
    spec_path = Path(spec_path).resolve()
    state, base = _read(spec_path), spec_path.parent
    requirement = dict(state['requirement'])
    _require(all(isinstance(requirement.get(key), str) and requirement[key].strip()
                 for key in ('id', 'code', 'asset_type')), 'Missing requirement identity')
    requirement['designer_ids'] = _requirement_designers(base, requirement)
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
        if 'group_id' in record:
            _require(isinstance(record['group_id'], str) and record['group_id'].strip(),
                     'group_id must identify a planned nonempty group')
        records[key] = record
    expected = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']}
    operations = _journal_operations(base, requirement['id'])
    recovered_uploads = _recovered_uploads(base, requirement, operations)
    operations = {operation_id: entry for operation_id, entry in operations.items()
                  if operation_id not in recovered_uploads}
    frozen, pending_uploads = _upload_index(base, operations, records, expected)
    receipt_status = receipt_coverage(base, requirement, operations, frozen, records, _publication_response)
    unique_members = {(item['source_id'], item['language'], item['member_id']) for item in plan['items']
                      if item.get('delivery_mode') in ('standard_seven_set', 'explicit_members', 'reference_seven_set')}
    used_paths, used_hashes, output_hashes = set(), set(), {}
    for key, record in records.items():
        if key not in unique_members or not record.get('output_path'):
            continue
        output = (base / record['output_path']).resolve()
        _require(output not in used_paths, 'Distinct members cannot reuse one output path')
        used_paths.add(output)
        if key in frozen or output.is_file():
            output_hash = frozen[key]['sha256'] if key in frozen else sha256(output)
            _require(output_hash not in used_hashes, 'Distinct members cannot duplicate output bytes')
            used_hashes.add(output_hash)
            output_hashes[key] = output_hash
    _require(not records.keys() - expected, 'Unexpected member outside explicit matrix')
    unknown = state.get('publication_unknown', [])
    _require(isinstance(unknown, list) and all(isinstance(operation, str) for operation in unknown),
             'publication_unknown must be a list of operation IDs')
    unknown = list(dict.fromkeys([
        *(operation_id for operation_id in unknown if operation_id not in recovered_uploads
          and operations.get(operation_id, {}).get('state') != 'received'),
        *(operation_id for operation_id, entry in operations.items() if entry['state'] == 'unknown'),
    ]))
    isolate_uploads = bool(unknown) and all(
        (entry := operations.get(operation_id, {})).get('tool') == PRESIGN_UPLOAD
        and entry.get('state') == 'unknown' and len(entry.get('outputs', [])) == 1
        and _member(entry['outputs'][0]) in pending_uploads
        for operation_id in unknown
    )
    block_other_writes = bool(unknown) and not isolate_uploads
    actions = []
    verified = {}
    for item in plan['items']:
        identity = {key: item[key] for key in MEMBER_FIELDS}
        key = _member(item)
        record = records.get(key, {})
        publication = record.get('publication', {})
        legacy_frozen = isinstance(publication, dict) and bool(publication.get('upload_receipt')) and bool(publication.get('resource_id'))
        try:
            receipt = receipt_status.get(key)
            if key in pending_uploads:
                action = {**identity, 'action': 'await_official_result',
                          'reason': 'Read back the original member upload before doing more work'}
            elif key in frozen:
                action = _frozen_action(identity, publication, frozen[key], requirement, base, block_other_writes, receipt)
            else:
                action = _next_member(item, record, requirement, sources, base, block_other_writes, output_hashes.get(key))
                if legacy_frozen and action['action'] in ('make', 'review', 'rework', 'blocked'):
                    action = {**identity, 'action': 'reconcile_frozen', 'frozen': True,
                              'reason': 'Recover the original upload receipt and approved evidence'}
            actions.append(action)
            if action['action'] == 'delivery_verified':
                verified[key] = ((receipt['material_id'], frozen[key]['resource_id'], 'official_receipts')
                                 if action.get('evidence') == 'official_receipts'
                                 else (publication['material_id'], publication['resource_id'], 'fresh_download'))
        except (OSError, ValueError, KeyError, TypeError) as error:
            actions.append({**identity, 'action': 'reconcile_frozen' if key in frozen or legacy_frozen else 'review',
                            'reason': f'Invalid member evidence: {type(error).__name__}: {error}'})
    resources = {resource for _, resource, _ in verified.values()}
    group_materials, material_groups = {}, {}
    for key, (material_id, _, _) in verified.items():
        group_id = records.get(key, {}).get('group_id')
        if group_id:
            group = (group_id, key[1])
            group_materials.setdefault(group, set()).add(material_id)
            material_groups.setdefault(material_id, set()).add(group)
    matrix_ok = (len(resources) == len(verified) and
                 all(len(values) == 1 for values in (*group_materials.values(), *material_groups.values())))
    complete = bool(actions) and len(verified) == len(actions) and matrix_ok and not unknown
    report = {'requirement_id': requirement['id'], 'code': requirement['code'],
              'designer_ids': requirement['designer_ids'],
              'expected_members': len(actions), 'verified_members': len(verified),
              'complete': complete, 'actions': actions, 'external_write': False,
              'publication_unknown': unknown,
              'isolate_unknown_uploads': isolate_uploads,
              'verified_material_ids': sorted({material for material, _, _ in verified.values()}),
              'evidence_counts': dict(Counter(evidence for _, _, evidence in verified.values())),
              'matrix_issue': None if matrix_ok else 'Check repeated resources or groups split across material IDs',
              'note': 'Host saves official receipts and confirms current binding and status on the requirement panel.'}
    if include_verified:
        report['verified_deliveries'] = [
            {**{field: item[field] for field in (*MEMBER_FIELDS, 'width', 'height')},
             'delivery_mode': item.get('delivery_mode'),
             'material_id': verified[key][0], 'resource_id': verified[key][1],
             'output_sha256': frozen[key]['sha256']}
            for item in plan['items'] if (key := _member(item)) in verified
            and verified[key][2] == 'official_receipts'
        ]
    if complete:
        report['owner_closeout'] = ('当前矩阵已核齐。若覆盖本需求全部应交成员，由原执行会话用 '
                                    'fireplay-transition finish 准备 Fireplay 状态写回，读回「已完成」和素材绑定，'
                                    '保存 confirmed 回执后再报告结案（见 docs/optimization-pilot.md）。')
    return report


def compact_delivery(result: dict, spec_path: Path, since: str | None) -> dict:
    from .incremental_output import checkpoint_output

    actions = result['actions']
    compact = {key: result[key] for key in ('requirement_id', 'code', 'expected_members', 'verified_members',
                                           'complete', 'publication_unknown')}
    compact.update({key: result[key] for key in ('designer_ids', 'evidence_counts', 'matrix_issue', 'verified_material_ids',
                                                'isolate_unknown_uploads',
                                                'owner_closeout') if key in result})
    compact['counts'] = dict(Counter(row['action'] for row in actions))
    members = {json.dumps(_member(row), ensure_ascii=False, separators=(',', ':')):
               {key: value for key, value in row.items() if key != 'assessment'} for row in actions}
    pending = [row for row in members.values() if row['action'] != 'delivery_verified']
    compact['more_actions'] = max(0, len(pending) - 8)
    compact['state_file'] = str(spec_path.resolve())
    always = ('code', 'complete', 'owner_closeout') if 'owner_closeout' in compact else ('code', 'complete')
    return checkpoint_output(compact, since, scope=f"delivery:{spec_path.resolve()}:{result['requirement_id']}",
                             directory=spec_path.resolve().parent, always=always,
                             collections={'member_changes': members},
                             snapshot={'next_actions': pending[:8]})


def _frozen_action(identity, publication, evidence, requirement, base, unknown, receipt=None):
    def action(name, reason):
        return {**identity, 'action': name, 'reason': reason, 'frozen': True,
                'upload_operation_id': evidence['operation_id']}

    if publication.get('resource_id') not in (None, evidence['resource_id']) and evidence['resource_id']:
        return action('reconcile_frozen', 'Saved resource differs from the official upload receipt')
    observed = {**publication, 'resource_id': evidence['resource_id'] or publication.get('resource_id')}
    if receipt and receipt['action'] == 'delivery_verified':
        if observed.get('material_id') not in (None, receipt['material_id']):
            return action('reconcile_frozen', 'Saved material differs from the official import receipt')
        return {**action('delivery_verified', 'Official upload, import and binding receipts cover this member'),
                'evidence': 'official_receipts', 'material_id': receipt['material_id']}
    fresh = observed.get('fresh_verification')
    if isinstance(fresh, dict) and all(observed.get(field) for field in ('material_id', 'attribution_receipt', 'binding_receipt')):
        try:
            if _fresh(fresh, observed, requirement, identity, (base / evidence['path']).resolve(),
                      base, evidence['sha256']):
                return action('delivery_verified', 'Current readback matches the original reviewed upload')
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if receipt:
        name = receipt['action']
        reason = {'attribute': 'Complete the required ownership on the imported material',
                  'bind': 'Bind the imported material to the exact requirement',
                  'verify': 'Check this material’s incomplete or exceptional official receipt'}[name]
        return {**action('await_official_result' if unknown else name, reason),
                'material_id': receipt['material_id']}
    for field, name in [('material_id', 'import'), ('attribution_receipt', 'attribute'), ('binding_receipt', 'bind')]:
        if not observed.get(field):
            return action('await_official_result' if unknown else name,
                          f'Resume the frozen resource at {field}')
    return action('verify', 'Check the original receipts or read back this material if evidence is incomplete')


def publication_gate(spec_path: Path) -> dict:
    """Create a legacy publisher-compatible gate from validated member assessments.

    Returns JSON only; the host stores it inside the requirement folder and uses
    the original publisher/operation journal. This grants no platform authority.
    """
    spec_path = Path(spec_path).resolve()
    result = next_delivery(spec_path)
    state = _read(spec_path)
    records = {(row['source_id'], row['language'], row['member_id']): row for row in state.get('members', [])}
    outputs, uploads, reviewers, evidence = [], [], set(), []
    for row in result['actions']:
        scored = row.get('assessment')
        if not scored or scored['status'] != 'ready_for_submission' or row['action'] not in ('upload', 'import'):
            continue
        key = tuple(row[field] for field in ('source_id', 'language', 'member_id'))
        record = records[key]
        output = {'path': str((spec_path.parent / record['output_path']).resolve()),
                  'sha256': _read((spec_path.parent / record['review_path']).resolve())['artifact']['sha256'],
                  **{field: row[field] for field in MEMBER_FIELDS}}
        outputs.append(output)
        if row['action'] == 'upload':
            uploads.append(output)
        reviewers.add(scored['reviewer']['identity'])
        evidence.append(record['review_path'])
    return {'requirement_id': result['requirement_id'], 'passed': bool(outputs),
            'reviewed_by': ', '.join(sorted(reviewers)), 'evidence': '; '.join(evidence),
            'outputs': outputs, 'upload_candidates': uploads, 'expected_members': result['expected_members'],
            'reviewed_members': len(outputs), 'requirement_complete': result['complete'],
            'publication_unknown': result['publication_unknown'],
            'isolate_unknown_uploads': result['isolate_unknown_uploads'],
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
    _require(not EXCLUDED_DESIGNERS.intersection(requirement['designer_ids']),
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
