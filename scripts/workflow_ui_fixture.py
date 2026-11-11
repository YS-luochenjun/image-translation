"""Disposable v5 browser fixture. IDs never identify real Host conversations."""
from pathlib import Path
from datetime import datetime, timezone
import json
import sys
import tempfile
import threading
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import create_server, digest


def image(path, label, color):
    canvas = Image.new('RGB', (480, 280), color)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((44, 54, 436, 218), 16, fill='#ffffff', outline='#8fa5a2', width=2)
    draw.text((68, 110), label, fill='#173b38')
    canvas.save(path)


def now():
    return datetime.now(timezone.utc).isoformat()


with tempfile.TemporaryDirectory(prefix='workflow-ui-') as temporary:
    root = Path(temporary)
    for suffix in ('a', 'b'):
        run = root / 'runs' / ('sample-' + suffix)
        run.mkdir(parents=True)
        image(run / 'source.png', 'Hello', '#edf3ef')
        image(run / 'background.png', 'CLEAN BACKGROUND', '#f0f4ef')
        image(run / 'translated.png', 'TRANSLATED ' + suffix.upper(), '#e1efea')
        image(run / 'text-layer.png', 'TEXT LAYER', '#ffffff')
        with Image.open(run / 'source.png') as source, Image.open(run / 'translated.png') as result:
            comparison = Image.new('RGB', (960, 280), '#ffffff')
            comparison.paste(source, (0, 0))
            comparison.paste(result, (480, 0))
            comparison.save(run / 'comparison.png')
        manifest = {'source': {'path': 'source.png', 'sha256': digest(run / 'source.png')},
                    'canvas': {'width': 480, 'height': 280}, 'target_language': 'ja',
                    'analysis': {'observations': ['The main headline reads Hello.']},
                    'regions': [{'id': 'headline', 'bbox': [60, 90, 350, 70], 'source_text': 'Hello',
                                 'target_text': 'こんにちは', 'action': 'translate',
                                 'recognition_verified': True, 'confidence': 0.99}]}
        (run / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        (run / 'plan.json').write_text(json.dumps({'source': 'source.png', 'rows': [], 'background': 'background.png'}), encoding='utf-8')
        receipt = {'stage': 'render', 'source_sha256': digest(run / 'source.png'), 'manifest_sha256': digest(run / 'manifest.json'),
                   'output_sha256': digest(run / 'translated.png'),
                   'artifact_sha256': {name: digest(run / name) for name in ('source.png', 'translated.png', 'text-layer.png', 'comparison.png')}}
        (run / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    server = create_server(root)
    workflow = server.desk.workflow
    sessions = [
        {'session_id': 'workflow-fixture-' + suffix.upper(), 'title': 'Isolated flow ' + suffix.upper(), 'status': 'running', 'cwd': str(root),
         'events': [{'event_id': 'seed-' + suffix, 'tool_name': 'ops_bash', 'phase': 'completed',
                     'run_root': 'runs/sample-' + suffix, 'stage': 'background.prepare', 'command_kind': 'prepare-layered',
                     'output_paths': ['runs/sample-' + suffix]}],
         'highlights': [{'event_id': 'summary-' + suffix, 'executor_session_id': 'workflow-fixture-' + suffix.upper(),
                         'run_root': 'runs/sample-' + suffix, 'stage': 'review.visual',
                         'text': 'Keep the main headline centered; check its spacing before final approval.'}]}
        for suffix in ('a', 'b')]
    stamp = now()
    sessions.append({'session_id': 'workflow-fixture-C', 'title': 'Isolated rootless activity C',
                     'status': 'running', 'cwd': str(root), 'events': [], 'activities': [
                         {'event_id': 'fixture-stage-C', 'type': 'stage', 'time': stamp, 'seq': 1,
                          'text': '正在整理标题与目标语言', 'stage': '内容分析', 'status': 'running'},
                         {'event_id': 'fixture-plan-C', 'type': 'plan', 'time': stamp, 'seq': 2,
                          'text': '三步制作计划', 'items': [
                              {'content': '核对标题与目标语言', 'status': 'in_progress'},
                              {'content': '制作独立文字层', 'status': 'pending'},
                              {'content': '比较原图和最终成品', 'status': 'pending'}]}]})
    workflow.ingest({'sessions': sessions})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'origin': server.origin, 'token': server.token,
                      'session_ids': [session['session_id'] for session in sessions],
                      'fixture_only': True}), flush=True)
    try:
        for command in sys.stdin:
            operation = command.strip()
            if operation == 'advance':
                workflow.ingest({'sessions': [{'session_id': 'workflow-fixture-A', 'cwd': str(root),
                    'events': [{'event_id': 'advance-a', 'tool_name': 'ops_bash', 'phase': 'started',
                                'run_root': 'runs/sample-a', 'stage': 'composite.render', 'command_kind': 'render-layered',
                                'output_paths': ['runs/sample-a']}]}]})
                print(json.dumps({'advanced': True}), flush=True)
            elif operation == 'activity':
                workflow.ingest({'sessions': [{'session_id': 'workflow-fixture-C', 'status': 'running', 'cwd': str(root),
                    'activities': [{'event_id': 'fixture-stage-C-next', 'type': 'stage', 'time': now(), 'seq': 3,
                                    'text': '已核对标题，正在准备目标文字', 'stage': '文字准备', 'status': 'running'}]}]})
                print(json.dumps({'activity_advanced': True}), flush=True)
            elif operation == 'mutate':
                image(root / 'runs/sample-a/translated.png', 'CHANGED CURRENT OUTPUT', '#f8e9df')
                print(json.dumps({'mutated': True}), flush=True)
            else:
                break
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
