"""A local multi-language job with shared, reviewed background and frozen inputs.

The job prepares evidence for an Agent; it never performs OCR, translation or
visual approval. Frozen job.json and inputs can be relocated and prepared again for reproduction.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import shutil

from .production_timing import timed_production
from .contracts import require, validate_manifest
from .layer_ops import _fields
from .layered import _image, _path, _verify, prepare_layered, render_layered, check_layered
from .pipeline import initialize, read_json, write_json, sha256
from .visual_review import validate_visual_targets
from .job_content import validate_region_map, merged_protections
from .stage_review import verify_background_review

JOB_KEYS = {'schema_version', 'source', 'source_sha256', 'background', 'protected_regions',
            'working_scale', 'view_scale', 'visual_targets', 'targets', 'notes', 'support_files'}
TARGET_KEYS = {'language', 'manifest', 'rows', 'assets', 'text_clear_boxes', 'notes', 'region_map'}
MAX_BUNDLE_BYTES = 256 * 1024 * 1024


def _languages(values: object) -> list[str]:
    require(isinstance(values, list) and 1 <= len(values) <= 20, 'Select1..20 target languages')
    require(all(isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,31}', value)
                for value in values), 'Languages must be safe identifiers such as zh-CN or ja')
    require(len({value.casefold() for value in values}) == len(values), 'Duplicate target language')
    return values


def initialize_job(source: Path, targets: list[str], output: Path) -> dict:
    languages = _languages(targets)
    require(not output.exists(), 'Job directory already exists')
    require(source.is_file(), 'Missing source image')
    entries = []
    for language in languages:
        run = output / 'languages' / language
        initialize(source, language, run)
        entries.append({'language': language, 'manifest': f'languages/{language}/manifest.json',
                        'rows': [], 'assets': [], 'text_clear_boxes': [], 'notes': [], 'region_map': []})
    shutil.copyfile(output / 'languages' / languages[0] / 'source.png', output / 'source.png')
    project = {'schema_version': 1, 'source': 'source.png', 'source_sha256': sha256(output / 'source.png'),
               'background': {'operations': []}, 'protected_regions': [], 'working_scale': 6,
               'view_scale': 3, 'visual_targets': [], 'targets': entries, 'support_files': [],
               'notes': ['Agent must inspect source, complete semantic manifests, choose repair and typography, and specify visual targets. No automatic translation.']}
    write_json(output / 'job.json', project)
    return {'job': str((output / 'job.json').resolve()), 'status': 'pending',
            'languages': languages, 'next': 'Agent: packet each languages/LANGUAGE; fill semantic manifests and job.json before prepare-job.'}


def _load_job(path: Path) -> tuple[dict, list[str]]:
    project = read_json(path)
    _fields(project, JOB_KEYS, {'schema_version', 'source', 'source_sha256', 'background',
                              'protected_regions', 'visual_targets', 'targets'}, 'job')
    require(type(project['schema_version']) is int and project['schema_version'] == 1, 'Unsupported job version')
    source_path = _path(path.resolve().parent, project['source'])
    require(sha256(source_path) == project['source_sha256'], 'Job source hash mismatch')
    source = _image(source_path)
    require(isinstance(project['targets'], list), 'targets must be a list')
    languages = _languages([target.get('language') if isinstance(target, dict) else None
                            for target in project['targets']])
    validate_visual_targets(project['visual_targets'], source.size)
    require(bool(project['visual_targets']), 'Source-derived visual targets are required before preparation')
    require(isinstance(project.get('support_files', []), list), 'support_files must be a list')
    semantic_manifests = []
    for target in project['targets']:
        _fields(target, TARGET_KEYS, {'language', 'manifest', 'rows', 'assets', 'region_map'}, 'target')
        manifest = read_json(_path(path.resolve().parent, target['manifest']))
        validate_manifest(manifest)
        require(manifest['source']['sha256'] == project['source_sha256'], 'Target semantic source mismatch')
        require(manifest['target_language'] == target['language'], 'Target semantic language mismatch')
        require((manifest['canvas']['width'], manifest['canvas']['height']) == source.size, 'Semantic canvas mismatch')
        require(isinstance(target['rows'], list) and isinstance(target['assets'], list), 'rows/assets must be lists')
        validate_region_map(target, manifest)
        semantic_manifests.append((target['language'], manifest))
    for owner in [project, *project['targets']]:
        require(isinstance(owner.get('notes', []), list) and
                all(isinstance(note, str) for note in owner.get('notes', [])), 'notes must be a list of strings')
    project['protected_regions'] = merged_protections(project['protected_regions'], semantic_manifests, source.size)
    return project, languages


def _plan(project: dict, target: dict) -> dict:
    return {'schema_version': 1, 'source': project['source'], 'source_sha256': project['source_sha256'],
            'background': deepcopy(project['background']), 'rows': deepcopy(target['rows']),
            'assets': deepcopy(target['assets']), 'protected_regions': deepcopy(project['protected_regions']),
            'working_scale': project.get('working_scale', 6), 'view_scale': project.get('view_scale', 3),
            'visual_targets': deepcopy(project['visual_targets']), 'semantic_manifest': target['manifest'],
            'region_map': deepcopy(target['region_map']),
            'target_language': target['language'], 'background_review': 'background-review.json', 'text_clear_boxes': target.get('text_clear_boxes', []),
            'notes': [*project.get('notes', []), *target.get('notes', [])]}


def prepare_job(job_path: Path, output: Path) -> dict:
    require(not output.exists(), 'Prepared job already exists; choose another directory')
    project, languages = _load_job(job_path)
    base = job_path.resolve().parent
    output.mkdir(parents=True)
    resources = output / 'inputs'
    resources.mkdir()
    copied: dict[str, str] = {}
    originals: dict[str, str] = {}
    total_bytes = 0

    def snapshot(value: str, relative_base: Path = base) -> str:
        nonlocal total_bytes
        source = _path(relative_base, value)
        size = source.stat().st_size
        require(size <= MAX_BUNDLE_BYTES, 'Job input exceeds256MiB budget')
        digest = sha256(source)
        extension = source.suffix.lower()
        require(re.fullmatch(r'\.[a-z0-9]{1,10}', extension) is not None, 'Input must have a simple file extension')
        relative = f'inputs/{digest}{extension}'
        destination = output / relative
        if relative not in copied:
            require(total_bytes + size <= MAX_BUNDLE_BYTES, 'Job inputs exceed256MiB budget')
            shutil.copyfile(source, destination)
            require(sha256(destination) == digest == sha256(source), 'Input changed while snapshotting')
            copied[relative] = digest
            total_bytes += size
        originals[str(source)] = digest
        return relative

    frozen = deepcopy(project)
    frozen['source'] = snapshot(project['source'])
    background = frozen['background']
    if isinstance(background, dict) and 'path' in background:
        background['path'] = snapshot(project['background']['path'])
        background['mask'] = snapshot(project['background']['mask'])
    frozen['support_files'] = [snapshot(value) for value in project.get('support_files', [])]
    for target, original in zip(frozen['targets'], project['targets']):
        manifest_path = _path(base, original['manifest'])
        manifest = read_json(manifest_path)
        for region in manifest['regions']:
            if region['action'] == 'translate':
                # Preserve the declared semantic font dependency as well as actual row fonts.
                font = snapshot(region['style']['font_path'], manifest_path.parent)
                region['style']['font_path'] = '../' + font
        relative_manifest = f"semantics/{target['language']}.json"
        (output / 'semantics').mkdir(exist_ok=True)
        write_json(output / relative_manifest, manifest)
        target['manifest'] = relative_manifest
        originals[str(manifest_path)] = sha256(manifest_path)
        for row in target['rows']:
            require(isinstance(row, dict), 'Each row must be an object')
            row['font_path'] = snapshot(row.get('font_path'))
        for asset in target['assets']:
            require(isinstance(asset, dict), 'Each asset must be an object')
            asset['path'] = snapshot(asset.get('path'))
    write_json(output / 'job.json', frozen)
    # Plans live beside job.json so every frozen path stays relative to this package.
    for target in frozen['targets']:
        write_json(output / f"plan-{target['language']}.json", _plan(frozen, target))
    prepare_layered(output / f'plan-{languages[0]}.json', output / 'background')
    clean = output / 'background' / 'background.png'
    template = {'schema_version': 1, 'source_sha256': frozen['source_sha256'],
                'background_sha256': sha256(clean), 'receipt_sha256': sha256(output / 'background' / 'receipt.json'),
                'reviewer': '', 'review_kind': 'self', 'checks': {
                    'no_residual_text': None, 'seam_continuity': None, 'protected_content': None},
                'observations': '', 'issues': []}
    write_json(output / 'background-review-template.json', template)
    files = {str(path.relative_to(output).as_posix()): sha256(path)
             for path in output.rglob('*') if path.is_file() and path.name != 'render-timing.json'}
    write_json(output / 'bundle.json', {'schema_version': 1, 'files': files, 'origin_inputs': originals,
                                      'notice': 'Origin paths are provenance only. Frozen job inputs do not depend on those paths. Visual review is not automated.'})
    return {'prepared': str(output.resolve()), 'status': 'pending', 'languages': languages,
            'next': 'Actually inspect background. Copy background-review-template.json to background-review.json and record review before render-job.'}


def verify_bundle(prepared: Path) -> dict:
    bundle = read_json(prepared / 'bundle.json')
    require(bundle.get('schema_version') == 1 and isinstance(bundle.get('files'), dict), 'Invalid job bundle')
    required = {'job.json', 'background-review-template.json', 'background/receipt.json',
                'background/source.png', 'background/background.png', 'background/edit-mask.png',
                'background/background-plan.json', 'background/repair-layout.json'}
    root = prepared.resolve()

    def dependency(value: str, base: Path = root) -> Path:
        require(isinstance(value, str) and not Path(value).is_absolute(), 'Frozen dependency must be relative')
        path = (base / value).resolve()
        require(path.is_relative_to(root) and path.is_file(), 'Missing or escaped frozen dependency')
        relative = path.relative_to(root).as_posix()
        required.add(relative)
        require(relative in bundle['files'], f'Bundle omits dependency: {relative}')
        return path

    require(required <= set(bundle['files']), 'Bundle lacks required artifacts')
    for relative, digest in bundle['files'].items():
        require(isinstance(relative, str) and not Path(relative).is_absolute(), 'Invalid bundle path')
        path = (root / relative).resolve()
        require(path.is_relative_to(root) and path.is_file(), 'Missing or escaped bundle input')
        require(sha256(path) == digest, f'Frozen job input changed: {relative}')
    project, languages = _load_job(prepared / 'job.json')
    dependency(project['source'])
    if 'path' in project['background']:
        dependency(project['background']['path'])
        dependency(project['background']['mask'])
    for value in project.get('support_files', []):
        dependency(value)
    for target in project['targets']:
        manifest_path = dependency(target['manifest'])
        manifest = read_json(manifest_path)
        for region in manifest['regions']:
            if region['action'] == 'translate':
                dependency(region['style']['font_path'], manifest_path.parent)
        for row in target['rows']:
            dependency(row['font_path'])
        for asset in target['assets']:
            dependency(asset['path'])
        plan_path = dependency(f"plan-{target['language']}.json")
        require(read_json(plan_path) == _plan(project, target), 'Frozen plan differs from its job target')
    receipt = read_json(prepared / 'background' / 'receipt.json')
    for artifact in receipt['artifact_sha256']:
        dependency(artifact, root / 'background')
    require(required <= set(bundle['files']), 'Bundle dependency inventory is incomplete')
    return project


def _background_review(prepared: Path) -> dict:
    project = read_json(prepared / 'job.json')
    return verify_background_review(prepared / 'background-review.json',
                                    source_sha256=project['source_sha256'],
                                    background=prepared / 'background' / 'background.png',
                                    receipt=prepared / 'background' / 'receipt.json')


def _verify_prepared_background(prepared: Path) -> None:
    """Verify live preparation dependencies without rewriting historic receipts.

    Existing background receipt remains immutable. The relocated bundle is
    usable for reproduction via prepare-job job.json into a fresh directory.
    """
    _verify(prepared / 'background', 'background')


@timed_production(input_name='prepared', output_name='output')
def render_job(prepared: Path, output: Path) -> dict:
    require(not output.exists(), 'Render job output already exists')
    project = verify_bundle(prepared)
    _background_review(prepared)
    _verify_prepared_background(prepared)
    output.mkdir(parents=True)
    results = {}
    for target in project['targets']:
        language = target['language']
        results[language] = render_layered(prepared / f'plan-{language}.json', prepared / 'background', output / language)
        receipt_path = output / language / 'receipt.json'
        receipt = read_json(receipt_path)
        for dependency in ('background-review.json', 'bundle.json'):
            path = prepared / dependency
            receipt['input_sha256'][str(path.resolve())] = sha256(path)
        write_json(receipt_path, receipt)
    write_json(output / 'job-render.json', {'schema_version': 1, 'prepared': str(prepared.resolve()),
                                          'bundle_sha256': sha256(prepared / 'bundle.json'),
                                          'background_review_sha256': sha256(prepared / 'background-review.json'),
                                          'languages': list(results)})
    return {'output': str(output.resolve()), 'status': 'fail' if any(result['status'] == 'fail' for result in results.values()) else 'pending',
            'languages': results, 'next': 'Actually compare all language outputs; complete each current review.json, then check-job.'}


def check_job(output: Path) -> dict:
    try:
        record = read_json(output / 'job-render.json')
        prepared = Path(record['prepared'])
        project = verify_bundle(prepared)
        _background_review(prepared)
        require(record.get('bundle_sha256') == sha256(prepared / 'bundle.json'), 'Prepared job binding changed')
        require(record.get('background_review_sha256') == sha256(prepared / 'background-review.json'), 'Background review changed')
        expected = [target['language'] for target in project['targets']]
        require(record.get('languages') == expected, 'Rendered language inventory changed')
        for target in project['targets']:
            language = target['language']
            render_dir = output / language
            plan_path = prepared / f'plan-{language}.json'
            require(sha256(render_dir / 'plan.json') == sha256(plan_path), f'{language}: output belongs to another plan')
            require(read_json(render_dir / 'plan.json').get('target_language') == language,
                    f'{language}: output language mismatch')
            receipt = read_json(render_dir / 'receipt.json')
            require(receipt['input_sha256'].get(str(plan_path.resolve())) == sha256(plan_path),
                    f'{language}: receipt does not bind expected target plan')
            manifest_path = _path(prepared.resolve(), target['manifest'])
            require(receipt['input_sha256'].get(str(manifest_path)) == sha256(manifest_path),
                    f'{language}: receipt does not bind target semantics')
        results = {language: check_layered(output / language) for language in expected}
        statuses = {item['status'] for item in results.values()}
        status = 'fail' if 'fail' in statuses else 'pending' if 'pending' in statuses else 'pass'
        return {'status': status, 'languages': results, 'user_acceptance': 'not_recorded'}
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {'status': 'fail', 'reasons': [str(error)], 'user_acceptance': 'not_recorded'}
