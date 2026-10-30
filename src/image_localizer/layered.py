"""Configuration-driven layered work, with immutable artifacts and honest review state."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
import hashlib
import json
import re

from PIL import Image, ImageChops, ImageDraw

from .contracts import box, require
from .pipeline import read_json, sha256, write_json

PLAN_KEYS = {'schema_version', 'source', 'source_sha256', 'background', 'rows', 'assets',
             'protected_regions', 'text_clear_boxes', 'working_scale', 'view_scale', 'notes'}
REVIEW_CHECKS = ('semantic', 'completeness', 'legibility', 'restoration', 'style', 'protected_content')


def _path(base: Path, value: str) -> Path:
    require(isinstance(value, str) and bool(value.strip()) and '://' not in value, 'Expected local file path')
    path = (base / value).resolve()
    require(path.is_file(), f'Missing local input: {path}')
    return path


def _image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        require(getattr(image, 'n_frames', 1) == 1, 'Select one frame before layered processing')
        require(image.width * image.height <= 50_000_000, 'Image exceeds 50 megapixels')
        require(image.getexif().get(274, 1) == 1, 'Normalize EXIF orientation with init first')
        return image.convert('RGBA')


def load_plan(path: Path) -> tuple[dict, Image.Image, dict[str, str]]:
    plan = read_json(path)
    require(isinstance(plan, dict), 'Layered plan must be an object')
    require(not set(plan) - PLAN_KEYS and plan.get('schema_version') == 1, 'Unknown layered plan fields/version')
    source_path = _path(path.resolve().parent, plan.get('source'))
    require(sha256(source_path) == plan.get('source_sha256'), 'Plan source SHA256 mismatch')
    source = _image(source_path)
    for key in ('rows', 'assets', 'protected_regions', 'text_clear_boxes'):
        require(isinstance(plan.get(key, []), list), f'{key} must be a list')
    for key, default, maximum in [('working_scale', 6, 8), ('view_scale', 3, 4)]:
        value = plan.get(key, default)
        require(type(value) is int and 1 <= value <= maximum, f'Invalid {key}')
    require(source.width * source.height * plan.get('working_scale', 6) ** 2 <= 100_000_000,
            'Working canvas exceeds 100 megapixels')
    require(source.width * source.height * plan.get('view_scale', 3) ** 2 <= 100_000_000,
            'Viewing canvas exceeds 100 megapixels')
    ids = set()
    for item in plan.get('protected_regions', []):
        require(isinstance(item, dict) and set(item) == {'id', 'bbox'}, 'Protection requires id and bbox')
        require(isinstance(item['id'], str) and item['id'] and item['id'] not in ids, 'Duplicate protection ID')
        ids.add(item['id'])
        box(item['bbox'], *source.size, item['id'])
    for bbox in plan.get('text_clear_boxes', []):
        box(bbox, *source.size, 'text_clear_boxes')
    notes = plan.get('notes', [])
    require(isinstance(notes, list) and all(isinstance(note, str) for note in notes), 'notes must be strings')
    return plan, source, {str(source_path): sha256(source_path)}


def _difference(first: Image.Image, second: Image.Image) -> Image.Image:
    difference = ImageChops.difference(first.convert('RGBA'), second.convert('RGBA'))
    mask = Image.new('L', first.size)
    for band in difference.split():
        mask = ImageChops.lighter(mask, band)
    return mask.point(lambda value: 255 if value else 0)


def _count(mask: Image.Image) -> int:
    return sum(mask.histogram()[1:])


def _protected(source: Image.Image, output: Image.Image, regions: list[dict]) -> dict:
    difference = _difference(source, output)
    return {region['id']: _count(difference.crop((x, y, x + w, y + h)))
            for region in regions for x, y, w, h in [region['bbox']]}


def _background_key(plan: dict, inputs: dict) -> str:
    data = {'source_sha256': plan['source_sha256'], 'background': plan.get('background', {'operations': []}),
            'protected_regions': plan.get('protected_regions', []), 'inputs': inputs}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _background_inputs(plan_path: Path, plan: dict, inputs: dict) -> dict:
    result = dict(inputs)
    background = plan.get('background', {'operations': []})
    require(isinstance(background, dict), 'background must be an object')
    if 'path' in background:
        require(set(background) == {'path', 'mask'}, 'External background requires path and edit mask only')
        for key in ('path', 'mask'):
            path = _path(plan_path.resolve().parent, background[key])
            result[str(path)] = sha256(path)
    else:
        require(set(background) == {'operations'} and isinstance(background['operations'], list),
                'Background expects operations or path+mask')
    return result


def _save_receipt(output: Path, stage: str, inputs: dict, started: float, extra: dict) -> dict:
    artifacts = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
    receipt = {'schema_version': 1, 'stage': stage, 'input_sha256': inputs, 'artifact_sha256': artifacts,
               'elapsed_seconds': round(perf_counter() - started, 4), 'status': 'pending', **extra}
    write_json(output / 'receipt.json', receipt)
    return receipt


def prepare_layered(plan_path: Path, output: Path) -> dict:
    from .layer_ops import repair_background

    started = perf_counter()
    require(not output.exists(), 'Output already exists; choose a new stage directory')
    plan, source, source_inputs = load_plan(plan_path)
    inputs = _background_inputs(plan_path, plan, source_inputs)
    background = plan.get('background', {'operations': []})
    if 'path' in background:
        clean = _image(_path(plan_path.resolve().parent, background['path']))
        with Image.open(_path(plan_path.resolve().parent, background['mask'])) as mask_image:
            require(getattr(mask_image, 'n_frames', 1) == 1, 'External edit mask must be single-frame')
            require(mask_image.mode == 'L', 'External edit mask must be grayscale L')
            mask = mask_image.copy()
        require(clean.size == source.size == mask.size, 'Background/mask size must match source')
        reports = [{'type': 'external', 'note': 'Actual supplied background; inspect separately'}]
    else:
        clean, mask, reports = repair_background(source, background['operations'])
    outside = _count(ImageChops.subtract(_difference(source, clean), mask.point(lambda value: 255 if value else 0)))
    require(outside == 0, 'Background changes pixels outside its edit mask')
    protection = _protected(source, clean, plan.get('protected_regions', []))
    require(not any(protection.values()), f'Background changes protected pixels: {protection}')
    require(all(sha256(Path(path)) == digest for path, digest in inputs.items()), 'Input changed during preparation')
    output.mkdir(parents=True)
    source.save(output / 'source.png')
    clean.save(output / 'background.png')
    mask.save(output / 'edit-mask.png')
    write_json(output / 'background-plan.json', background)
    write_json(output / 'repair-layout.json', {'operations': reports})
    receipt = _save_receipt(output, 'background', inputs, started,
                            {'background_key': _background_key(plan, inputs), 'protected_changed_pixels': protection,
                             'outside_mask_changed_pixels': outside, 'review_required': 'Actually view background before lettering'})
    return {'output_dir': str(output.resolve()), 'status': 'pending', 'stage': 'background',
            'elapsed_seconds': receipt['elapsed_seconds'], 'next': 'View background.png; then render-layered with this directory'}


def _verify(output: Path, stage: str) -> dict:
    receipt = read_json(output / 'receipt.json')
    require(isinstance(receipt, dict), 'Stage receipt must be an object')
    require(receipt.get('stage') == stage and receipt.get('schema_version') == 1, 'Wrong layered stage receipt')
    artifacts = receipt.get('artifact_sha256')
    inputs = receipt.get('input_sha256')
    required = {'source.png', 'background.png', 'edit-mask.png', 'background-plan.json', 'repair-layout.json'} if stage == 'background' else {
        'source.png', 'background.png', 'text-layer.png', 'asset-layer.png', 'overlay.png', 'allowed-edit-mask.png',
        'translated.png', 'overlay-preview.png', 'comparison.png', 'view.png', 'plan.json', 'layout.json', 'review-template.json'}
    require(isinstance(artifacts, dict) and required.issubset(artifacts), 'Receipt is missing required artifact hashes')
    require(isinstance(inputs, dict) and bool(inputs), 'Receipt is missing input hashes')
    require(all(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) for digest in [*artifacts.values(), *inputs.values()]), 'Invalid receipt hash')
    for name, digest in artifacts.items():
        require(Path(name).name == name and '/' not in name and '\\' not in name, 'Invalid artifact path')
        require(sha256(output / name) == digest, f'Layered artifact changed: {name}')
    for path, digest in receipt.get('input_sha256', {}).items():
        require(sha256(Path(path)) == digest, f'Layered input changed: {path}')
    return receipt


def render_layered(plan_path: Path, prepared: Path, output: Path) -> dict:
    from .layer_ops import compose_assets, paint_text_layer

    started = perf_counter()
    require(not output.exists(), 'Output already exists; choose a new render directory')
    plan, source, source_inputs = load_plan(plan_path)
    background_inputs = _background_inputs(plan_path, plan, source_inputs)
    background_receipt = _verify(prepared, 'background')
    require(background_receipt['background_key'] == _background_key(plan, background_inputs),
            'Source/background/protection changed; prepare a new background')
    clean = _image(prepared / 'background.png')
    require(clean.size == source.size, 'Prepared background size mismatch')
    resolved_rows = deepcopy(plan.get('rows', []))
    inputs = {str(plan_path.resolve()): sha256(plan_path), **background_inputs,
              str((prepared / 'receipt.json').resolve()): sha256(prepared / 'receipt.json')}
    for row in resolved_rows:
        require(isinstance(row, dict), 'Each row must be an object')
        path = _path(plan_path.resolve().parent, row.get('font_path'))
        row['font_path'] = str(path)
        inputs[str(path)] = sha256(path)
    resolved_assets = deepcopy(plan.get('assets', []))
    for asset in resolved_assets:
        require(isinstance(asset, dict), 'Each asset must be an object')
        path = _path(plan_path.resolve().parent, asset.get('path'))
        asset['path'] = str(path)
        inputs[str(path)] = sha256(path)
    lettering, text_layout = paint_text_layer(source.size, resolved_rows, scale=plan.get('working_scale', 6),
                                              preserve_boxes=plan.get('text_clear_boxes', []))
    assets, asset_layout = compose_assets(source.size, resolved_assets, base_dir=plan_path.resolve().parent)
    overlay = Image.alpha_composite(lettering, assets)
    final = Image.alpha_composite(clean, overlay)
    protection = _protected(source, final, plan.get('protected_regions', []))
    with Image.open(prepared / 'edit-mask.png') as mask_image:
        allowed = ImageChops.lighter(mask_image.convert('L'), overlay.getchannel('A')).point(lambda value: 255 if value else 0)
    outside = _count(ImageChops.subtract(_difference(source, final), allowed))
    require(all(sha256(Path(path)) == digest for path, digest in inputs.items()), 'Input changed during rendering')
    _verify(prepared, 'background')
    output.mkdir(parents=True)
    source.save(output / 'source.png')
    clean.save(output / 'background.png')
    lettering.save(output / 'text-layer.png')
    assets.save(output / 'asset-layer.png')
    overlay.save(output / 'overlay.png')
    allowed.save(output / 'allowed-edit-mask.png')
    final.save(output / 'translated.png')
    checker = Image.new('RGBA', source.size, '#ffffff')
    draw = ImageDraw.Draw(checker)
    for y in range(0, source.height, 8):
        for x in range(0, source.width, 8):
            if (x // 8 + y // 8) % 2:
                draw.rectangle((x, y, x + 7, y + 7), fill='#dddddd')
    checker.alpha_composite(overlay)
    checker.save(output / 'overlay-preview.png')
    comparison = Image.new('RGB', (source.width * 2, source.height))
    comparison.paste(source.convert('RGB'), (0, 0))
    comparison.paste(final.convert('RGB'), (source.width, 0))
    comparison.save(output / 'comparison.png')
    view_scale = plan.get('view_scale', 3)
    final.resize((source.width * view_scale, source.height * view_scale), Image.Resampling.LANCZOS).save(output / 'view.png')
    write_json(output / 'plan.json', plan)
    write_json(output / 'layout.json', {'rows': text_layout, 'assets': asset_layout})
    advisories = {f'text-{index}': report['warnings'] for index, report in enumerate(text_layout) if report.get('warnings')}
    advisories.update({f'asset-{index}': report['warnings'] for index, report in enumerate(asset_layout) if report.get('warnings')})
    template = {'output_sha256': sha256(output / 'translated.png'), 'plan_sha256': sha256(output / 'plan.json'),
                'advisories': advisories, 'advisory_reviews': {key: None for key in advisories},
                'reviewer': '', 'review_kind': 'self', 'checks': {name: None for name in REVIEW_CHECKS},
                'stage_notes': {name: '' for name in ('source', 'background', 'lettering', 'composite')},
                'issues': [], 'user_acceptance': 'not_recorded'}
    write_json(output / 'review-template.json', template)
    technical = 'fail' if outside or any(protection.values()) else 'pass'
    receipt = _save_receipt(output, 'render', inputs, started,
                            {'technical': technical, 'status': 'fail' if technical == 'fail' else 'pending',
                             'protected_changed_pixels': protection, 'outside_mask_changed_pixels': outside,
                             'background_elapsed_seconds': background_receipt['elapsed_seconds'],
                             'notice': 'Elapsed time measures tool execution only, not Agent analysis or visual review. No model calls.'})
    return {'output_dir': str(output.resolve()), 'status': receipt['status'], 'technical': technical,
            'elapsed_seconds': receipt['elapsed_seconds'], 'next': 'View source/background/overlay/composite; copy review-template.json to review.json and record actual review'}


def check_layered(output: Path) -> dict:
    result = {'status': 'pending', 'technical': 'fail', 'user_acceptance': 'not_recorded', 'reasons': []}
    try:
        receipt = _verify(output, 'render')
        # Recheck actual output, not only historical technical assertions.
        source, final = _image(output / 'source.png'), _image(output / 'translated.png')
        plan = read_json(output / 'plan.json')
        require(source.size == final.size, 'Output canvas changed')
        protection = _protected(source, final, plan.get('protected_regions', []))
        require(not any(protection.values()) and receipt.get('technical') == 'pass', 'Protected pixels changed')
        result['technical'] = 'pass'
        if not (output / 'review.json').exists():
            result['reasons'].append('Actual review is missing')
            return result
        review = read_json(output / 'review.json')
        require(isinstance(review, dict), 'Review must be an object')
        require(review.get('output_sha256') == sha256(output / 'translated.png'), 'Review output hash is stale')
        require(review.get('plan_sha256') == sha256(output / 'plan.json'), 'Review plan hash is stale')
        require(isinstance(review.get('reviewer'), str) and bool(review['reviewer'].strip()), 'Reviewer required')
        require(review.get('review_kind') in ('self', 'independent'), 'Invalid reviewer kind')
        checks = review.get('checks', {})
        require(isinstance(checks, dict), 'Review checks must be an object')
        values = [checks.get(name) for name in REVIEW_CHECKS]
        require(all(value is None or type(value) is bool for value in values), 'Review checks must be boolean/null')
        notes = review.get('stage_notes', {})
        require(isinstance(notes, dict), 'stage_notes must be an object')
        complete_notes = all(isinstance(notes.get(name), str) and bool(notes[name].strip())
                             for name in ('source', 'background', 'lettering', 'composite'))
        issues = review.get('issues', [])
        require(isinstance(issues, list), 'Review issues must be a list')
        for issue in issues:
            require(isinstance(issue, dict) and issue.get('severity') in ('minor', 'major', 'critical')
                    and isinstance(issue.get('note'), str) and bool(issue['note'].strip()), 'Invalid review issue')
        advisory_keys = read_json(output / 'review-template.json').get('advisories', {})
        acknowledgments = review.get('advisory_reviews', {})
        require(isinstance(acknowledgments, dict), 'advisory_reviews must be an object')
        advisory_values = [acknowledgments.get(key) for key in advisory_keys]
        require(all(value is None or type(value) is bool for value in advisory_values), 'Advisory reviews must be boolean/null')
        if False in values or False in advisory_values or any(issue['severity'] in ('major', 'critical') for issue in issues):
            result.update(status='fail', reasons=['Review identifies a blocking defect'])
        elif None in values or None in advisory_values or not complete_notes:
            result['reasons'].append('Review checks or stage observations are incomplete')
        else:
            result['status'] = 'pass'
        result.update(review_kind=review['review_kind'], issues=issues)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result.update(status='fail', reasons=[str(error)])
    return result
