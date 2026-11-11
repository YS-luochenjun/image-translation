"""Read-only private workflow HTTP timing. Bootstrap token stays in memory."""
import argparse
from collections import Counter
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import project_identity

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=35104)
parser.add_argument('--timeout', type=float, default=30)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]

def request(method, path, body=None, token=None):
    connection = HTTPConnection('127.0.0.1', args.port, timeout=args.timeout)
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Review-Token'] = token
    started = time.perf_counter()
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(64 * 1024 * 1024 + 1)
        print(json.dumps({'endpoint': path, 'status': response.status, 'seconds': time.perf_counter() - started, 'bytes': len(data)}), flush=True)
        if response.status != 200 or len(data) > 64 * 1024 * 1024:
            raise SystemExit('FAIL: unexpected HTTP result or diagnostic response budget exceeded')
        return json.loads(data)
    finally:
        connection.close()

bootstrap = request('GET', '/api/bootstrap')
if bootstrap.get('project_id') != project_identity(root):
    raise SystemExit('FAIL: private service belongs to another workspace')
token = bootstrap['token']
members = request('GET', '/api/workflow/session-ids', token=token)['session_ids']
if len(members) > 100:
    raise SystemExit('FAIL: more than 100 project sessions require a narrower diagnostic filter')
result = request('POST', '/api/workflow/snapshot', json.dumps({'session_ids': members}).encode(), token)
node_bytes, artifact_bytes = Counter(), Counter()
preview_sizes, captures = Counter(), Counter()
unique_previews = {}
duplicates = 0
run_sizes = []
nodes = artifacts = 0
for session in result['sessions']:
    for run in session['runs']:
        run_sizes.append((run['root'], len(json.dumps(run, ensure_ascii=False).encode())))
        for node in run['nodes']:
            nodes += 1
            identities = [(item.get('id'), item.get('path'), item.get('sha256'), item.get('capture')) for item in node['artifacts']]
            duplicates += len(identities) - len(set(identities))
            for key, value in node.items():
                node_bytes[key] += len(json.dumps(value, ensure_ascii=False).encode())
            for artifact in node['artifacts']:
                artifacts += 1
                captures[artifact.get('capture')] += 1
                size = len(artifact.get('preview', '').encode())
                preview_sizes[size] += 1
                unique_previews[(artifact.get('id'), artifact.get('path'), artifact.get('sha256'))] = size
                for key, value in artifact.items():
                    artifact_bytes[key] += len(json.dumps(value, ensure_ascii=False).encode())
print(json.dumps({'sessions': len(result['sessions']), 'nodes': nodes, 'artifacts': artifacts, 'node_fields': node_bytes.most_common(10), 'artifact_fields': artifact_bytes.most_common(10), 'captures': captures, 'preview_sizes': preview_sizes.most_common(10), 'unique_preview_bytes': sum(unique_previews.values()), 'node_duplicate_artifacts': duplicates, 'runs': sorted(run_sizes, key=lambda item: item[1], reverse=True)}), flush=True)
