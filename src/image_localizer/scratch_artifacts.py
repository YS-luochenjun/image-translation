"""Demand-scoped storage for imported edits and on-demand contact sheets."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps, features

from .artifact_copy import stage_artifact
from .contracts import box, require
from .fireplay import _write, request_folder
from .pipeline import read_json, sha256 as file_sha256

IMAGE_FORMATS = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}
MAX_IMAGE_PIXELS = 50_000_000


def _key(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _location(root: Path, code: str, *parts: str) -> Path:
    root = root.resolve()
    request_folder(code, root)
    path = root / 'work' / 'fireplay-production' / code / 'scratch' / Path(*parts)
    require(path == path.resolve(), 'Linked scratch paths are unsupported')
    return path


def _record(path: Path, payload: dict) -> None:
    if path.exists():
        require(read_json(path) == payload, 'Scratch receipt differs from recorded evidence')
    else:
        _write(path, payload, immutable=True)


def stage_model_output(root: Path, code: str, source: Path, *, provider: str,
                       operation: str, members: list[str]) -> dict:
    require(isinstance(provider, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', provider),
            'Expected a model provider or tool name')
    require(isinstance(operation, str) and operation.strip() and len(operation) <= 240,
            'Expected the provider operation or output ID')
    require(isinstance(members, list) and 1 <= len(members) <= 100
            and all(isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', item) for item in members),
            'Expected member IDs for the model output')
    source = source.resolve(strict=True)
    require(source.is_file(), 'Expected a model output file')
    digest = file_sha256(source)
    with Image.open(source) as image:
        require(image.format in IMAGE_FORMATS and getattr(image, 'n_frames', 1) == 1
                and image.width * image.height <= MAX_IMAGE_PIXELS, 'Expected a supported single-frame model image')
        extension = IMAGE_FORMATS[image.format]
        image.verify()
    destination = _location(root, code, 'model', f'{digest}.{extension}')
    receipt = {'schema_version': 1, 'code': code, 'provider': provider, 'operation': operation,
               'members': sorted(set(members)), 'source': str(source),
               'artifact': destination.name, 'sha256': digest}
    receipt_path = _location(root, code, 'model', 'receipts', f'{_key(receipt)}.json')
    result = stage_artifact(source, destination, expected_sha256=digest)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _record(receipt_path, receipt)
    return {**result, 'receipt': str(receipt_path)}


def scratch_preview(root: Path, code: str, spec_path: Path) -> dict:
    spec = read_json(spec_path)
    require(isinstance(spec, dict) and set(spec) <= {'items', 'columns', 'cell_size', 'quality'},
            'Invalid preview spec fields')
    items = spec.get('items')
    columns, cell, quality = spec.get('columns', 3), spec.get('cell_size', [400, 320]), spec.get('quality', 90)
    require(isinstance(items, list) and 1 <= len(items) <= 20, 'Use 1 to 20 ordered preview items')
    require(type(columns) is int and 1 <= columns <= 8, 'Preview columns must be between 1 and 8')
    require(isinstance(cell, list) and len(cell) == 2 and all(type(side) is int and 64 <= side <= 1024 for side in cell),
            'Preview cell_size must contain two integers between 64 and 1024')
    require(type(quality) is int and 40 <= quality <= 95, 'Preview quality must be between 40 and 95')
    sources, tiles = [], []
    for item in items:
        require(isinstance(item, dict) and set(item) <= {'path', 'crop', 'label'}
                and isinstance(item.get('path'), str) and bool(item['path']), 'Invalid preview item')
        label = item.get('label', '')
        require(isinstance(label, str) and len(label) <= 60 and (not label or label.isprintable()),
                'Preview label must be a single line of up to 60 characters')
        path = (spec_path.resolve().parent / item['path']).resolve(strict=True)
        require(path.is_file(), 'Preview item must be a local file')
        sources.append(path)
        tiles.append({'sha256': file_sha256(path), 'crop': item.get('crop'), 'label': label})
    identity = {'schema_version': 1, 'items': tiles, 'columns': columns, 'cell_size': cell,
                'quality': quality, 'pillow': Image.__version__, 'jpeg': features.version_codec('jpg')}
    folder = _location(root, code, 'previews', _key(identity))
    destination, receipt_path = folder / 'preview.jpg', folder / 'receipt.json'
    if destination.is_file() and receipt_path.is_file():
        digest = file_sha256(destination)
        require(read_json(receipt_path) == {'identity': identity, 'sha256': digest}, 'Cached preview evidence changed')
        return {'path': str(destination), 'sha256': digest, 'mode': 'reused', 'receipt': str(receipt_path)}

    width, height = cell
    columns = min(columns, len(tiles))
    rows = (len(tiles) + columns - 1) // columns
    board = Image.new('RGB', (columns * width, rows * height), '#eeeeee')
    draw = ImageDraw.Draw(board)
    for index, (path, tile) in enumerate(zip(sources, tiles)):
        with Image.open(path) as image:
            require(getattr(image, 'n_frames', 1) == 1 and image.width * image.height <= MAX_IMAGE_PIXELS,
                    'Expected a supported single-frame preview image')
            panel = ImageOps.exif_transpose(image).convert('RGBA')
        crop = tile['crop']
        if crop is not None:
            left, top, crop_width, crop_height = box(crop, *panel.size, 'Preview crop')
            panel = panel.crop((left, top, left + crop_width, top + crop_height))
        x_offset, y_offset = (index % columns) * width, (index // columns) * height
        caption_height = 20 if tile['label'] else 0
        panel.thumbnail((width - 8, height - caption_height - 8), Image.Resampling.LANCZOS)
        board.paste(panel, (x_offset + (width - panel.width) // 2,
                            y_offset + caption_height + (height - caption_height - panel.height) // 2), panel)
        if tile['label']:
            draw.text((x_offset + 4, y_offset + 4), tile['label'], fill='#242424')
        require(file_sha256(path) == tile['sha256'], 'Preview input changed during generation')
    with TemporaryDirectory(prefix='image-localizer-preview-') as temporary:
        rendered = Path(temporary) / 'preview.jpg'
        board.save(rendered, quality=quality)
        digest = file_sha256(rendered)
        receipt = {'identity': identity, 'sha256': digest}
        if receipt_path.exists():
            require(read_json(receipt_path) == receipt, 'Regenerated preview differs from recorded evidence')
        result = stage_artifact(rendered, destination, expected_sha256=digest)
        _record(receipt_path, receipt)
    return {**result, 'receipt': str(receipt_path), 'mode': 'generated' if result['mode'] != 'reused' else 'reused'}
