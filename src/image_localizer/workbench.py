"""Read-only projection of this project's production and monitoring records."""
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .fireplay_capacity import Capacity
from .project_monitor import EvidenceReader

MONITOR = Path('work/fireplay-monitor')
PRODUCTION = Path('work/fireplay-production')
INTAKE = Path('state/fireplay')
LOCAL_ZONE = ZoneInfo('America/Los_Angeles')
CODE = re.compile(r'[A-Z][A-Z0-9]{1,15}-(\d{8})-\d{3,6}')
ASSET_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
PLATFORM_DONE = {'done', 'completed'}
PROGRESS_ACTIVE = {'accepted', 'dispatched', 'executing', 'in_progress', 'partial', 'pending_upload', 'running'}
PROGRESS_BLOCKED = {'blocked', 'needs_info', 'unknown', 'dispatch_unknown', 'failed'}


def optional_record(reader, path):
    try:
        path = reader.safe(path)
        return reader.record(path) if path.is_file() else {}
    except (ValueError, OSError):
        reader.warn('Unreadable workbench record: ' + reader.relative(reader.root / path))
        return {}


def mapping(value):
    return value if isinstance(value, dict) else {}


def text(value):
    return value.strip() if isinstance(value, str) else ''


def count(value):
    return value if type(value) is int and value >= 0 else None


def time_key(value):
    try:
        parsed = datetime.fromisoformat(text(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else datetime.min.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def request_day(code, *values):
    match = CODE.fullmatch(code)
    for value in (match[1] if match else '', *values):
        try:
            return date.fromisoformat(text(value)[:10])
        except ValueError:
            continue
    return None


def requirement_timing(reader, code, identity):
    timestamps = {}
    for action, target in (('start', 'in_progress'), ('finish', 'done')):
        record = optional_record(reader, INTAKE / code / f'status-{action}.json')
        updated = time_key(record.get('observed_updated_at'))
        observed = time_key(record.get('readback_at'))
        if (record.get('state') == 'confirmed' and record.get('code') == code
                and record.get('requirement_id') == identity and record.get('target_status') == target
                and datetime.min.replace(tzinfo=timezone.utc) < updated <= observed):
            timestamps[action] = updated
    start, finish = timestamps.get('start'), timestamps.get('finish')
    return {'started_at': start.isoformat() if start else None, 'closed_at': finish.isoformat() if finish else None,
            'elapsed_seconds': (finish - start).total_seconds() if start and finish and finish >= start else None}


def intake_record(reader, code):
    return optional_record(reader, INTAKE / code / 'request.json')


def source_candidate(reader, code, record, index):
    item = mapping(mapping(record.get('downloads')).get(str(index)))
    if not record.get('content_version') or item.get('content_version') != record['content_version']:
        return None, {}
    fingerprint = text(item.get('sha256'))
    if not re.fullmatch(r'[a-f0-9]{64}', fingerprint) or not text(item.get('local_path')):
        return None, {}
    try:
        path = reader.safe(item['local_path'])
        folder = reader.safe(INTAKE / code / 'sources')
        if path.is_relative_to(folder) and path.suffix.lower() in ASSET_TYPES and path.is_file():
            return path, item
    except (ValueError, OSError):
        pass
    return None, {}


def source_bytes(root, code, index, fingerprint):
    if not CODE.fullmatch(code) or not re.fullmatch(r'[1-9]\d{0,4}', index):
        return None
    reader = EvidenceReader(root)
    path, item = source_candidate(reader, code, intake_record(reader, code), index)
    if path is None or item['sha256'] != fingerprint:
        return None
    try:
        data = reader.read(path, maximum=40 * 1024 * 1024, hashing=True)
    except (ValueError, OSError):
        return None
    if hashlib.sha256(data).hexdigest() != fingerprint:
        return None
    return ASSET_TYPES[path.suffix.lower()], data


def requirement(reader, code, identity, record, assignment, summary, admission, today):
    plan = optional_record(reader, PRODUCTION / code / 'size-plan.json')
    confirmed = optional_record(reader, PRODUCTION / code / 'start-confirmed.json')
    if confirmed.get('requirement_id') != record.get('requirement_id', assignment.get('requirement_id')):
        confirmed = {}
    observations = [(summary.get('updated_at'), summary.get('status')),
                    (record.get('updatedAt'), record.get('platform_status')),
                    (assignment.get('platform_updated_at'), assignment.get('platform_status'))]
    observations = [item for item in observations if text(item[1])]
    updated, platform = max(reversed(observations), default=(None, None), key=lambda item: time_key(item[0]))
    if (confirmed.get('state') == 'confirmed' and confirmed.get('status') == 'in_progress'
            and platform not in PLATFORM_DONE | {'cancelled', 'canceled', 'pending_upload'}
            and not text(assignment.get('platform_status'))):
        platform = 'in_progress'
    state = text(assignment.get('state')) or text(record.get('state'))
    unresolved = bool(assignment.get('unknown_operation') or assignment.get('pending_operation')
                      or state in ('unknown', 'dispatch_unknown'))
    complete = not unresolved and (assignment.get('delivery_verified_by_controller') is True
                                   or state in ('verified_complete', 'delivery_verified'))
    blockers = record.get('blockers')
    blockers = [entry for entry in blockers if isinstance(entry, str)] if isinstance(blockers, list) else []
    if complete or platform in PLATFORM_DONE:
        progress = 'done'
    elif platform in ('cancelled', 'canceled') or state in ('excluded_closed', 'excluded_out_of_scope', 'excluded_user_instruction'):
        progress = 'closed'
    elif unresolved or blockers or state in PROGRESS_BLOCKED:
        progress = 'blocked'
    elif state in PROGRESS_ACTIVE or platform in ('in_progress', 'pending_upload'):
        progress = 'active'
    else:
        progress = 'waiting'
    day = request_day(code, assignment.get('request_date'), summary.get('requestDate'))
    archived = complete or bool(day and day < today - timedelta(days=3)) or progress == 'closed'
    locales = assignment.get('target_locales', record.get('targets'))
    locales = list(dict.fromkeys(item for item in locales if isinstance(item, str))) if isinstance(locales, list) else []
    expected = count(assignment.get('expected_members'))
    if expected is None:
        expected = count(plan.get('expected_resource_count'))
    source_list = record.get('sources')
    source_total = count(assignment.get('source_attachment_count'))
    if source_total is None and isinstance(source_list, list):
        source_total = len(source_list)
    thread = text(assignment.get('responsible_thread_id'))
    intent = mapping(assignment.get('dispatch_intent'))
    dispatch = mapping(assignment.get('dispatch_receipt'))
    thread_title = text(assignment.get('responsible_thread_title'))
    if not thread_title and thread and dispatch.get('threadId') == thread:
        thread_title = text(intent.get('title'))

    sources = []
    for index in range(1, min(source_total or 0, 1000) + 1):
        path, source = source_candidate(reader, code, record, index)
        metadata = mapping(source_list[index - 1]) if isinstance(source_list, list) and index <= len(source_list) else {}
        url = '/api/workbench/source?' + urlencode({'code': code, 'index': index, 'sha': source['sha256']}) if path else None
        sources.append({'index': index, 'name': text(metadata.get('name')), 'width': count(source.get('width')),
                        'height': count(source.get('height')), 'url': url})
    return {
        'code': code, 'platform_status': platform, 'platform_updated_at': updated, 'progress': progress,
        'state': state, 'request_date': day.isoformat() if day else None, 'priority': text(record.get('priority') or summary.get('priority')),
        'archived': archived, 'locales': locales, 'source_count': source_total, 'sources': sources,
        'expected': expected, 'verified': count(assignment.get('verified_members')), 'delivery_verified': complete,
        'thread_id': thread, 'thread_title': thread_title, 'host_id': text(assignment.get('host_id') or dispatch.get('hostId')),
        'admission': text(admission.get('state')), 'blockers': blockers, 'operation_pending': unresolved,
        'timing': requirement_timing(reader, code, identity),
    }


def snapshot(root, now=None):
    reader = EvidenceReader(root)
    now = now or datetime.now(timezone.utc)
    assignments = mapping(optional_record(reader, MONITOR / 'assignments.json').get('requirements'))
    admissions = mapping(optional_record(reader, MONITOR / 'admissions.json').get('requirements'))
    discovery = optional_record(reader, MONITOR / 'discovery-state.json')
    controller = optional_record(reader, MONITOR / 'controller.json')
    summaries = mapping(discovery.get('summaries'))
    by_code = {}
    for identity in assignments.keys() | admissions.keys():
        assignment, admission = mapping(assignments.get(identity)), mapping(admissions.get(identity))
        code = text(assignment.get('code') or admission.get('code'))
        if CODE.fullmatch(code):
            by_code.setdefault(code, set()).add(identity)
    try:
        folder = reader.safe(INTAKE)
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if CODE.fullmatch(path.name):
                    by_code.setdefault(path.name, set())
    except (OSError, ValueError):
        reader.warn('无法读取接入目录')
    rows = []
    for code, identities in sorted(by_code.items(), reverse=True):
        record = intake_record(reader, code)
        recorded_id = text(record.get('requirement_id'))
        if len(identities) > 1 or (recorded_id and identities and recorded_id not in identities):
            reader.warn('需求编号与登记身份冲突: ' + code)
            continue
        identity = recorded_id or next(iter(identities), '')
        if not identity:
            continue
        assignment, summary, admission = (mapping(group.get(identity)) for group in (assignments, summaries, admissions))
        rows.append(requirement(reader, code, identity, record, assignment, summary, admission, now.astimezone(LOCAL_ZONE).date()))
    try:
        capacity = Capacity(root).summary() if not any(
            warning.startswith(('Unreadable or unsafe record: work/fireplay-monitor',
                                'Unreadable workbench record: work/fireplay-monitor')) for warning in reader.warnings
        ) else None
    except (ValueError, TypeError, OSError, AttributeError, KeyError):
        capacity = None
    checkpoints = mapping(discovery.get('checkpoints'))
    policy = optional_record(reader, Path('config/fireplay-monitor.json'))
    entries = optional_record(reader, Path('knowledge/index.json')).get('entries')
    knowledge = []
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict) or not text(entry.get('id')):
            continue
        document = read_knowledge_entry(reader, entry)
        if document:
            item = {key: entry.get(key) for key in ('id', 'kind', 'status', 'tags', 'scope', 'path')}
            knowledge.append({**item, 'title': knowledge_title(document, entry['id'])})
    return {'read_at': now.isoformat(), 'requirements': rows, 'capacity': capacity, 'knowledge': knowledge,
            'monitor': {'discovery_interval': count(policy.get('discovery_interval_minutes')),
                        'followup_mode': text(policy.get('followup_mode')),
                        'last_new': text(checkpoints.get('new')), 'last_active': text(checkpoints.get('active')),
                        'last_full': text(checkpoints.get('full')), 'controller_recorded': bool(controller)},
            'warnings': reader.warnings}


def read_knowledge_entry(reader, entry):
    try:
        path = reader.safe(text(entry.get('path')))
        if path.is_relative_to(reader.root / 'knowledge') and path.suffix == '.md':
            return reader.read(path).decode('utf-8-sig')
    except (OSError, ValueError):
        pass
    return None


def knowledge_title(document, identity):
    first_line = document.lstrip().partition('\n')[0]
    return first_line[2:].strip() if first_line.startswith('# ') else identity


def knowledge_document(root, identity):
    reader = EvidenceReader(root)
    entries = optional_record(reader, Path('knowledge/index.json')).get('entries')
    for entry in entries if isinstance(entries, list) else ():
        if mapping(entry).get('id') == identity:
            document = read_knowledge_entry(reader, entry)
            return {'id': identity, 'title': knowledge_title(document, identity), 'content': document} if document else None
    return None
