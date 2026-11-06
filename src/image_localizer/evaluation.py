"""Immutable local comparison evidence; pixel checks never certify visual quality."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import textwrap
import warnings

from PIL import Image, ImageChops, ImageDraw

from .contracts import box, require

MAX_SPEC_BYTES = 256_000
MAX_FILE_BYTES = 40_000_000
MAX_IMAGE_PIXELS = 16_000_000
MAX_TOTAL_INPUT_PIXELS = 48_000_000
MAX_OUTPUT_PIXELS = 80_000_000
MAX_CANDIDATES = 8
MAX_REGIONS = 32
MAX_CASES = 12
MAX_BENCHMARK_PIXELS = 120_000_000
LABEL_HEIGHT = 64
MIN_PANEL_WIDTH = 256
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
VISUAL_TARGETS = (
    'semantic fidelity and completeness',
    'letterform weight, proportions and spacing',
    'material, depth, contact edges and shadow direction/softness',
    'background restoration and protected subject integrity',
    'composition, hierarchy and full-image harmony',
)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _read_bytes(path: Path, maximum: int) -> bytes:
    require(path.is_file(), f'Missing local input: {path}')
    require(path.stat().st_size <= maximum, f'File exceeds byte budget: {path}')
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, f'File exceeds byte budget: {path}')
    return data


def _object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f'Non-finite JSON value: {value}')


def _load_spec(path: Path) -> tuple[dict, bytes]:
    data = _read_bytes(path, MAX_SPEC_BYTES)
    try:
        spec = json.loads(data.decode('utf-8'), object_pairs_hook=_object, parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f'Invalid UTF-8 JSON spec: {path}') from error
    require(isinstance(spec, dict), 'Spec must be an object')
    require(type(spec.get('schema_version')) is int and spec['schema_version'] == 1,
            'Expected schema_version 1')
    return spec, data


def _keys(value: object, required: set, optional: set, label: str) -> None:
    require(isinstance(value, dict), f'{label} must be an object')
    require(required <= set(value) and set(value) <= required | optional, f'Invalid {label} fields')


def _identifier(value: object) -> None:
    require(isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None,
            'ID must be 1..64 safe ASCII characters')


def _strings(value: object, label: str) -> None:
    require(isinstance(value, list) and len(value) <= 64 and
            all(isinstance(item, str) and len(item) <= 4000 for item in value),
            f'{label} must be at most 64 strings of at most 4000 characters')


def _local(base: Path, value: object) -> Path:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 4096 and
            '://' not in value and '\x00' not in value, 'Expected local path')
    return (base / value).resolve()


def _image(path: Path) -> tuple[Image.Image, dict]:
    data = _read_bytes(path, MAX_FILE_BYTES)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                require(image.width * image.height <= MAX_IMAGE_PIXELS, 'Image exceeds pixel budget')
                require(getattr(image, 'n_frames', 1) == 1, 'Image must be single-frame')
                require(image.getexif().get(274, 1) == 1, 'Normalize EXIF orientation before comparison')
                rgba = image.convert('RGBA')
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError(f'Invalid or unsafe image: {path}') from error
    return rgba, {'path': str(path), 'sha256': _digest(data),
                  'rgba_sha256': _digest(rgba.tobytes()), 'size': list(rgba.size)}


def _regions(value: object, size: tuple[int, int], label: str, *, nonempty: bool = False) -> list:
    require(isinstance(value, list) and int(nonempty) <= len(value) <= MAX_REGIONS,
            f'{label} count must be {int(nonempty)}..{MAX_REGIONS}')
    identifiers = set()
    for region in value:
        _keys(region, {'id', 'bbox'}, set(), label)
        _identifier(region['id'])
        require(region['id'] not in identifiers, f'Duplicate {label} ID')
        identifiers.add(region['id'])
        box(region['bbox'], *size, region['id'])
    return value


def _rectangle(region: dict) -> tuple[int, int, int, int]:
    x, y, width, height = region['bbox']
    return x, y, x + width, y + height


def _output_budget(size: tuple[int, int], count: int, regions: list) -> int:
    width, height = size
    panels = (count + 1) * max(width, MIN_PANEL_WIDTH) * (height + LABEL_HEIGHT)
    crops = sum((count + 1) * max(r['bbox'][2], MIN_PANEL_WIDTH) *
                (r['bbox'][3] + LABEL_HEIGHT) for r in regions)
    return panels + crops + count * width * height


def _prepare(spec: dict, base: Path) -> dict:
    _keys(spec, {'schema_version', 'source', 'candidates', 'regions'}, {'protected_regions', 'notes'}, 'comparison')
    _strings(spec.get('notes', []), 'notes')
    candidates = spec['candidates']
    require(isinstance(candidates, list) and 1 <= len(candidates) <= MAX_CANDIDATES,
            f'Candidate count must be 1..{MAX_CANDIDATES}')
    identifiers = set()
    for candidate in candidates:
        _keys(candidate, {'id', 'path'}, set(), 'candidate')
        _identifier(candidate['id'])
        require(candidate['id'] not in identifiers, 'Duplicate candidate ID')
        identifiers.add(candidate['id'])
    source, source_record = _image(_local(base, spec['source']))
    regions = _regions(spec['regions'], source.size, 'regions', nonempty=True)
    protected = _regions(spec.get('protected_regions', []), source.size, 'protected_regions')
    output_pixels = _output_budget(source.size, len(candidates), regions)
    require(output_pixels <= MAX_OUTPUT_PIXELS, 'Comparison exceeds output pixel budget')
    require(source.width * source.height * (len(candidates) + 1) <= MAX_TOTAL_INPUT_PIXELS,
            'Comparison exceeds total input pixel budget')
    loaded = []
    for candidate in candidates:
        image, record = _image(_local(base, candidate['path']))
        require(image.size == source.size, 'All images must have the same canvas size')
        loaded.append((candidate['id'], image, record))
    return {'source': source, 'source_record': source_record, 'candidates': loaded,
            'regions': regions, 'protected': protected, 'output_pixels': output_pixels}


def _mask(source: Image.Image, candidate: Image.Image) -> Image.Image:
    difference = ImageChops.difference(source, candidate)
    mask = Image.new('L', source.size)
    for band in difference.split():
        mask = ImageChops.lighter(mask, band)
    return mask.point(lambda value: 255 if value else 0)


def _count(mask: Image.Image) -> int:
    return sum(mask.histogram()[1:])


def _board(panels: list[tuple[str, Image.Image]], path: Path) -> None:
    width, height = panels[0][1].size
    panel_width = max(width, MIN_PANEL_WIDTH)
    board = Image.new('RGB', (panel_width * len(panels), height + LABEL_HEIGHT), '#222222')
    draw = ImageDraw.Draw(board)
    for index, (label, image) in enumerate(panels):
        # Padding gives even tiny crops readable labels without rescaling pixels.
        plate = Image.new('RGBA', image.size, 'white')
        plate.alpha_composite(image)
        board.paste(plate.convert('RGB'), (index * panel_width, LABEL_HEIGHT))
        draw.multiline_text((index * panel_width + 3, 6), '\n'.join(textwrap.wrap(label, 24)),
                            fill='white', spacing=3)
    board.save(path)


def _write_json(path: Path, value: dict) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _unchanged(records: list[dict]) -> None:
    for record in records:
        require(_digest(_read_bytes(Path(record['path']), MAX_FILE_BYTES)) == record['sha256'],
                f'Input changed during evaluation: {record["path"]}')


def _emit(spec_path: Path, spec_data: bytes, spec: dict, prepared: dict, output: Path) -> dict:
    source = prepared['source']
    candidates = prepared['candidates']
    records = [prepared['source_record'], *[record for _, _, record in candidates]]
    _unchanged(records)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'regions').mkdir()
    (output / 'differences').mkdir()
    panels = [('source', source), *[(identifier, image) for identifier, image, _ in candidates]]
    _board(panels, output / 'board.png')
    crop_records = []
    for index, region in enumerate(prepared['regions']):
        name = f'regions/{index:03d}.png'
        _board([(label, image.crop(_rectangle(region))) for label, image in panels], output / name)
        crop_records.append({**region, 'artifact': name})
    candidate_reports = []
    review_candidates = []
    for index, (identifier, image, record) in enumerate(candidates):
        difference = _mask(source, image)
        name = f'differences/{index:03d}.png'
        difference.save(output / name)
        protected = {region['id']: _count(difference.crop(_rectangle(region)))
                     for region in prepared['protected']}
        candidate_reports.append({'id': identifier, **record, 'difference': name,
                                  'changed_pixels': _count(difference), 'protected_changed_pixels': protected,
                                  'technical': 'fail' if any(protected.values()) else 'pass',
                                  'visual_review': 'pending'})
        reviews = []
        scopes = [('full_image', None), *[('region', r['id']) for r in prepared['regions']]]
        for scope, region_id in scopes:
            reviews.append({'scope': scope, 'region_id': region_id, 'status': 'pending', 'style_comparison': [
                {'critical_visual_target': target, 'source_observation': None,
                 'target_observation': None, 'verdict': None} for target in VISUAL_TARGETS]})
        review_candidates.append({'id': identifier, 'sha256': record['sha256'], 'regions': reviews})
    binding = {'spec': {'path': str(spec_path.resolve()), 'sha256': _digest(spec_data)},
               'source': prepared['source_record'],
               'candidates': [{'id': identifier, **record} for identifier, _, record in candidates]}
    _write_json(output / 'spec.json', spec)
    _write_json(output / 'review-template.json', {
        'schema_version': 1, 'binding': binding, 'status': 'pending', 'reviewer': None,
        'instructions': 'View source and target at native scale and full image; record actual observations. '
                        'Technical pixel checks do not establish semantic or aesthetic quality.',
        'candidates': review_candidates})
    _unchanged(records)
    require(_digest(_read_bytes(spec_path, MAX_SPEC_BYTES)) == _digest(spec_data), 'Spec changed during evaluation')
    artifacts = {p.relative_to(output).as_posix(): _digest(p.read_bytes())
                 for p in sorted(output.rglob('*')) if p.is_file()}
    report = {'schema_version': 1, 'binding': binding, 'canvas': list(source.size),
              'technical': 'fail' if any(c['technical'] == 'fail' for c in candidate_reports) else 'pass',
              'visual_review': 'pending', 'status': 'pending',
              'pixel_difference_semantics': 'Exact RGBA changed-pixel mask; changes outside protected regions '
                                            'are descriptive and do not imply failure or visual quality.',
              'board': 'board.png', 'regions': crop_records, 'protected_regions': prepared['protected'],
              'candidates': candidate_reports, 'notes': spec.get('notes', []), 'artifact_sha256': artifacts}
    _write_json(output / 'report.json', report)
    return report


def build_comparison(spec_path: Path, output: Path) -> dict:
    """Build native-scale boards in a new directory; invalid inputs raise ValueError."""
    spec_path, output = Path(spec_path).resolve(), Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), 'Output already exists; choose a new directory')
    spec, data = _load_spec(spec_path)
    prepared = _prepare(spec, spec_path.parent)
    return _emit(spec_path, data, spec, prepared, output)


def run_benchmark(spec_path: Path, output: Path) -> dict:
    """Run bounded cases; known visual defects are supplied labels, not detections."""
    spec_path, output = Path(spec_path).resolve(), Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), 'Output already exists; choose a new directory')
    spec, data = _load_spec(spec_path)
    _keys(spec, {'schema_version', 'cases'}, {'notes'}, 'benchmark')
    _strings(spec.get('notes', []), 'notes')
    cases = spec['cases']
    require(isinstance(cases, list) and 1 <= len(cases) <= MAX_CASES, 'Invalid benchmark case count')
    identifiers, jobs, total_pixels = set(), [], 0
    for case in cases:
        _keys(case, {'id', 'source', 'candidate', 'expected_technical', 'known_visual_defects'},
              {'protected_regions'}, 'benchmark case')
        _identifier(case['id'])
        require(case['id'] not in identifiers, 'Duplicate case ID')
        identifiers.add(case['id'])
        require(case['expected_technical'] in ('pass', 'fail'), 'Invalid expected_technical')
        _strings(case['known_visual_defects'], 'known_visual_defects')
        source_path = _local(spec_path.parent, case['source'])
        source, _ = _image(source_path)
        comparison = {'schema_version': 1, 'source': str(source_path),
                      'candidates': [{'id': case['id'], 'path': str(_local(spec_path.parent, case['candidate']))}],
                      'regions': [{'id': 'whole_canvas', 'bbox': [0, 0, *source.size]}],
                      'protected_regions': case.get('protected_regions', []),
                      'notes': ['Benchmark labels in the benchmark report are supplied provenance, '
                                'not automated visual detections.']}
        prepared = _prepare(comparison, spec_path.parent)
        total_pixels += prepared['output_pixels'] + source.width * source.height * 2
        require(total_pixels <= MAX_BENCHMARK_PIXELS, 'Benchmark exceeds aggregate pixel budget')
        records = [prepared['source_record'], *[record for _, _, record in prepared['candidates']]]
        jobs.append((case, comparison, records))
        # Preflight does not retain every decoded canvas in memory.
        del prepared, source
    require(_digest(_read_bytes(spec_path, MAX_SPEC_BYTES)) == _digest(data), 'Spec changed during evaluation')
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for index, (case, comparison, records) in enumerate(jobs):
        _unchanged(records)
        case_dir = output / f'{index:03d}-{case["id"]}'
        case_dir.mkdir()
        case_spec = case_dir / 'spec.json'
        _write_json(case_spec, comparison)
        result = build_comparison(case_spec, case_dir / 'comparison')
        _unchanged(records)
        results.append({'id': case['id'], 'report': f'{case_dir.name}/comparison/report.json',
                        'report_sha256': _digest((case_dir / 'comparison/report.json').read_bytes()),
                        'binding': result['binding'], 'technical': result['technical'],
                        'expected_technical': case['expected_technical'],
                        'expectation_matched': result['technical'] == case['expected_technical'],
                        'known_visual_defects': case['known_visual_defects'],
                        'known_visual_defects_provenance': 'Supplied benchmark labels; requires actual visual review',
                        'visual_review': 'pending'})
    report = {'schema_version': 1, 'spec': {'path': str(spec_path), 'sha256': _digest(data)},
              'cases': results, 'technical_expectations': 'pass' if all(r['expectation_matched'] for r in results) else 'fail',
              'visual_review': 'pending', 'status': 'pending', 'notes': spec.get('notes', [])}
    require(_digest(_read_bytes(spec_path, MAX_SPEC_BYTES)) == _digest(data), 'Spec changed during evaluation')
    _write_json(output / 'report.json', report)
    return report
