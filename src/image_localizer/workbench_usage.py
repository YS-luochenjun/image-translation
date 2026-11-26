"""Report local Codex usage and receipt-backed costs for image requirements."""
from collections import defaultdict
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

from .project_monitor import EvidenceReader
from .stage_events import _validate
from .workbench import CODE, PRODUCTION, count, mapping, optional_record, text

USAGE_TAIL_BYTES = 4 * 1024 * 1024
TOKEN_KEYS = ('total_tokens', 'input_tokens', 'output_tokens', 'cached_input_tokens')


def token_breakdown(home, rollout, thread_id, total):
    if total is None or not isinstance(rollout, str) or not rollout:
        return {}
    try:
        path = Path(rollout).resolve(strict=True)
        if (not path.name.endswith(f'-{thread_id}.jsonl')
                or not any(path.is_relative_to(home / folder) for folder in ('sessions', 'archived_sessions'))
                or not path.is_file()):
            return {}
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            start = max(0, size - USAGE_TAIL_BYTES)
            stream.seek(start)
            raw = stream.read(min(size, USAGE_TAIL_BYTES))
    except (OSError, ValueError, RuntimeError):
        return {}
    lines = raw.splitlines()
    if not raw.endswith(b'\n'):
        lines = lines[:-1]
    for line in reversed(lines[1:] if start else lines):
        if b'"token_count"' not in line:
            continue
        try:
            event = mapping(json.loads(line))
        except (ValueError, RecursionError):
            continue
        payload = mapping(event.get('payload'))
        if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
            continue
        usage = mapping(mapping(payload.get('info')).get('total_token_usage'))
        reported, inputs, outputs, cached = (count(usage.get(key)) for key in TOKEN_KEYS)
        if reported == total and inputs is not None and outputs is not None and inputs + outputs == total:
            return {'input': inputs, 'output': outputs, 'cached_input': cached if cached is not None and cached <= inputs else None}
    return {}


def local_session_usage(root, home, thread_ids):
    database = home / 'state_5.sqlite'
    if not thread_ids or not database.is_file():
        return {}
    result = {}
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
            timestamps = []
            if 'created_at_ms' in columns:
                timestamps.append('created_at_ms')
            if 'created_at' in columns:
                timestamps.append('created_at * 1000')
            created_at = f'COALESCE({", ".join(timestamps)})' if len(timestamps) == 2 else next(iter(timestamps), 'NULL')
            for thread_id in thread_ids:
                row = connection.execute(
                    f'SELECT cwd, tokens_used, rollout_path, {created_at} FROM threads WHERE id = ?', (thread_id,)
                ).fetchone()
                if row and isinstance(row[0], str) and Path(row[0]).is_absolute() and Path(row[0]).resolve() == root:
                    total = count(row[1])
                    result[thread_id] = {
                        'created_at': count(row[3]),
                        'tokens': {'total': total, **token_breakdown(home, row[2], thread_id, total)},
                    }
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        pass
    return result


def requirement_costs(reader, code):
    if not CODE.fullmatch(code):
        return {}
    journal = PRODUCTION / code / 'stage-events.jsonl'
    try:
        entries = reader.history(reader.safe(journal))
    except (OSError, ValueError):
        return {}
    receipts = {}
    for entry in entries:
        if not entry.get('cost'):
            continue
        try:
            event = _validate(entry)
        except (KeyError, ValueError, TypeError):
            continue
        cost = event['cost']
        unit = cost['unit'].strip()
        if unit.casefold().replace('_', ' ') in ('kie credit', 'kie credits'):
            unit = 'KIE Credits'
        elif unit.casefold() == 'hdk':
            unit = 'HDK'
        key = (event['evidence'].strip(), unit)
        amount = Decimal(cost['amount'])
        if key not in receipts:
            receipts[key] = amount
        elif receipts[key] != amount:
            receipts[key] = None
    totals = defaultdict(Decimal)
    for (_, unit), amount in receipts.items():
        if amount is not None:
            totals[unit] += amount
    return totals


def generation_cost(reader, code):
    folder = PRODUCTION / code
    summary = optional_record(reader, folder / 'generation-cost-summary.json')
    verified_count = count(summary.get('verified_count'))
    generations = summary.get('verified_generations')
    if not verified_count or not isinstance(generations, list):
        return {}
    ids = [text(mapping(item).get('id')) for item in generations]
    if len(ids) != verified_count or '' in ids or len(set(ids)) != verified_count:
        return {}
    for key in ('audit_receipt', 'pricing_receipt'):
        try:
            receipt = reader.safe(text(summary.get(key)), reader.root / folder)
            if not receipt.is_relative_to(reader.root / folder) or not receipt.is_file():
                return {}
        except (OSError, ValueError):
            return {}
    amounts = {}
    for key in ('estimated_hdk', 'settled_hdk'):
        value = summary.get(key)
        if type(value) in (int, float):
            amount = Decimal(str(value))
            if amount.is_finite() and 0 <= amount <= 1_000_000_000:
                amounts[key] = amount
    return {'verified_count': verified_count, **amounts}


def session_usage(root, requirements, codex_home=None):
    home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().resolve()
    bindings = defaultdict(list)
    for row in requirements:
        if row['thread_id'] and row['host_id'] in ('', 'local'):
            bindings[row['thread_id']].append(row)
    usage = local_session_usage(root, home, bindings)
    reader = EvidenceReader(root)
    accounting = {}
    for row in requirements:
        code = row['code']
        if not CODE.fullmatch(code):
            continue
        costs = requirement_costs(reader, code)
        generation = generation_cost(reader, code)
        if 'settled_hdk' in generation:
            costs.setdefault('HDK', generation['settled_hdk'])
        thread_id = row['thread_id']
        local = row['host_id'] in ('', 'local')
        linked = len(bindings.get(thread_id, [])) if local else 0
        accounting[code] = {
            'costs': {unit: format(amount, 'f') for unit, amount in costs.items()},
            'estimated_hdk': format(generation['estimated_hdk'], 'f') if 'estimated_hdk' in generation else None,
            'verified_generations': generation.get('verified_count'),
            'tokens': usage.get(thread_id, {}).get('tokens', {}) if linked == 1 else {},
            'shared_session': linked > 1,
        }
    sessions = []
    ordered_threads = sorted(bindings, key=lambda thread_id: usage.get(thread_id, {}).get('created_at') or -1,
                             reverse=True)
    for thread_id in ordered_threads:
        rows = bindings[thread_id]
        costs = defaultdict(Decimal)
        codes = sorted({row['code'] for row in rows})
        for code in codes:
            for unit, amount in accounting.get(code, {}).get('costs', {}).items():
                costs[unit] += Decimal(amount)
        sessions.append({
            'title': next((row['thread_title'] for row in rows if row['thread_title']), ' / '.join(codes)),
            'completed': all(row['delivery_verified'] for row in rows),
            'tokens': usage.get(thread_id, {}).get('tokens', {}),
            'costs': {unit: format(amount, 'f') for unit, amount in costs.items()},
        })
    return {'sessions': sessions, 'requirements': accounting}
