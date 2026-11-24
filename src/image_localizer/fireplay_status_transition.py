"""Prepare and reconcile a single host-operated Fireplay status write."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .delivery_autopilot import next_delivery
from .fireplay import _exact, _hash, _lock, _require, _write, load_record, payload, request_folder
from .fireplay_capacity import Capacity, monitor_directory
from .pipeline import read_json


TRANSITIONS = {'start': ('not_started', 'in_progress'), 'finish': ('in_progress', 'done')}


def _time(value):
    _require(isinstance(value, str), 'Timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _require(result.tzinfo is not None, 'Timestamp needs a timezone')
    return result


def _observe(snapshot, view, code, requirement_id, now):
    response = payload(snapshot[view])
    _require(response.get('view') == view and response.get('scope') == 'all' and
             isinstance(response.get('items'), list) and len(response['items']) == 1,
             f'{view} must be a fresh exact-UUID observation')
    row = _exact(response, code)
    observed = _time(response.get('serverTime'))
    _require(row['id'] == requirement_id and _time(row.get('updatedAt')) <= observed
             and 0 <= (now - observed).total_seconds() <= 1800, f'Stale or unrelated {view} observation')
    return row, observed


def status_transition(root: Path, code: str, action: str, snapshot: dict, *,
                      prepare: bool = False, reconcile: bool = False,
                      delivery_state: Path | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    before, target = TRANSITIONS[action]
    folder = request_folder(code, root)
    with _lock(monitor_directory(root)), _lock(folder):
        record = load_record(code, root)
        requirement_id = record['requirement_id']
        row, observed = _observe(snapshot, 'summary', code, requirement_id, now)
        brief, brief_observed = _observe(snapshot, 'blind_brief', code, requirement_id, now)
        _require(row['updatedAt'] == brief['updatedAt'] and row['status'] == brief['status'],
                 'Requirement changed between the two reads')
        path = folder / f'status-{action}.json'
        prior = read_json(path) if path.exists() else None
        result = {'code': code, 'requirement_id': requirement_id, 'status': row['status'],
                  'target_status': target}
        if prior:
            _require(prior['requirement_id'] == requirement_id, 'Status operation belongs to another requirement')
            result['operation_id'] = prior['operation_id']
            if prior['state'] == 'confirmed':
                state = 'confirmed' if row['status'] == target else 'manual_review'
                if action == 'finish' and state == 'confirmed':
                    Capacity(root).release(requirement_id, code)
                return {**result, 'state': state}
            if reconcile:
                _require(min(observed, brief_observed) >= _time(prior['prepared_at']),
                         'Read back the status after the original intent')
                _write(folder / f'status-readback-{_hash(snapshot)}.json', snapshot, immutable=True)
                if row['status'] == target and _time(row['updatedAt']) > _time(prior['base_updated_at']):
                    _write(path, {**prior, 'state': 'confirmed', 'observed_updated_at': row['updatedAt'],
                                  'readback_at': observed.isoformat()})
                    if action == 'finish':
                        Capacity(root).release(requirement_id, code)
                    return {**result, 'state': 'confirmed'}
            return {**result, 'state': 'reconcile_required'}
        _require(not reconcile, 'No local status operation to reconcile')
        if row['status'] != before:
            return {**result, 'state': 'manual_review'}
        options = payload(snapshot['options'])
        modes = [item['value'] for item in options.get('fixed', {}).get('aiModes', [])
                 if item.get('label') == '图片翻译' and item.get('enabled') is True]
        _require(len(modes) == 1 and row.get('aiMode') == modes[0]
                 and row.get('assetType') == brief.get('assetType') in ('image', 'button')
                 and {before, target} <= set(options.get('configured', {}).get('status', [])),
                 'Need current official image translation mode and both configured statuses')
        if row.get('canUpdateStatus') is not True:
            return {**result, 'state': 'needs_status_permission'}
        assignments_path = root / 'work/fireplay-monitor/assignments.json'
        assignment = (read_json(assignments_path)['requirements'].get(requirement_id, {})
                      if assignments_path.exists() else {})
        _require(not assignment.get('unknown_operation') and not assignment.get('pending_operation'),
                 'Reconcile the existing requirement operation first')
        if action == 'start':
            _require(record['state'] == 'accepted' and record['updatedAt'] == row['updatedAt'],
                     'Register and accept the current requirement version before starting')
            _require(not assignment.get('responsible_thread_id') and
                     assignment.get('state') not in ('dispatched', 'dispatch_unknown', 'unknown'),
                     'An existing or unresolved owner must be reconciled before starting')
            capacity = Capacity(root)
            if not capacity.available(requirement_id):
                return {**result, 'state': 'queued_capacity', 'capacity': capacity.summary()}
            if prepare:
                capacity.reserve(requirement_id, code)
        else:
            _require(record['state'] == 'accepted' and record['updatedAt'] == row['updatedAt'],
                     'Register the refreshed brief and resolve content revisions before closing')
            start_path = folder / 'status-start.json'
            started = read_json(start_path) if start_path.exists() else {}
            _require((started.get('state') == 'confirmed' and started.get('requirement_id') == requirement_id)
                     or bool(assignment.get('responsible_thread_id')), 'Reconcile the responsible session before finishing')
            _require(delivery_state is not None, 'Finish requires the actual delivery-state JSON')
            production = (root / 'work/fireplay-production' / code).resolve()
            _require(delivery_state.resolve().parent == production,
                     'Finish needs the delivery state beside this requirement’s publication journal')
            _require(read_json(delivery_state).get('content_version') == record['content_version'],
                     'Delivery state must cover the currently accepted requirement content version')
            delivery_row, _ = _observe(snapshot, 'delivery', code, requirement_id, now)
            _require(delivery_row.get('updatedAt') == row['updatedAt'] and delivery_row.get('status') == before,
                     'Delivery view changed before status write')
            report = next_delivery(delivery_state)
            _require(report['requirement_id'] == requirement_id and report['code'] == code and
                     report['complete'] and not report['publication_unknown'], 'All members need fresh verified delivery')
            _require(isinstance(delivery_row.get('materials'), list) and
                     (delivery_row.get('materialSelection') or {}).get('status') != 'waiting_selection',
                     'Need the current visible bound materials')
            verified_materials = set(report['verified_material_ids'])
            _require(verified_materials and verified_materials <= {item.get('id') for item in delivery_row['materials']},
                     'Fresh Fireplay binding does not include every verified material')
            result['verified_members'] = report['verified_members']
        if not prepare:
            return {**result, 'state': 'eligible'}
        operation = {**result, 'state': 'reconcile_required', 'operation_id': str(uuid4()),
                     'base_updated_at': row['updatedAt'], 'prepared_at': now.isoformat(),
                     'content_version': record['content_version']}
        _write(folder / f"status-input-{operation['operation_id']}.json", snapshot, immutable=True)
        _write(path, operation, immutable=True)
        return {**operation, 'host_action': {'tool': 'requirement_update_status', 'arguments': {
            'items': [{'requirement_id': requirement_id, 'status': target, 'base_updated_at': row['updatedAt']}]}}}
