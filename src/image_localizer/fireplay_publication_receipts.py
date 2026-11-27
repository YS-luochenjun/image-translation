"""Match reviewed uploads, material registration and binding in the publication journal."""
from pathlib import Path

from .fireplay import payload


EMPTY_DESIGNER_WARNING = '未识别到 DID，设计师为空'
FILENAME_ATTRIBUTION_WARNING = '文件名归属信息无法解析，已按未标注处理'
EXCLUDED_DESIGNERS = frozenset({'c74b62d9', 'b2fbcgab'})


def _items(value):
    return value if isinstance(value, list) else []


def _rows(value):
    return [item for item in _items(value) if isinstance(item, dict)]


def _has_warnings(result, designers, *, allow_import_attribution=False):
    warnings = result.get('warnings')
    if not warnings:
        return False
    allowed = (EMPTY_DESIGNER_WARNING,) if designers == [] or allow_import_attribution else ()
    if allow_import_attribution:
        allowed += (FILENAME_ATTRIBUTION_WARNING,)
    return not isinstance(warnings, list) or any(warning not in allowed for warning in warnings)


def _output_key(base, output, by_file):
    return by_file.get(((base / output['path']).resolve(), output['sha256']))


def _import_urls(result):
    if isinstance(result.get('urls'), list):
        return [url for url in result['urls'] if isinstance(url, str)]
    return [result['url']] if isinstance(result.get('url'), str) else []


def receipt_coverage(base: Path, requirement, operations, frozen, records, response_for):
    designers = requirement.get('designer_ids')
    departments = requirement.get('shared_dept_ids')
    if (not isinstance(designers, list) or not isinstance(departments, list) or
            not departments or not all(isinstance(value, str) and value for value in designers + departments) or
            EXCLUDED_DESIGNERS.intersection(designers)):
        return {}

    by_file = {((base / upload['path']).resolve(), upload['sha256']): key for key, upload in frozen.items()}
    materials, attributed, bound, binding_issues, coverage = {}, {}, set(), set(), {}
    pending_import_attribution = set()
    for entry in operations.values():
        tool = entry['tool']
        if entry['state'] != 'received' or tool not in (
                'material_upload_from_url', 'material_append_images', 'material_attribution_update',
                'requirement_bind_materials'):
            continue
        if tool == 'requirement_bind_materials':
            binding_issues.update(entry.get('binding_material_ids', []))
        try:
            response = payload(response_for(base, entry))
        except (TypeError, ValueError, KeyError, OSError):
            continue
        if response.get('success') is False and not _rows(response.get('results')):
            continue
        if tool == 'material_attribution_update':
            intent = entry.get('attribution') or {}
            if response.get('success') is True and not response.get('unmatched_count') and not _has_warnings(response, designers):
                for material_id in _items(response.get('updated')):
                    if isinstance(material_id, str):
                        matched = material_id in _items(intent.get('material_ids'))
                        attributed[material_id] = intent if matched else None
                        if matched and intent.get('creation_type') == 'derivative':
                            pending_import_attribution.discard(material_id)
            continue
        if tool == 'requirement_bind_materials':
            for item in _rows(response.get('results')):
                if (item.get('success') is True and item.get('requirement_id') == requirement['id'] and
                        item.get('requirementId', requirement['id']) == requirement['id'] and
                        not _has_warnings(response, designers) and not _has_warnings(item, designers)):
                    bound = {value for value in _items(item.get('materialIds')) if isinstance(value, str)}
                    binding_issues.difference_update(bound)
            continue
        if tool == 'material_append_images':
            for output in entry.get('outputs', []):
                key = _output_key(base, output, by_file)
                if key is not None:
                    coverage[key] = (None, False)
            continue

        groups = entry.get('output_groups') or [entry.get('outputs', [])]
        results = _rows(response.get('results'))
        for index, group in enumerate(groups):
            keys = [_output_key(base, output, by_file) for output in group]
            items = [item for item in results
                     if type(item.get('item_index')) is int and item['item_index'] == index]
            item = items[0] if len(items) == 1 else {}
            material_id = item.get('material_id')
            if item.get('success') is False and not material_id:
                continue
            known_keys = [key for key in keys if key is not None]
            if item.get('success') is not True or not isinstance(material_id, str) or not material_id:
                coverage.update({key: (None, False) for key in known_keys})
                continue
            if material_id in materials:
                materials[material_id] = None
                coverage.update({key: (material_id, False) for key in known_keys})
                continue
            materials[material_id] = item
            uploads = [frozen[key] for key in known_keys]
            count = item.get('count', 1 if requirement['asset_type'] == 'button' and item.get('url') else None)
            planned_groups = {records.get(key, {}).get('group_id') for key in keys}
            clean = (bool(keys) and type(count) is int and len(keys) == len(uploads) == len(set(keys)) == count and
                     len({key[1] for key in keys}) == 1 and len(planned_groups) == 1 and
                     all(upload['resource_id'] and upload['url'] and not upload['warnings'] for upload in uploads) and
                     sorted(_import_urls(item)) == sorted(upload['url'] for upload in uploads) and
                     item.get('type') == requirement['asset_type'] and
                     item.get('shared_dept_id') in departments and
                     not _has_warnings(response, designers, allow_import_attribution=True) and
                     not _has_warnings(item, designers, allow_import_attribution=True))
            warnings = _items(response.get('warnings')) + _items(item.get('warnings'))
            if clean and (FILENAME_ATTRIBUTION_WARNING in warnings or
                          (designers and EMPTY_DESIGNER_WARNING in warnings)):
                pending_import_attribution.add(material_id)
            for key in known_keys:
                if key in coverage:
                    coverage[key] = (None, False)
                else:
                    coverage[key] = (material_id, clean)

    result = {}
    for key, (material_id, clean) in coverage.items():
        action = 'verify'
        if clean and materials[material_id] is not None and (material_id not in attributed or attributed[material_id] is not None):
            item = materials[material_id]
            attribution = attributed.get(material_id, {})
            observed_designers = item.get('designers')
            if isinstance(observed_designers, list):
                observed_designers = [value.get('id') if isinstance(value, dict) else value
                                      for value in observed_designers]
            observed_designers = attribution.get('designer_ids', observed_designers)
            creation_type = attribution.get('creation_type', item.get('creation_type'))
            ownership_ok = (creation_type == 'derivative' and isinstance(observed_designers, list) and
                            all(isinstance(value, str) for value in observed_designers) and
                            sorted(observed_designers) == sorted(designers))
            if not ownership_ok or material_id in pending_import_attribution:
                action = 'attribute'
            elif material_id not in binding_issues:
                action = 'delivery_verified' if material_id in bound else 'bind'
        result[key] = {'action': action, 'material_id': material_id}
    return result
