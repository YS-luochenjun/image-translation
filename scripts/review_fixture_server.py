"""Isolated two-image, multilingual/version fixture for browser review tests."""
import json
from pathlib import Path
import sys
import uuid
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from image_localizer.review_desk import create_server, digest


def image(path, text, alternate=False):
    result = Image.new('RGB', (720, 400), '#edf1ef' if alternate else '#f6f2e8')
    draw = ImageDraw.Draw(result)
    draw.rectangle((400, 50, 650, 350), fill='#789c89')
    draw.text((40, 30), text, fill='#244d3d', font_size=30)
    result.save(path)


def add_run(root, name, language, versions, alternate=False):
    run = root / 'runs' / name
    run.mkdir(parents=True)
    image(run / 'source.png', 'OTHER IMAGE' if alternate else 'HELLO SOURCE', alternate)
    manifest = {'source': {'path': 'source.png', 'sha256': digest(run / 'source.png')}, 'target_language': language, 'regions': [
        {'id': f'region-{index + 1}', 'action': 'translate', 'source_text': f'Original text {index + 1}',
         'target_text': f'{language} translation {index + 1}',
         'bbox': [40 + index % 4 * 150, 90 + index // 4 * 40, 140, 32],
         'notes': 'Isolated browser verification sample; inspect only this region.'}
        for index in range(24)]}
    manifest_path = run / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    for version in range(1, versions + 1):
        render = run / 'renders' / f'v{version}'
        render.mkdir(parents=True)
        image(render / 'translated.png', f'{language.upper()} RESULT V{version}', alternate)
        (render / 'receipt.json').write_text(json.dumps({'manifest_sha256': digest(manifest_path),
            'input_sha256': {'../../manifest.json': digest(manifest_path)},
            'output_sha256': digest(render / 'translated.png'),
            'artifact_sha256': {'translated.png': digest(render / 'translated.png')},
            'status': 'needs_review'}), encoding='utf-8')


def main():
    root = ROOT / '.review-desk' / 'browser-fixtures' / uuid.uuid4().hex
    add_run(root, 'other-image', 'en', 1, alternate=True)
    add_run(root, 'browser-sample-fr', 'fr', 2)
    add_run(root, 'browser-sample-en', 'en', 2)
    with create_server(root, 0) as server:
        print(server.origin + '/', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
