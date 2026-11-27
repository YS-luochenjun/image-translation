"""Configuration-driven layered work, with immutable artifacts and honest review state."""
from __future__ import annotations

from copy import deepcopy
from inspect import getsource
from pathlib import Path
from time import perf_counter
import hashlib
import json
import re

from PIL import Image, ImageChops, ImageDraw

from .artifact_copy import copy_artifact
from .production_timing import timed_production
from .contracts import box, require, validate_manifest
from .visual_review import validate_visual_targets, comparison_template, check_comparisons
from .pipeline import read_json, sha256, write_json

PLAN_KEYS = {'schema_version', 'source', 'source_sha256', 'background', 'rows', 'assets',
             'protected_regions', 'text_clear_boxes', 'working_scale', 'view_scale', 'notes',
             'visual_targets', 'semantic_manifest', 'target_language', 'background_review', 'region_map'}
REVIEW_CHECKS = ('semantic', 'completeness', 'legibility', 'restoration', 'style', 'protected_content')
LEGACY_BACKGROUND_MODULE_SHA256 = 'd9a30068df6b5aefdba73c2a74f281f3a28831eaf6aeb83c5a9e29606a343684'


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
    source_sha = sha256(source_path)
    require(source_sha == plan.get('source_sha256'), 'Plan source SHA256 mismatch')
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
    validate_visual_targets(plan.get('visual_targets', []), source.size)
    inputs = {str(source_path): source_sha}
    if 'semantic_manifest' in plan or 'target_language' in plan:
        manifest_path = _path(path.resolve().parent, plan.get('semantic_manifest'))
        manifest = read_json(manifest_path)
        validate_manifest(manifest)
        require(manifest['source']['sha256'] == plan['source_sha256'], 'Semantic manifest source mismatch')
        require(manifest['target_language'] == plan.get('target_language'), 'Semantic target language mismatch')
        require((manifest['canvas']['width'], manifest['canvas']['height']) == source.size, 'Semantic canvas mismatch')
        from .job_content import validate_region_map, merged_protections
        validate_region_map(plan, manifest)
        required_protections = merged_protections(plan.get('protected_regions', []),
                                                  [(plan['target_language'], manifest)], source.size)
        require(plan.get('protected_regions', []) == required_protections,
                'Plan must include all semantic preserve regions in protected_regions')
        inputs[str(manifest_path)] = sha256(manifest_path)
    return plan, source, inputs


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
    # Semantic edits do not invalidate a previously prepared background.
    source_path = _path(plan_path.resolve().parent, plan['source'])
    result = {str(source_path): inputs[str(source_path)]}
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


def _portable_background_key(plan_path: Path, plan: dict, inputs: dict, *, legacy=False) -> str:
    background = deepcopy(plan.get('background', {'operations': []}))
    for field in ('path', 'mask'):
        if field in background:
            background[field] = inputs[str(_path(plan_path.resolve().parent, background[field]))]
    functions = (_image, _difference, _count, _protected, _background_inputs, _prepare_background_pixels)
    implementation = (LEGACY_BACKGROUND_MODULE_SHA256 if legacy else
                      hashlib.sha256('\n'.join(getsource(function) for function in functions).encode()).hexdigest())
    content = {'source': plan['source_sha256'], 'background': background,
               'protected_regions': plan.get('protected_regions', []),
               'implementation': [implementation, sha256(Path(__file__).with_name('layer_ops.py')),
                                  Image.__version__]}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _save_receipt(output: Path, stage: str, inputs: dict, started: float, extra: dict) -> dict:
    artifacts = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
    for name, path in extra.get('artifact_refs', {}).items():
        artifacts[name] = inputs[str((output / path).resolve())] if stage == 'background' else inputs[path]
    for name, target in extra.get('artifact_aliases', {}).items():
        artifacts[name] = artifacts[target]
    receipt = {'schema_version': 1, 'stage': stage, 'input_sha256': inputs, 'artifact_sha256': artifacts,
               'elapsed_seconds': round(perf_counter() - started, 4), 'status': 'pending', **extra}
    write_json(output / 'receipt.json', receipt)
    return receipt


def background_artifact(output: Path, receipt: dict, name: str) -> Path:
    references = receipt.get('artifact_refs', {})
    require(isinstance(references, dict) and set(references) <= {'source.png', 'background.png', 'edit-mask.png'},
            'Invalid shared background inventory')
    if name not in references:
        return output / name
    value = references[name]
    digest = receipt.get('artifact_sha256', {}).get(name)
    require(isinstance(value, str) and isinstance(digest, str)
            and re.fullmatch(r'\.\./inputs/[a-f0-9]{64}\.[a-z0-9]{1,10}', value)
            and Path(value).stem == digest and not (output / name).exists(),
            f'Invalid shared background artifact: {name}')
    path = (output / value).resolve()
    require(path.parent == output.resolve().parent / 'inputs', 'Shared background artifact escaped frozen inputs')
    return path


def render_artifact(output: Path, receipt: dict, name: str) -> Path:
    aliases = receipt.get('artifact_aliases', {})
    require(aliases in ({}, {'overlay.png': 'text-layer.png'}), 'Invalid render artifact aliases')
    if name in aliases:
        target = output / aliases[name]
        artifacts = receipt.get('artifact_sha256', {})
        require(receipt.get('profile') == 'compact' and isinstance(artifacts, dict)
                and name in artifacts and artifacts[name] == artifacts.get(aliases[name])
                and not (output / name).exists() and not (output / name).is_symlink() and not target.is_symlink(),
                f'Invalid render artifact alias: {name}')
        return target
    references = receipt.get('artifact_refs', {})
    require(isinstance(references, dict) and set(references) <= {'source.png', 'background.png'},
            'Invalid shared render inventory')
    if name not in references:
        return output / name
    value = references[name]
    inputs, artifacts = receipt.get('input_sha256'), receipt.get('artifact_sha256')
    require(isinstance(value, str) and isinstance(inputs, dict) and isinstance(artifacts, dict),
            'Invalid shared artifact evidence')
    path = Path(value)
    digest = artifacts.get(name)
    require(path.is_absolute() and (path.name == name or path.stem == digest) and
            isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest)
            and inputs.get(str(path)) == digest
            and not (output / name).exists(), f'Invalid shared render artifact: {name}')
    return path


def _prepare_background_pixels(plan_path, plan, source, background):
    from .layer_ops import repair_background

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
    return clean, mask, reports, outside, protection


@timed_production(input_name='plan_path', output_name='output', scope='background')
def prepare_layered(plan_path: Path, output: Path, *, reuse: Path | None = None,
                    frozen_refs: bool = False) -> dict:
    started = perf_counter()
    require(not output.exists(), 'Output already exists; choose a new stage directory')
    plan, source, source_inputs = load_plan(plan_path)
    inputs = _background_inputs(plan_path, plan, source_inputs)
    background = plan.get('background', {'operations': []})
    portable_key = _portable_background_key(plan_path, plan, inputs)
    candidates = {'source.png': plan['source']}
    if 'path' in background:
        candidates.update({'background.png': background['path'], 'edit-mask.png': background['mask']})
    references = {}
    if frozen_refs:
        require(output.resolve().parent == plan_path.resolve().parent, 'Shared artifacts require a frozen job layout')
        for name, value in candidates.items():
            path = _path(plan_path.resolve().parent, value)
            require(path.parent == output.resolve().parent / 'inputs' and path.stem == inputs[str(path)],
                    'Shared artifacts require a hashed frozen input')
            references[name] = f'../inputs/{path.name}'
    if reuse is not None:
        prior = _verify(reuse, 'background')
        require(prior.get('portable_background_key') in (portable_key, _portable_background_key(plan_path, plan, inputs, legacy=True)),
                'Reviewed background inputs or implementation changed; prepare a new background')
        require(all(sha256(Path(path)) == digest for path, digest in inputs.items()), 'Input changed during preparation')
        output.mkdir(parents=True)
        for name in ('source.png', 'background.png', 'edit-mask.png', 'repair-layout.json'):
            if name in references:
                digest = inputs[str((output / references[name]).resolve())]
                if name == 'source.png' or digest == prior['artifact_sha256'][name]:
                    continue
                references.pop(name)
            copy_artifact(background_artifact(reuse, prior, name), output / name)
            require(sha256(output / name) == prior['artifact_sha256'][name], 'Reused artifact changed while copying')
        write_json(output / 'background-plan.json', background)
        receipt = _save_receipt(output, 'background', inputs, started, {
            'background_key': _background_key(plan, inputs), 'portable_background_key': portable_key,
            'artifact_refs': references,
            'protected_changed_pixels': prior['protected_changed_pixels'],
            'outside_mask_changed_pixels': prior['outside_mask_changed_pixels'],
            'reused_receipt_sha256': sha256(reuse / 'receipt.json')})
        return {'output_dir': str(output.resolve()), 'status': 'pending', 'stage': 'background',
                'elapsed_seconds': receipt['elapsed_seconds'], 'background_reused': True}
    clean, mask, reports, outside, protection = _prepare_background_pixels(plan_path, plan, source, background)
    require(all(sha256(Path(path)) == digest for path, digest in inputs.items()), 'Input changed during preparation')
    output.mkdir(parents=True)
    for name, image in (('source.png', source), ('background.png', clean), ('edit-mask.png', mask)):
        if name not in references:
            image.save(output / name)
    write_json(output / 'background-plan.json', background)
    write_json(output / 'repair-layout.json', {'operations': reports})
    receipt = _save_receipt(output, 'background', inputs, started,
                            {'background_key': _background_key(plan, inputs), 'portable_background_key': portable_key,
                             'artifact_refs': references,
                             'protected_changed_pixels': protection,
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
        'translated.png', 'plan.json', 'layout.json', 'review-template.json'}
    if stage == 'render' and receipt.get('profile') != 'compact':
        required |= {'overlay-preview.png', 'comparison.png', 'view.png'}
    if stage == 'render' and 'empty_artifacts' in receipt:
        empty = receipt['empty_artifacts']
        require(receipt.get('profile') == 'compact' and isinstance(empty, list)
                and all(isinstance(name, str) and (name == 'asset-layer.png'
                        or re.fullmatch(r'effects-[a-z][a-z0-9-]*\.png', name)) for name in empty),
                'Invalid empty render artifact inventory')
        require(isinstance(artifacts, dict) and all(name not in artifacts and not (output / name).exists()
                and not (output / name).is_symlink() for name in empty), 'Empty render artifact has a file')
        required.difference_update(empty)
    require(isinstance(artifacts, dict) and required.issubset(artifacts), 'Receipt is missing required artifact hashes')
    require(isinstance(inputs, dict) and bool(inputs), 'Receipt is missing input hashes')
    require(all(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) for digest in [*artifacts.values(), *inputs.values()]), 'Invalid receipt hash')
    for name, digest in artifacts.items():
        require(Path(name).name == name and '/' not in name and '\\' not in name, 'Invalid artifact path')
        path = (render_artifact(output, receipt, name) if stage == 'render'
                else background_artifact(output, receipt, name))
        if stage == 'background' and name in receipt.get('artifact_refs', {}):
            require(inputs.get(str(path)) == digest, f'Unbound shared background artifact: {name}')
        require(sha256(path) == digest, f'Layered artifact changed: {name}')
    for path, digest in receipt.get('input_sha256', {}).items():
        require(sha256(Path(path)) == digest, f'Layered input changed: {path}')
    return receipt


@timed_production(input_name='plan_path', output_name='output')
def render_layered(plan_path: Path, prepared: Path, output: Path, *, compact: bool = False) -> dict:
    from .layer_ops import compose_assets
    from .lettering import render_lettering

    started = perf_counter()
    require(not output.exists(), 'Output already exists; choose a new render directory')
    plan, source, source_inputs = load_plan(plan_path)
    background_inputs = _background_inputs(plan_path, plan, source_inputs)
    background_receipt = _verify(prepared, 'background')
    require(background_receipt['background_key'] == _background_key(plan, background_inputs),
            'Source/background/protection changed; prepare a new background')
    clean_path = background_artifact(prepared, background_receipt, 'background.png')
    clean = _image(clean_path)
    require(clean.size == source.size, 'Prepared background size mismatch')
    resolved_rows = deepcopy(plan.get('rows', []))
    inputs = {str(plan_path.resolve()): sha256(plan_path), **source_inputs, **background_inputs,
              str((prepared / 'receipt.json').resolve()): sha256(prepared / 'receipt.json')}
    if 'background_review' in plan:
        from .stage_review import verify_background_review
        review_path = _path(plan_path.resolve().parent, plan['background_review'])
        verify_background_review(review_path, source_sha256=plan['source_sha256'],
                                 background=clean_path, receipt=prepared / 'receipt.json')
        inputs[str(review_path)] = sha256(review_path)
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
    lettering, text_layout, components = render_lettering(
        source.size, resolved_rows, scale=plan.get('working_scale', 6), base_dir=plan_path.resolve().parent)
    for clear_box in plan.get('text_clear_boxes', []):
        x, y, width, height = clear_box
        rectangle = (x, y, x + width, y + height)
        cleared = _count(lettering.getchannel('A').crop(rectangle))
        lettering.paste((0, 0, 0, 0), rectangle)
        for component in components.values():
            component.paste(0 if component.mode == 'L' else (0, 0, 0, 0), rectangle)
        text_layout.append({'type': 'preserve', 'bbox': clear_box, 'cleared_alpha_pixels': cleared,
                            'requires_review': bool(cleared), 'warnings': [
                                'Protection cleared text alpha and may damage glyphs; actual visual review is required.'
                            ] if cleared else []})
    assets, asset_layout = compose_assets(source.size, resolved_assets, base_dir=plan_path.resolve().parent)
    overlay = Image.alpha_composite(lettering, assets)
    final = Image.alpha_composite(clean, overlay)
    protection = _protected(source, final, plan.get('protected_regions', []))
    with Image.open(background_artifact(prepared, background_receipt, 'edit-mask.png')) as mask_image:
        allowed = ImageChops.lighter(mask_image.convert('L'), overlay.getchannel('A')).point(lambda value: 255 if value else 0)
    outside = _count(ImageChops.subtract(_difference(source, final), allowed))
    require(all(sha256(Path(path)) == digest for path, digest in inputs.items()), 'Input changed during rendering')
    _verify(prepared, 'background')
    output.mkdir(parents=True)
    references = {}
    for name in ('source.png', 'background.png'):
        path = background_artifact(prepared, background_receipt, name).resolve()
        if compact:
            references[name] = str(path)
            inputs[str(path)] = background_receipt['artifact_sha256'][name]
        elif name in background_receipt.get('artifact_refs', {}):
            (source if name == 'source.png' else clean).save(output / name)
        else:
            copy_artifact(path, output / name)
            require(sha256(output / name) == background_receipt['artifact_sha256'][name],
                    'Prepared artifact changed while copying')
    lettering.save(output / 'text-layer.png')
    empty_artifacts = []
    for name, component in components.items():
        require(re.fullmatch(r'[a-z][a-z0-9-]*', name), 'Invalid effect component name')
        filename = f'effects-{name}.png'
        if compact and component.getbbox() is None:
            empty_artifacts.append(filename)
        else:
            component.save(output / filename)
    if compact and assets.getbbox() is None:
        empty_artifacts.append('asset-layer.png')
    else:
        assets.save(output / 'asset-layer.png')
    aliases = {}
    if compact and ImageChops.difference(overlay, lettering).getbbox(alpha_only=False) is None:
        aliases['overlay.png'] = 'text-layer.png'
    else:
        overlay.save(output / 'overlay.png')
    allowed.save(output / 'allowed-edit-mask.png')
    final.save(output / 'translated.png')
    if not compact:
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
    if plan.get('visual_targets'):
        template['style_comparisons'] = comparison_template(plan['visual_targets'])
    write_json(output / 'review-template.json', template)
    technical = 'fail' if outside or any(protection.values()) else 'pass'
    compact_inventory = {'artifact_aliases': aliases, 'empty_artifacts': sorted(empty_artifacts)} if compact else {}
    receipt = _save_receipt(output, 'render', inputs, started,
                            {'technical': technical, 'status': 'fail' if technical == 'fail' else 'pending',
                             'profile': 'compact' if compact else 'full', 'artifact_refs': references,
                             **compact_inventory,
                             'protected_changed_pixels': protection, 'outside_mask_changed_pixels': outside,
                             'background_elapsed_seconds': background_receipt['elapsed_seconds'],
                             'notice': 'Elapsed time measures tool execution only, not Agent analysis or visual review. No model calls.'})
    return {'output_dir': str(output.resolve()), 'status': receipt['status'], 'technical': technical,
            'shared_artifacts': references,
            **compact_inventory,
            'elapsed_seconds': receipt['elapsed_seconds'], 'next': 'View source/background/overlay/composite; copy review-template.json to review.json and record actual review'}


def check_layered(output: Path) -> dict:
    result = {'status': 'pending', 'technical': 'fail', 'user_acceptance': 'not_recorded', 'reasons': []}
    try:
        receipt = _verify(output, 'render')
        # Recheck actual output, not only historical technical assertions.
        source, final = _image(render_artifact(output, receipt, 'source.png')), _image(output / 'translated.png')
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
        values.extend(check_comparisons(plan.get('visual_targets', []), review.get('style_comparisons'),
                                        set(receipt['artifact_sha256'])))
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
