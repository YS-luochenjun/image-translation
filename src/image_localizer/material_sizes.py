"""Read-only requirement-scoped size planning and file validation; no rendering or upload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

POLICY = Path(__file__).resolve().parents[2] / 'docs' / 'material-size-policy.json'
SCOPE_FIELDS = ('delivery_mode', 'scope_basis', 'reference_members', 'reference_evidence_path', 'explicit_members')


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Invalid size-spec fields')


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Expected nonempty text')
    return value


def _sizes(value):
    if not isinstance(value, list) or not value:
        raise ValueError('Expected nonempty sizes')
    result = []
    for size in value:
        if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or n <= 0 for n in size):
            raise ValueError('Size must be two positive integer pixels')
        if size in result:
            raise ValueError('Duplicate size')
        result.append(size)
    return result


def _reference(value, base):
    _keys(value, ('requirement_id', 'material_id', 'evidence_path', 'sizes', 'same_group_same_type'),
          ('requirement_id', 'material_id', 'evidence_path', 'sizes', 'same_group_same_type'))
    if value['same_group_same_type'] is not True:
        raise ValueError('Historical reference must attest same group and same type')
    for key in ('requirement_id', 'material_id', 'evidence_path'):
        _text(value[key])
    if not (base / value['evidence_path']).is_file():
        raise ValueError('Missing historical evidence file')
    return _sizes(value['sizes'])


def _image_role(size, policy):
    width, height = size
    choices = [(abs(width / height - ratio), role) for role, ratios in policy['ratios'].items() for ratio in ratios]
    distance, role = min(choices)
    if distance > 0.1:
        raise ValueError(f'Unsupported image aspect ratio: {size}')
    minimum = policy['minimums'][role]
    if width < minimum[0] or height < minimum[1]:
        raise ValueError(f'Image below {role} minimum: {size}')
    return role


def _legal_sizes(sizes, asset_type, policy):
    if asset_type == 'button':
        if any(size not in policy['whitelist'] for size in sizes):
            raise ValueError('Button size outside official whitelist')
    elif asset_type != 'image' and any(min(abs(w / h - ratio) for ratio in policy['ratios']) > 0.1 for w, h in sizes):
        raise ValueError('Unsupported material aspect ratio')


def _load(path):
    path = Path(path).resolve()
    spec = json.loads(path.read_text(encoding='utf-8'))
    _keys(spec, ('asset_type', 'explicit_sizes', 'explicit_specification', 'historical_reference', 'sources', 'outputs') + SCOPE_FIELDS,
          ('asset_type', 'sources'))
    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    if spec['asset_type'] not in policy['types']:
        raise ValueError('Unknown requirement asset type')
    if not isinstance(spec['sources'], list) or not spec['sources']:
        raise ValueError('At least one source is required')
    return spec, path.parent, policy


def _image_members(source, spec, base, width, height):
    def field(key):
        return source.get(key, spec.get(key))

    mode = field('delivery_mode')
    explicit = field('explicit_sizes')
    explicit_members = field('explicit_members')
    if mode == 'explicit_members':
        if explicit is not None or field('reference_members') is not None or field('reference_evidence_path') is not None:
            raise ValueError('Conflicting explicit member scope')
        basis = _text(field('scope_basis'))
        if not isinstance(explicit_members, list) or not explicit_members:
            raise ValueError('Explicit members must be nonempty')
        seen = set()
        for member in explicit_members:
            _keys(member, ('member_id', 'width', 'height'), ('member_id', 'width', 'height'))
            identifier = _text(member['member_id'])
            if identifier in seen:
                raise ValueError('Duplicate explicit member ID')
            seen.add(identifier)
            _sizes([[member['width'], member['height']]])
        return [dict(member) for member in explicit_members], 'explicit_requirement', basis, mode
    if explicit_members is not None:
        raise ValueError('Explicit members require explicit_members delivery_mode')
    if explicit is not None:
        if mode not in (None, 'explicit_sizes'):
            raise ValueError('Conflicting image scope: explicit_sizes and delivery_mode')
        basis = _text(field('explicit_specification'))
        return [{'member_id': f'{w}x{h}', 'width': w, 'height': h} for w, h in _sizes(explicit)], 'explicit_requirement', basis, 'explicit_sizes'
    if mode not in ('provided_source_set', 'reference_seven_set'):
        raise ValueError('scope_unresolved: image requires provided_source_set, evidenced reference_seven_set, or explicit_sizes with user basis; no automatic three/seven expansion')
    basis = _text(field('scope_basis'))
    if mode == 'provided_source_set':
        if field('reference_members') is not None or field('reference_evidence_path') is not None:
            raise ValueError('Conflicting image scope: provided source set cannot use reference members')
        return [{'member_id': source['id'], 'width': width, 'height': height}], 'provided_source_set', basis, mode
    evidence = _text(field('reference_evidence_path'))
    if not (base / evidence).is_file():
        raise ValueError('Missing seven-member reference evidence file')
    members = field('reference_members')
    if not isinstance(members, list) or len(members) != 7:
        raise ValueError('reference_seven_set requires seven explicit members, not three unique sizes')
    ids, resources = set(), set()
    for member in members:
        _keys(member, ('member_id', 'width', 'height', 'resource_id'), ('member_id', 'width', 'height', 'resource_id'))
        identifier, resource = _text(member['member_id']), _text(member['resource_id'])
        if identifier in ids or resource in resources:
            raise ValueError('Duplicate reference member or resource ID')
        ids.add(identifier)
        resources.add(resource)
        _sizes([[member['width'], member['height']]])
    return [dict(member, reference_evidence_path=evidence) for member in members], 'reference_seven_set', basis, mode


def plan_sizes(spec_path):
    spec, base, policy = _load(spec_path)
    asset_type = spec['asset_type']
    rules = policy['types'][asset_type]
    if rules.get('supported') is False:
        return {'status': 'unsupported', 'asset_type': asset_type, 'reason': rules['reason'], 'items': []}
    items, ids = [], set()
    for source in spec['sources']:
        _keys(source, ('id', 'path', 'languages', 'explicit_sizes', 'explicit_specification', 'historical_reference') + SCOPE_FIELDS,
              ('id', 'path', 'languages'))
        identifier = _text(source['id'])
        if identifier in ids:
            raise ValueError('Duplicate source ID')
        ids.add(identifier)
        languages = source['languages']
        if not isinstance(languages, list) or not languages or any(not isinstance(v, str) or not v.strip() for v in languages) or len(set(languages)) != len(languages):
            raise ValueError('Languages must be unique nonempty strings; enumerate each once')
        with Image.open(base / _text(source['path'])) as image:
            width, height = image.size
        basis, mode = None, None
        if asset_type == 'image':
            members, authority, basis, mode = _image_members(source, spec, base, width, height)
        else:
            explicit = source.get('explicit_sizes', spec.get('explicit_sizes'))
            reference = source.get('historical_reference', spec.get('historical_reference'))
            if explicit is not None:
                basis = _text(source.get('explicit_specification', spec.get('explicit_specification')))
                sizes, authority = _sizes(explicit), 'explicit_requirement'
            elif reference is not None:
                sizes, authority = _reference(reference, base), 'same_group_same_type_delivered_reference'
            else:
                defaults = rules['defaults']
                sizes = defaults['portrait' if width / height < rules['portrait_below_ratio'] else 'landscape_or_square'] if asset_type == 'button' else defaults
                authority = 'project_default'
            _legal_sizes(sizes, asset_type, rules)
            members = [{'member_id': f'{w}x{h}', 'width': w, 'height': h} for w, h in sizes]
        for language in languages:
            for member in members:
                item = dict(member, source_id=identifier, language=language, authority=authority,
                            source_width=width, source_height=height,
                            adaptation='recompose_or_contain_no_stretch', max_bytes=rules['max_bytes'])
                if basis is not None:
                    item['scope_basis'] = basis
                if authority == 'explicit_requirement':
                    item['explicit_specification'] = basis
                if asset_type == 'image':
                    item['delivery_mode'] = mode
                    try:
                        item['role'] = _image_role([member['width'], member['height']], rules)
                        item['official_size_check'] = {'status': 'pass'}
                    except ValueError as error:
                        item['official_size_check'] = {'status': 'conflict', 'reason': str(error)}
                items.append(item)
    if rules.get('uniform_group_sizes'):
        if len({(item['width'], item['height']) for item in items}) != 1:
            raise ValueError('TK carousel cards must use one uniform group size')
    return {'status': 'planned', 'asset_type': asset_type, 'policy_version': policy['version'],
            'official_required_roles': rules.get('official_required_roles', []),
            'scope': policy['scope'], 'expected_resource_count': len(items), 'items': items,
            'note': 'Image scope follows explicit requirement members; no automatic three/seven expansion. Platform checks do not define delivery scope.'}


def validate_sizes(spec_path):
    plan = plan_sizes(spec_path)
    if plan['status'] == 'unsupported':
        return plan
    spec, base, policy = _load(spec_path)
    outputs = spec.get('outputs')
    if not isinstance(outputs, list):
        raise ValueError('outputs is required for validation')
    expected = {(i['source_id'], i['language'], i['member_id'], i['width'], i['height']): i for i in plan['items']}
    found, records, errors, used_paths = set(), [], [], {}
    for output in outputs:
        _keys(output, ('source_id', 'language', 'member_id', 'path', 'adaptation'), ('source_id', 'language', 'path', 'adaptation'))
        for key in ('source_id', 'language', 'path'):
            _text(output[key])
        if 'member_id' in output:
            _text(output['member_id'])
        if output['adaptation'] not in ('contain', 'recompose', 'same_ratio'):
            raise ValueError('Use contain or recompose; stretch is forbidden')
        path = (base / output['path']).resolve()
        with Image.open(path) as image:
            size, image_format = image.size, image.format
            image.verify()
        matches = [key for key in expected if key[0] == output['source_id'] and key[1] == output['language']
                   and key[3:] == size and ('member_id' not in output or key[2] == output['member_id'])]
        key = matches[0] if len(matches) == 1 else None
        item = expected.get(key)
        if item is None:
            errors.append({'path': str(path), 'reason': 'unexpected or ambiguous source/language/member/dimensions; identify each same-size member'})
        elif key in found:
            errors.append({'path': str(path), 'reason': 'duplicate matrix item'})
        elif item.get('delivery_mode') in ('reference_seven_set', 'explicit_members') and path in used_paths and used_paths[path] != (key[0], key[2]):
            errors.append({'path': str(path), 'reason': 'reference members require separate output files'})
        else:
            found.add(key)
            used_paths[path] = (key[0], key[2])
            if output['adaptation'] == 'same_ratio' and abs(size[0] / size[1] - item['source_width'] / item['source_height']) > 0.001:
                errors.append({'path': str(path), 'reason': 'changed ratio requires contain/recompose evidence'})
        data = path.read_bytes()
        if image_format not in policy['static_formats']:
            errors.append({'path': str(path), 'reason': 'static format must be PNG or JPEG'})
        if len(data) > policy['types'][spec['asset_type']]['max_bytes']:
            errors.append({'path': str(path), 'reason': 'file exceeds byte limit'})
        record = {'path': str(path), 'source_id': output['source_id'], 'language': output['language'],
                  'width': size[0], 'height': size[1], 'format': image_format, 'bytes': len(data),
                  'sha256': hashlib.sha256(data).hexdigest(), 'adaptation_declared': output['adaptation']}
        if item is not None:
            record['member_id'] = item['member_id']
        records.append(record)
    missing = []
    for key in sorted(expected.keys() - found):
        entry = dict(source_id=key[0], language=key[1], width=key[3], height=key[4])
        if expected[key].get('delivery_mode') in ('reference_seven_set', 'explicit_members'):
            entry['member_id'] = key[2]
        missing.append(entry)
    return {'status': 'pass' if not errors and not missing else 'fail', 'expected_resource_count': len(expected),
            'covered_resource_count': len(found), 'missing': missing, 'errors': errors, 'outputs': records,
            'scope_items': plan['items'],
            'note': 'Technical scope/format/byte coverage only; official_size_check is separate. Review every current file SHA visually; existing valid files need not be remade.'}
