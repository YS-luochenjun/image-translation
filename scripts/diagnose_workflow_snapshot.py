"""Read-only workflow snapshot timing with a hard deadline; never ingests."""
import argparse
import cProfile
import faulthandler
import json
import os
import pstats
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import ReviewDesk
parser = argparse.ArgumentParser()
parser.add_argument('--deadline', type=float, default=20)
parser.add_argument('--rounds', type=int, default=1)
parser.add_argument('--profile', action='store_true')
parser.add_argument('--ledger-stats', action='store_true')
parser.add_argument('--max-bytes', type=int, default=8 * 1024 * 1024)
parser.add_argument('--warm-deadline', type=float, default=3)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
flow = ReviewDesk(root).workflow
ledger = root / '.review-desk/workflows/ledger.jsonl'
print(json.dumps({'ledger_bytes': ledger.stat().st_size if ledger.exists() else 0}), flush=True)
if args.ledger_stats:
    from collections import Counter
    records = flow._records()
    counts, sizes = Counter(), Counter()
    for item in records:
        kind = item.get('kind', 'unknown')
        counts[kind] += 1
        sizes[kind] += len(json.dumps(item, ensure_ascii=False).encode('utf-8')) + 1
    sessions, bindings, events = flow._state(records)
    retained = [item for item in records if item.get('kind') not in ('session', 'activity')]
    for session in sessions.values():
        retained.append({key: value for key, value in session.items() if key not in ('activities', 'plan')})
        activities = {item['event_id']: item for item in session.get('activities', [])}
        plan = session.get('plan')
        if plan and plan['event_id'] not in activities:
            activities[plan['event_id']] = dict(plan, type='plan')
        retained.extend({'kind': 'activity', 'session_id': session['session_id'], 'activity': item} for item in activities.values())
    print(json.dumps({'counts': counts, 'bytes': sizes, 'sessions': len(sessions), 'bindings': len(bindings), 'events': len(events), 'compacted_bytes_estimate': sum(len(json.dumps(item, ensure_ascii=False).encode('utf-8')) + 1 for item in retained)}), flush=True)
    sys.exit(0)
profile = cProfile.Profile()
for method in ('_records', '_inventory', '_cache_fingerprint', '_link_compares'):
    original = getattr(flow, method)
    def measured(*values, _original=original, _name=method, **keywords):
        begin = time.perf_counter()
        value = _original(*values, **keywords)
        print(json.dumps({'phase': _name, 'seconds': time.perf_counter() - begin, 'items': len(value) if hasattr(value, '__len__') else None}), flush=True)
        return value
    setattr(flow, method, measured)
def expired():
    print('FAIL: snapshot deadline exceeded', flush=True)
    faulthandler.dump_traceback()
    os._exit(2)
for index in range(args.rounds):
    timer = threading.Timer(args.deadline if index == 0 else args.warm_deadline, expired)
    timer.daemon = True
    timer.start()
    started = time.perf_counter()
    try:
        if args.profile:
            profile.enable()
        result = flow.snapshot()
        profile.disable()
        response_bytes = len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        print(json.dumps({'round': index + 1, 'seconds': time.perf_counter() - started, 'response_bytes': response_bytes, 'sessions': len(result['sessions']), 'runs': sum(len(s['runs']) for s in result['sessions']), 'unassigned': len(result['unassigned']), 'warnings': result['warnings'][:5]}), flush=True)
        if response_bytes > args.max_bytes:
            raise SystemExit('FAIL: snapshot exceeds Host JSON response budget')
    finally:
        timer.cancel()
if args.profile:
    pstats.Stats(profile).strip_dirs().sort_stats('cumtime').print_stats(25)
