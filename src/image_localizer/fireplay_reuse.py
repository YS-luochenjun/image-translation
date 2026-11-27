"""Read-only historical image discovery with exact original-byte matching."""
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import fireplay
from .contracts import require
from .delivery_autopilot import next_delivery
from .fireplay_download import MAX_BYTES, _inspect, transfer_public_bytes
from .pipeline import read_json, sha256


def _source_download(root, record, source):
    row = record.get('downloads', {}).get(str(source['index']))
    require(isinstance(row, dict) and row.get('content_version') == record.get('content_version'),
            'Download the requested original for this content version first')
    folder = fireplay.request_folder(record['code'], root) / 'sources'
    path = (root / row['local_path']).resolve(strict=True)
    require(path.is_relative_to(folder) and path.is_file(), 'Original must be in the registered sources directory')
    actual = _inspect(path)
    require(all(actual[key] == row.get(key) for key in ('sha256', 'bytes', 'width', 'height', 'format')),
            'Original file disagrees with the recorded download')
    return path, actual


def _history(root, original, current, asset_type):
    candidates = []
    unresolved = []
    for request_path in sorted((root / 'state/fireplay').glob('*/request.json')):
        code = request_path.parent.name
        if code == current['code'] or request_path.resolve() != request_path:
            continue
        matches = []
        try:
            historical = fireplay.load_record(code, root)
            if historical['requirement_id'] == current['requirement_id']:
                continue
            matches = [source for source in historical['sources']
                       if historical.get('downloads', {}).get(str(source['index']), {}).get('sha256') == original]
            if not matches:
                continue
            confirmed = read_json(request_path.with_name('status-finish.json'))
            version = historical['content_version']
            if (confirmed.get('state') != 'confirmed' or confirmed.get('code') != code
                    or confirmed.get('requirement_id') != historical['requirement_id']
                    or confirmed.get('content_version') != version):
                continue
            folder = root / 'work/fireplay-production' / code
            state_path = folder / 'delivery-state.json'
            require(state_path.resolve() == state_path, 'Historical delivery state must stay in its requirement directory')
            state = read_json(state_path)
            state_paths = [state_path, *(folder / name for name in state.get('supplemental_delivery_states', []))]
            original_paths = {_source_download(root, historical, source)[0] for source in matches}
            staged = []
            for state_path in state_paths:
                require(state_path.resolve() == state_path and state_path.parent == folder,
                        'Historical delivery state must stay in its requirement directory')
                state = read_json(state_path)
                if state.get('content_version') != version or state['requirement']['asset_type'] != asset_type:
                    continue
                spec_path = (folder / state['size_spec']).resolve(strict=True)
                require(spec_path.is_relative_to(root), 'Historical size spec escapes the project')
                spec = read_json(spec_path)
                source_ids = set()
                for row in spec['sources']:
                    path = (spec_path.parent / row['path']).resolve()
                    require(path.is_relative_to(root), 'Historical source escapes the project')
                    if path in original_paths or (path.is_file() and sha256(path) == original):
                        source_ids.add(row['id'])
                if not source_ids:
                    continue
                report = next_delivery(state_path, include_verified=True)
                if (report['requirement_id'] != historical['requirement_id'] or report['code'] != code
                        or not report['complete'] or report['publication_unknown']):
                    continue
                for row in report['verified_deliveries']:
                    if row['source_id'] in source_ids and row['material_id'] in confirmed.get('verified_material_ids', []):
                        staged.append({**row, 'historical_code': code, 'historical_requirement_id': report['requirement_id']})
            candidates.extend(staged)
        except FileNotFoundError:
            if matches:
                unresolved.append(code)
        except (ValueError, OSError, TypeError, KeyError):
            unresolved.append(code)
    unique = {(row['historical_requirement_id'], row['source_id'], row['language'], row['member_id'],
               row['material_id'], row['resource_id']): row for row in candidates}
    return list(unique.values()), unresolved


def _official_candidates(original_path, original, lookup):
    require(lookup['source_sha256'] == original['sha256'], 'Search receipt belongs to a different original')
    search = fireplay.payload(lookup['image_search'])
    hits = search['items']
    require(isinstance(hits, list) and len(hits) <= 50, 'Expected at most 50 official visual search results')
    selected = {(item['id'], item['matchedResourceId']): item for item in hits if isinstance(item, dict)
                and item.get('id') and item.get('matchedResourceId') and item.get('canView') is True}
    result = {'verified_source_material_ids': [], 'unverified_resource_ids': []}
    if 'resources' not in lookup:
        material_ids = list(dict.fromkeys(key[0] for key in selected))
        result['resource_lookups'] = [
            {'tool': 'material_resources_query', 'arguments': {'material_ids': material_ids[start:start + 20]}}
            for start in range(0, len(material_ids), 20)
        ]
        return result
    receipts = lookup['resources']
    require(isinstance(receipts, list) and len(receipts) <= 3, 'Expected the returned resource lookup batches')
    with original_path.open('rb') as stream:
        md5 = hashlib.file_digest(stream, 'md5').hexdigest()
    confirmed = set()
    checked = set()
    for receipt in receipts:
        for material in fireplay.payload(receipt)['items']:
            material_id = material.get('material_id')
            if material.get('query_status') != 'ready' or material.get('complete') is not True:
                continue
            for resource in material.get('images', []):
                key = material_id, resource.get('resource_id')
                hit = selected.get(key)
                if not hit or key in checked:
                    continue
                if (resource.get('status') != 'normal' or resource.get('url') != hit.get('matchedResourceUrl')
                        or not fireplay._http(resource.get('url'))
                        or (resource.get('width'), resource.get('height')) != (original['width'], original['height'])):
                    continue
                checked.add(key)
                if resource.get('md5') and resource['md5'].lower() != md5:
                    continue
                try:
                    digest = hashlib.sha256()
                    size = transfer_public_bytes(resource['url'], digest.update, max_bytes=min(MAX_BYTES, original['bytes']))
                except (ValueError, OSError):
                    result['unverified_resource_ids'].append(key[1])
                    continue
                if size == original['bytes'] and digest.hexdigest() == original['sha256']:
                    confirmed.add(material_id)
    ids = sorted(confirmed)
    result['verified_source_material_ids'] = ids
    result['unverified_resource_ids'].extend(sorted(key[1] for key in selected.keys() - checked))
    if ids:
        result['requirement_lookup'] = {'tool': 'requirement_list', 'arguments': {
            'scope': 'all', 'view': 'summary', 'asset_type': ['image', 'button'],
            'material_ids': ids, 'limit': 20, 'offset': 0, 'pagination': 'cursor',
        }}
    return result


def search_history(root: Path, code: str, index: int, lookup=None) -> dict:
    root = root.resolve()
    current = fireplay.load_record(code, root)
    sources = [row for row in current['sources'] if row['index'] == index]
    require(len(sources) == 1, 'Source index is missing or ambiguous')
    source = sources[0]
    path, original = _source_download(root, current, source)
    folder = fireplay.request_folder(code, root)
    version = current['content_version']
    require(isinstance(version, str) and re.fullmatch('[0-9a-f]{64}', version), 'Invalid content version')
    snapshot = read_json(folder / 'snapshots' / f'{version}.json')
    require(fireplay._hash(snapshot) == version, 'Registered source snapshot changed')
    content = snapshot['content']
    require(content['id'] == current['requirement_id'] and content['code'] == code
            and content['assetType'] in ('image', 'button'),
            'Historical reuse requires a registered image or button requirement')
    require(source in snapshot['parsed']['sources'], 'Current original is absent from the registered snapshot')
    candidates, unresolved = _history(root, original['sha256'], current, content['assetType'])
    if isinstance(source.get('targets'), list):
        targets = set(source['targets'])
    else:
        targets = set(current['targets'])
    candidates = [row for row in candidates if row['language'] in targets]
    result = {'code': code, 'source_index': index, 'source_sha256': original['sha256'],
              'local_candidates': candidates, 'historical_records_to_check': unresolved}
    search_arguments = {'scope': 'all', 'top_k': 20, 'limit': 5}
    source_url = current.get('source_links', {}).get(str(index), {}).get('url', source['url'])
    if fireplay._http(source_url) and not urlsplit(source_url).query:
        search_arguments['image_url'] = source_url
    else:
        result['reference_file'] = str(path)
    result['image_lookup'] = {'tool': 'material_search_by_image', 'arguments': search_arguments}
    if lookup is not None:
        result['fireplay'] = _official_candidates(path, original, lookup)
    return result
