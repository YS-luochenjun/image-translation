"""Find exact historical images and stage verified bytes for current review."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from time import perf_counter
from urllib.parse import urlsplit

from . import fireplay
from .artifact_copy import stage_artifact
from .contracts import require
from .delivery_autopilot import next_delivery
from .fireplay_download import MAX_BYTES, _inspect, transfer_public_bytes
from .material_sizes import plan_sizes
from .pipeline import read_json, sha256


IDENTITY = ('source_id', 'language', 'member_id')


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


def _verified_history(root, historical, confirmed, asset_type, *, original=None, archive=False):
    code = historical['code']
    version = historical['content_version']
    folder = root / 'work/fireplay-production' / code
    main = folder / 'delivery-state.json'
    require(main.resolve() == main, 'Historical delivery state must stay in its requirement directory')
    state_paths = [main, *(folder / name for name in read_json(main).get('supplemental_delivery_states', []))]
    hashes = {}
    for source in historical['sources']:
        recorded = historical.get('downloads', {}).get(str(source['index']), {})
        if original is None or recorded.get('sha256') == original:
            path, actual = _source_download(root, historical, source)
            hashes[path] = actual['sha256']
    require(bool(hashes), 'No verified original downloads')
    deliveries = []
    for state_path in state_paths:
        require(state_path.resolve() == state_path and state_path.parent == folder,
                'Historical delivery state must stay in its requirement directory')
        state = read_json(state_path)
        if state.get('content_version') != version or state['requirement']['asset_type'] != asset_type:
            require(not archive, 'Delivery state belongs to a different version or asset type')
            continue
        spec_path = (folder / state['size_spec']).resolve(strict=True)
        require(spec_path.is_relative_to(root), 'Historical size spec escapes the project')
        spec = read_json(spec_path)
        source_hashes = {}
        for row in spec['sources']:
            path = (spec_path.parent / row['path']).resolve()
            require(path.is_relative_to(root), 'Historical source escapes the project')
            digest = hashes.get(path)
            if digest is None and path.is_file():
                digest = sha256(path)
            if digest in hashes.values():
                source_hashes[row['id']] = digest
        if not source_hashes and not archive:
            continue
        report = next_delivery(state_path, include_verified=True)
        if (report['requirement_id'] != historical['requirement_id'] or report['code'] != code
                or not report['complete'] or report['publication_unknown']):
            require(not archive, 'Current delivery matrix cannot be verified')
            continue
        rows = [row for row in report['verified_deliveries']
                if row['source_id'] in source_hashes and row['material_id'] in confirmed['verified_material_ids']]
        if archive:
            require(len(rows) == report['verified_members'], 'Delivery members lack exact original or official receipt evidence')
        deliveries.extend((source_hashes[row['source_id']], row) for row in rows)
    if archive:
        require(len({row['resource_id'] for _, row in deliveries}) == len(deliveries)
                == confirmed['verified_members'] > 0, 'Archived matrix differs from the confirmed closeout')
        require({row['material_id'] for _, row in deliveries} == set(confirmed['verified_material_ids']),
                'Archived materials differ from the confirmed closeout')
    return deliveries


def _confirmed_identity(confirmed, historical):
    return (confirmed.get('state') == 'confirmed'
            and all(confirmed.get(key) == historical[key] for key in ('code', 'requirement_id', 'content_version')))


def _history(root, original, current, asset_type):
    from .storage_retention import archive_on_hold, archive_path, archive_retired, read_archive

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
            if not _confirmed_identity(confirmed, historical):
                continue
            if archive_retired(request_path.parent, historical) or archive_on_hold(request_path.parent):
                continue
            archived = archive_path(request_path.parent, historical)
            if archived.exists():
                data = read_archive(archived, historical, confirmed)
                rows = [(item['source_sha256'], item['delivery']) for item in data['members']
                        if data['asset_type'] == asset_type]
            else:
                rows = _verified_history(root, historical, confirmed, asset_type, original=original)
            candidates.extend({**row, 'historical_code': code, 'historical_requirement_id': historical['requirement_id']}
                              for digest, row in rows if digest == original)
        except FileNotFoundError:
            if matches:
                unresolved.append(code)
        except (ValueError, OSError, TypeError, KeyError, EOFError):
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


def _registered_content(root, current):
    folder = fireplay.request_folder(current['code'], root)
    version = current['content_version']
    require(isinstance(version, str) and re.fullmatch('[0-9a-f]{64}', version), 'Invalid content version')
    snapshot = read_json(folder / 'snapshots' / f'{version}.json')
    require(fireplay._hash(snapshot) == version, 'Registered source snapshot changed')
    content = snapshot['content']
    require(content['id'] == current['requirement_id'] and content['code'] == current['code']
            and content['assetType'] in ('image', 'button'),
            'Historical reuse requires a registered image or button requirement')
    require(current['sources'] == snapshot['parsed']['sources'], 'Current originals disagree with the registered snapshot')
    return content


def _visual_query(current, source, path):
    arguments = {'scope': 'all', 'top_k': 20, 'limit': 5}
    result = {'image_lookup': {'tool': 'material_search_by_image', 'arguments': arguments}}
    source_url = current.get('source_links', {}).get(str(source['index']), {}).get('url', source['url'])
    if fireplay._http(source_url) and not urlsplit(source_url).query:
        arguments['image_url'] = source_url
    else:
        result['reference_file'] = str(path)
    return result


def search_history(root: Path, code: str, index: int, lookup=None) -> dict:
    root = root.resolve()
    current = fireplay.load_record(code, root)
    content = _registered_content(root, current)
    sources = [row for row in current['sources'] if row['index'] == index]
    require(len(sources) == 1, 'Source index is missing or ambiguous')
    source = sources[0]
    path, original = _source_download(root, current, source)
    candidates, unresolved = _history(root, original['sha256'], current, content['assetType'])
    if isinstance(source.get('targets'), list):
        targets = set(source['targets'])
    else:
        targets = set(current['targets'])
    candidates = [row for row in candidates if row['language'] in targets]
    result = {'code': code, 'source_index': index, 'source_sha256': original['sha256'],
              'local_candidates': candidates, 'historical_records_to_check': unresolved,
              **_visual_query(current, source, path)}
    if lookup is not None:
        result['fireplay'] = _official_candidates(path, original, lookup)
    return result


def reuse_plan(root: Path, code: str, state_path: Path, *, rework=False, lookup=None, remote_image=None) -> dict:
    started = perf_counter()
    root = root.resolve()
    folder = root / 'work/fireplay-production' / code
    current = fireplay.load_record(code, root)
    content = _registered_content(root, current)
    state_path = state_path.resolve(strict=True)
    require(folder.resolve() == folder and state_path.parent == folder, 'Delivery state must belong to this demand')
    state = read_json(state_path)
    require(state['requirement']['id'] == current['requirement_id'] and state['requirement']['code'] == code
            and state['content_version'] == current['content_version']
            and state['requirement']['asset_type'] == content['assetType'], 'Delivery state identity or version changed')
    spec_path = (folder / state['size_spec']).resolve(strict=True)
    require(spec_path.is_relative_to(folder), 'Delivery specification must belong to this demand')
    plan = plan_sizes(spec_path)
    require(plan['status'] == 'planned' and plan['asset_type'] == content['assetType'], 'Delivery spec type changed')
    result = {'code': code, 'content_version': current['content_version'],
              'delivery_state_sha256': sha256(state_path), 'size_spec_sha256': sha256(spec_path),
              'matches': [], 'unmatched': [], 'historical_records_to_check': []}
    currently_rejected = any('reject' in str(status).casefold()
                             for status in (current.get('platform_status'), content.get('status')))
    if rework or currently_rejected:
        return {**result, 'mode': 'rework', 'unmatched': [{key: row[key] for key in IDENTITY} for row in plan['items']]}

    sources, originals, histories = {}, {}, {}
    for source in current['sources']:
        download = current.get('downloads', {}).get(str(source['index']), {})
        if download.get('content_version') != current['content_version']:
            continue
        path, original = _source_download(root, current, source)
        sources[path] = original['sha256']
        originals.setdefault(original['sha256'], (source, path, original))
    source_hashes = {}
    for source in read_json(spec_path)['sources']:
        path = (spec_path.parent / source['path']).resolve(strict=True)
        require(path.is_relative_to(root), 'Planned original must stay inside the workspace')
        source_hashes[source['id']] = sources[path] if path in sources else sha256(path)
        require(source_hashes[source['id']] in originals, 'Planned original is not a registered original by exact SHA256')

    for item in plan['items']:
        digest = source_hashes[item['source_id']]
        if digest not in histories:
            histories[digest] = _history(root, digest, current, content['assetType'])
        historical, unresolved = histories[digest]
        result['historical_records_to_check'].extend(unresolved)
        keys = ('language', 'member_id', 'width', 'height', 'delivery_mode')
        matches = [row for row in historical if all(row.get(key) == item.get(key) for key in keys)
                   and re.fullmatch('[a-f0-9]{64}', row.get('output_sha256') or '')]
        identity = {**{key: item[key] for key in (*IDENTITY, 'width', 'height', 'max_bytes')}, 'source_sha256': digest}
        if matches and item.get('official_size_check', {}).get('status') != 'conflict':
            result['matches'].append({**identity, 'candidates': matches})
        else:
            result['unmatched'].append(identity)
    result['historical_records_to_check'] = sorted(set(result['historical_records_to_check']))
    digest = result['unmatched'][0]['source_sha256'] if result['unmatched'] else None
    if remote_image is not None:
        selected = [sources[path] for source in current['sources'] if source['index'] == remote_image
                    and (download := current['downloads'].get(str(remote_image)))
                    and (path := (root / download['local_path']).resolve()) in sources]
        require(len(selected) == 1 and selected[0] in source_hashes.values(),
                'Remote source must be a downloaded original in the frozen matrix')
        digest = selected[0]
    if digest:
        source, path, original = originals[digest]
        result['remote'] = {'source_sha256': digest, 'source_index': source['index'],
                            **_visual_query(current, source, path)}
        if lookup is not None:
            require(lookup.get('source_sha256') == digest, 'Official lookup must use the planned unmatched original')
            result['remote'].update(_official_candidates(path, original, lookup))
            if 'requirements' in lookup:
                result['remote'].update(_requirement_candidates(
                    result['remote']['verified_source_material_ids'], code, lookup['requirements']))
    else:
        require(lookup is None, 'Select a current original before supplying a Fireplay lookup')
    candidate_ids = sorted({row['historical_requirement_id']
                            for item in result['matches'] for row in item['candidates']})
    result['historical_delivery_reads'] = [
        {'tool': 'requirement_list', 'arguments': {'scope': 'all', 'view': 'delivery',
         'requirement_ids': candidate_ids[start:start+20], 'limit': 20}}
        for start in range(0, len(candidate_ids), 20)]
    return {**result, 'mode': 'normal', 'unique_originals_checked': len(histories),
            'local_seconds': round(perf_counter() - started, 4) if lookup is None else None}


def _requirement_candidates(source_ids, current_code, receipt):
    response = fireplay.payload(receipt)
    metadata = response.get('query_meta', {})
    require(source_ids and response.get('view') == 'summary'
            and sorted(metadata.get('filters_applied', {}).get('material_ids', [])) == sorted(source_ids),
            'Panel response is not scoped to the verified original material IDs')
    identifiers = list(dict.fromkeys(row['id'] for row in response['items']
                                     if row['code'] != current_code and row.get('status') == 'done'
                                     and 'source' in row.get('matched_relations', [])))[:5]
    return {'historical_source_requirement_ids': identifiers,
            'panel_lookup_partial': bool(response.get('next_cursor') or response.get('next_offset')
                                         or metadata.get('source_limit_reached') or metadata.get('total_exact') is False),
            'delivery_lookup': {'tool': 'requirement_list', 'arguments': {
                'scope': 'all', 'view': 'delivery', 'requirement_ids': identifiers, 'limit': 5,
            }} if identifiers else None}


def _historic_output(root, candidate, item):
    folder = root / 'work/fireplay-production' / candidate['historical_code']
    main = folder / 'delivery-state.json'
    try:
        state = read_json(main)
        paths = [main, *(folder / value for value in state.get('supplemental_delivery_states', []))]
        for path in paths:
            if path.resolve() != path or path.parent != folder:
                continue
            for member in read_json(path).get('members', []):
                if (any(member.get(key) != candidate[key] for key in IDENTITY) or not member.get('output_path')):
                    continue
                artifact = (folder / member['output_path']).resolve()
                if not artifact.is_relative_to(folder) or not artifact.is_file():
                    continue
                actual = _inspect(artifact)
                if (actual['sha256'] == candidate['output_sha256']
                        and (actual['width'], actual['height']) == (item['width'], item['height'])
                        and actual['bytes'] <= item['max_bytes'] and actual['format'] in ('PNG', 'JPEG')):
                    return artifact, 'png' if actual['format'] == 'PNG' else 'jpg'
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None, None


def stage_reuse(root, code, state_path, source_id, language, member_id, material_id, evidence):
    root = root.resolve()
    state_path = state_path.resolve(strict=True)
    fireplay.request_folder(code, root)
    folder = root / 'work/fireplay-production' / code
    require(folder.resolve() == folder and state_path.parent == folder, 'Delivery state must belong to this demand')
    with fireplay._lock(folder, 'publication.lock'):
        plan = reuse_plan(root, code, state_path)
        target = source_id, language, member_id
        matches = [item for item in plan['matches'] if tuple(item[key] for key in IDENTITY) == target]
        require(len(matches) == 1, 'No exact historical match for this current member')
        item = matches[0]
        candidates = [row for row in item['candidates'] if row['material_id'] == material_id]
        require(len(candidates) == 1, 'Choose a single exact historical material and resource')
        candidate = candidates[0]
        panel = fireplay.payload(evidence['historical_delivery'])
        observed = datetime.fromisoformat(panel['serverTime'])
        require(observed.tzinfo is not None
                and -timedelta(minutes=1) <= datetime.now(timezone.utc) - observed <= timedelta(minutes=15)
                and panel.get('view') == 'delivery', 'Read the current historical delivery from Fireplay')
        historical = [row for row in panel['items']
                      if row['id'] == candidate['historical_requirement_id'] and row['code'] == candidate['historical_code']]
        require(len(historical) == 1 and historical[0].get('status') == 'done'
                and historical[0].get('materialSelection', {}).get('status') != 'waiting_selection'
                and any(row['id'] == material_id for row in historical[0].get('materials', [])),
                'Historical requirement status or visible binding does not confirm this material')
        material = fireplay.payload(evidence['material_detail'])['material']
        require(material['id'] == material_id and material.get('canUse') is True
                and material.get('status') == 'available' and material.get('type') == read_json(state_path)['requirement']['asset_type'],
                'Historical material is unavailable or cannot be used')
        resources = [row for row in material['images'] if row['id'] == candidate['resource_id']]
        require(len(resources) == 1, 'Historical material resource changed')
        resource = resources[0]
        extensions = {'image/png': 'png', 'image/jpeg': 'jpg'}
        mime = resource.get('mimeType')
        require(resource.get('status') == 'normal' and mime in extensions and fireplay._http(resource.get('url'))
                and (resource.get('width'), resource.get('height')) == (item['width'], item['height'])
                and type(resource.get('fileSize')) is int and 0 < resource['fileSize'] <= item['max_bytes'],
                'Historical resource fails current file constraints')
        require(all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) for value in target),
                'Unsafe member path identifier')
        previous, extension = _historic_output(root, candidate, item)
        stem = folder / 'outputs' / source_id / language / member_id
        destination = stem.with_name(f'{member_id}.{extension or extensions[mime]}')
        require(destination.resolve() == destination, 'Canonical member path uses a link')
        digest = candidate['output_sha256']
        existing = [stem.with_name(f'{member_id}.{suffix}') for suffix in ('png', 'jpg')
                    if stem.with_name(f'{member_id}.{suffix}').exists()]
        if existing:
            require(len(existing) == 1 and existing[0].resolve() == existing[0] and existing[0].is_file()
                    and sha256(existing[0]) == digest, 'Canonical member path already contains a different version')
            return {'path': str(existing[0]), 'output_path': str(existing[0].relative_to(folder)), 'sha256': digest,
                    'mode': 'reused', 'candidate': candidate}
        delivery = next_delivery(state_path)
        current_action = next(row['action'] for row in delivery['actions'] if tuple(row[key] for key in IDENTITY) == target)
        require(current_action == 'make' and not delivery['publication_unknown'], 'Current member has entered review or publishing')
        if previous is not None:
            staged = stage_artifact(previous, destination, expected_sha256=digest)
        else:
            with TemporaryDirectory(prefix='.reuse-stage-', dir=folder) as staging:
                temporary = Path(staging) / f'resource.{extensions[mime]}'
                with temporary.open('xb') as stream:
                    transfer_public_bytes(resource['url'], stream.write, max_bytes=item['max_bytes'])
                incoming = _inspect(temporary)
                require((incoming['width'], incoming['height']) == (item['width'], item['height'])
                        and incoming['format'] == ('PNG' if mime == 'image/png' else 'JPEG'),
                        'Historical resource does not match the reviewed format and dimensions')
                staged = stage_artifact(temporary, destination, expected_sha256=digest)
        return {**staged, 'output_path': str(destination.relative_to(folder)), 'candidate': candidate,
                'next': 'Record output_path and review for the current matrix member'}
