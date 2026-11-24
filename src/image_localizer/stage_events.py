"""Record observed production intervals and actual provider costs."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path

from .contracts import require


STAGES = ('intake', 'download', 'background', 'lettering', 'composite', 'provider_wait',
          'visual_review', 'rework', 'upload', 'readback')


def _parse_time(value: str) -> datetime:
    require(isinstance(value, str), 'Stage time must be a timestamp')
    when = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(when.utcoffset() is not None, 'Stage timestamps must carry a timezone')
    return when


def _validate(event: dict) -> dict:
    stage = event['stage']
    require(stage in STAGES, 'Unknown production stage')
    duration = (_parse_time(event['finished_at']) - _parse_time(event['started_at'])).total_seconds()
    require(duration >= 0, 'Stage end precedes start')
    require(event['outcome'] in ('completed', 'failed', 'waiting'), 'Invalid outcome')
    evidence = event.get('evidence')
    require(evidence is None or isinstance(evidence, str) and evidence.strip(), 'Invalid evidence reference')
    cost = event.get('cost')
    if cost is not None:
        require(isinstance(cost, dict) and set(cost) == {'amount', 'unit'} and evidence,
                'Actual costs require amount, unit and receipt evidence')
        require(isinstance(cost['amount'], str) and 0 < len(cost['amount']) <= 30 and
                isinstance(cost['unit'], str) and 0 < len(cost['unit'].strip()) <= 32, 'Invalid cost value')
        try:
            amount = Decimal(cost['amount'])
        except InvalidOperation as error:
            raise ValueError('Cost needs a decimal string') from error
        require(amount.is_finite() and 0 <= amount <= 1_000_000_000 and amount.as_tuple().exponent >= -12,
                'Invalid cost amount')
    return {**event, 'duration_seconds': duration}


def record_stage(path: Path, event: dict) -> dict:
    require(set(event) <= {'stage', 'started_at', 'finished_at', 'outcome', 'evidence', 'cost', 'member'},
            'Unknown stage event fields')
    row = _validate(event)
    data = (json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    require(len(data) <= 4096, 'Stage event is too large; reference the original evidence')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        require(os.write(descriptor, data) == len(data), 'Incomplete stage event write')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return row


def summarize_stages(path: Path) -> dict:
    totals = defaultdict(lambda: {'count': 0, 'seconds': 0.0, 'failed': 0})
    costs = defaultdict(Decimal)
    earliest = latest = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            event = _validate(json.loads(line))
            stage = totals[event['stage']]
            stage['count'] += 1
            stage['seconds'] += event['duration_seconds']
            stage['failed'] += event['outcome'] == 'failed'
            start, finish = _parse_time(event['started_at']), _parse_time(event['finished_at'])
            earliest = start if earliest is None else min(earliest, start)
            latest = finish if latest is None else max(latest, finish)
            if cost := event.get('cost'):
                costs[cost['unit']] += Decimal(cost['amount'])
    return {'stages': dict(totals), 'costs': {unit: str(amount) for unit, amount in costs.items()},
            'wall_span_seconds': (latest - earliest).total_seconds() if earliest is not None else 0,
            'timing_scope': 'Stage totals sum recorded intervals, including overlapping work.'}
