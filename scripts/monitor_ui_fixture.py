"""Private backend fixture for browser evaluation tests; never indexes real runs."""
from pathlib import Path
import json
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from image_localizer.review_desk import create_server, digest

with tempfile.TemporaryDirectory(prefix='monitor-ui-') as temporary:
    root = Path(temporary)
    run = root / 'runs' / 'sample'
    output = run / 'renders' / 'v1'
    output.mkdir(parents=True)
    (run / 'source.png').write_bytes(b'isolated source')
    manifest = run / 'manifest.json'
    manifest.write_text(json.dumps({'source': {'path': 'source.png'}, 'target_language': 'ja', 'regions': []}), encoding='utf-8')
    (output / 'translated.png').write_bytes(b'isolated output')
    (output / 'receipt.json').write_text(json.dumps({'output_sha256': digest(output / 'translated.png'), 'manifest_sha256': digest(manifest), 'technical': 'pass'}), encoding='utf-8')
    server = create_server(root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'origin': server.origin, 'token': server.token}), flush=True)
    try:
        for command in sys.stdin:
            if command.strip() == 'mutate':
                (output / 'translated.png').write_bytes(b'changed isolated output')
                print(json.dumps({'mutated': True}), flush=True)
            else:
                break
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
