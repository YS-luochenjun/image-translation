"""Summarize observed upload timings without issuing platform operations."""
from collections import Counter, defaultdict
from datetime import datetime
import json
from statistics import mean, median

from .delivery_autopilot import PRESIGN_UPLOAD, UPLOAD_TOOLS, _publication_response, _upload_values
from .fireplay import payload


def _interval(start, end):
    try:
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if first.tzinfo is not None and last.tzinfo is not None and first <= last:
            return first, last
    except (TypeError, ValueError):
        pass
    return None


def _stats(intervals):
    seconds = [(end - start).total_seconds() for start, end in intervals]
    return {'samples': len(seconds), 'mean_seconds': round(mean(seconds), 3) if seconds else None,
            'median_seconds': round(median(seconds), 3) if seconds else None}


def _outcome(folder, entry):
    if entry['state'] != 'received':
        return 'unknown', {}
    try:
        response = _publication_response(folder, entry)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'invalid_receipt', {}
    if isinstance(response, dict) and (response.get('isError') or response.get('success') is False or response.get('error')):
        return 'failed', {}
    try:
        result = payload(response)
    except (ValueError, KeyError, TypeError):
        return 'unclassified_receipt', {}
    if result.get('success') is False or result.get('error'):
        return 'failed', {}
    resource_ids, urls = _upload_values(result)
    if resource_ids and urls:
        return 'uploaded', result
    return 'unclassified_receipt', result


def publication_timing(root):
    production = root.resolve(strict=True) / 'work' / 'fireplay-production'
    if not production.is_dir():
        raise ValueError('Project has no Fireplay production directory')
    journals = sorted(production.glob('*/publication-journal.jsonl'))
    base64_outcomes, presign_outcomes = Counter(), Counter()
    base64_receipts, puts = [], []
    batches = {}
    for journal in journals:
        if journal.is_symlink() or journal.parent.is_symlink():
            continue
        first, current = {}, {}
        with journal.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get('tool') in UPLOAD_TOOLS:
                    operation = entry['operation_id']
                    first.setdefault(operation, entry)
                    current[operation] = entry
        for operation, entry in current.items():
            outcome, result = _outcome(journal.parent, entry)
            if entry['tool'] != PRESIGN_UPLOAD:
                base64_outcomes[outcome] += 1
                if (first[operation]['state'] == 'unknown' and entry['state'] == 'received'
                        and outcome != 'invalid_receipt'
                        and (timing := _interval(first[operation].get('at'), entry.get('at')))):
                    base64_receipts.append(timing)
                continue
            presign_outcomes[outcome] += 1
            batch = batches.setdefault((journal.parent.name, entry.get('batch_id') or operation),
                                       {'outcomes': Counter(), 'presign': set(), 'puts': [], 'untimed_puts': 0})
            batch['outcomes'][outcome] += 1
            if outcome == 'invalid_receipt':
                batch['untimed_puts'] += 1
                continue
            presign = entry.get('presign_timing', {})
            if timing := _interval(presign.get('started_at'), presign.get('finished_at')):
                batch['presign'].add(timing)
            transfer = result.get('transfer') or {}
            timing = (_interval(transfer.get('started_at'), transfer.get('finished_at'))
                      if transfer.get('method') == 'PUT'
                      else _interval(entry.get('put_transfer_started_at'), entry.get('put_finished_at')))
            if timing:
                batch['puts'].append(timing)
                puts.append(timing)
            elif entry.get('put_started_at'):
                batch['untimed_puts'] += 1
    spans = []
    batch_states = defaultdict(int)
    for batch in batches.values():
        outcomes = batch['outcomes']
        state = ('unknown' if outcomes['unknown'] or outcomes['invalid_receipt'] or outcomes['unclassified_receipt']
                 else 'mixed' if outcomes['uploaded'] and outcomes['failed']
                 else 'uploaded' if outcomes['uploaded'] else 'failed')
        batch_states[state] += 1
        if batch['puts'] and not batch['untimed_puts'] and not outcomes['unknown']:
            spans.append((min(start for start, _ in batch['puts']), max(end for _, end in batch['puts'])))
    return {'journals_found': len(journals),
            'base64': {'outcomes': dict(base64_outcomes), 'intent_to_receipt': _stats(base64_receipts)},
            'presigned': {'member_outcomes': dict(presign_outcomes), 'batch_outcomes': dict(batch_states),
                          'presign_call': _stats([next(iter(batch['presign'])) for batch in batches.values()
                                                  if len(batch['presign']) == 1]),
                          'put_attempt': _stats(puts), 'fully_timed_batch_put_span': _stats(spans)}}
