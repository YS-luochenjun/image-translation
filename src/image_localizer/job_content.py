"""Bind declared translations to lettering and merge semantic pixel protections.

Bitmap text is a supplied declaration requiring actual visual transcription review;
this module neither reads glyphs from images nor certifies those declarations.
"""
from __future__ import annotations

import unicodedata

from .contracts import box, require

_REMOVABLE_PUNCTUATION = frozenset('，。、；')


def _identity(value: object, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f'{label}: nonempty ID required')
    return value


def _text(value: object, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f'{label}: nonempty text required')
    return value


def _normalized_text(value: str) -> str:
    # NFC deliberately preserves full-width digits and all ASCII punctuation.
    return ''.join(character for character in unicodedata.normalize('NFC', value)
                   if not character.isspace() and character not in _REMOVABLE_PUNCTUATION)


def _object_list(value: object, label: str) -> list[dict]:
    require(isinstance(value, list) and all(isinstance(item, dict) for item in value),
            f'{label}: expected a list of objects')
    return value


def _content(target: dict) -> tuple[dict[str, str], set[str]]:
    rows = _object_list(target.get('rows'), 'rows')
    assets = _object_list(target.get('assets'), 'assets')
    all_ids: set[str] = set()
    row_texts: dict[str, str] = {}
    text_assets: set[str] = set()
    for row in rows:
        identity = _identity(row.get('id'), 'row')
        require(identity not in all_ids, f'Duplicate row/asset ID: {identity}')
        all_ids.add(identity)
        row_texts[identity] = _text(row.get('text'), f'Row {identity}')
    for asset in assets:
        identity = _identity(asset.get('id'), 'asset')
        require(identity not in all_ids, f'Duplicate row/asset ID: {identity}')
        all_ids.add(identity)
        require(asset.get('kind') in ('text', 'compound', 'decoration'), f'Asset {identity}: invalid kind')
        if asset['kind'] in ('text', 'compound'):
            text_assets.add(identity)
    return row_texts, text_assets


def validate_region_map(target: dict, manifest: dict) -> None:
    """Require exact per-region declared text and one reference per lettering item.

    region_map entries contain region_id, ordered row_ids, and ordered asset_texts
    [{asset_id, text}]. Row text followed by declared asset text must equal the
    manifest target_text after NFC, whitespace and only ，。、； removal.
    Every row and text/compound asset must be referenced exactly once. Decoration
    assets are never accepted as translation content. Asset declarations still
    require a human/Agent to actually inspect and read the supplied bitmap.
    """
    require(isinstance(target, dict) and isinstance(manifest, dict), 'Target and manifest must be objects')
    rows, assets = _content(target)
    regions = _object_list(manifest.get('regions'), 'manifest regions')
    translations = {}
    region_ids = set()
    for region in regions:
        identity = _identity(region.get('id'), 'semantic region')
        require(identity not in region_ids, f'Duplicate semantic region ID: {identity}')
        region_ids.add(identity)
        require(region.get('action') in ('translate', 'preserve'), f'Region {identity}: invalid action')
        if region['action'] == 'translate':
            translations[identity] = _text(region.get('target_text'), f'Region {identity} target_text')
    mappings = _object_list(target.get('region_map'), 'region_map')
    mapped_regions, used_rows, used_assets = set(), set(), set()
    for mapping in mappings:
        require(set(mapping) == {'region_id', 'row_ids', 'asset_texts'}, 'Invalid region_map fields')
        region_id = _identity(mapping['region_id'], 'mapped region')
        require(region_id in translations, f'Mapped region is not a translate region: {region_id}')
        require(region_id not in mapped_regions, f'Duplicate region mapping: {region_id}')
        mapped_regions.add(region_id)
        row_ids = mapping['row_ids']
        require(isinstance(row_ids, list), f'{region_id}: row_ids must be a list')
        declarations = _object_list(mapping['asset_texts'], f'{region_id} asset_texts')
        require(bool(row_ids or declarations), f'{region_id}: translation requires mapped lettering')
        pieces = []
        for value in row_ids:
            identity = _identity(value, 'mapped row')
            require(identity in rows, f'Unknown mapped row ID: {identity}')
            require(identity not in used_rows, f'Row referenced more than once: {identity}')
            used_rows.add(identity)
            pieces.append(rows[identity])
        for declaration in declarations:
            require(set(declaration) == {'asset_id', 'text'}, 'Invalid asset_texts fields')
            identity = _identity(declaration['asset_id'], 'mapped asset')
            require(identity in assets, f'Unknown or non-text mapped asset ID: {identity}')
            require(identity not in used_assets, f'Asset referenced more than once: {identity}')
            used_assets.add(identity)
            pieces.append(_text(declaration['text'], f'Asset {identity} declared text'))
        require(_normalized_text(''.join(pieces)) == _normalized_text(translations[region_id]),
                f'Region {region_id}: mapped lettering does not match semantic target_text')
    require(mapped_regions == set(translations), 'region_map must cover every translate region exactly once')
    require(used_rows == set(rows), 'Every actual row must be referenced exactly once')
    require(used_assets == assets, 'Every text/compound asset must be referenced exactly once')


def merged_protections(project_protected: list[dict], manifests: list[tuple[str, dict]],
                       size: tuple[int, int]) -> list[dict]:
    """Return a new bbox-deduplicated protection union with deterministic unique IDs.

    Order is project protections, then each language's explicit protections and
    preserve regions. The first bbox/ID is retained; ID collisions use -2, -3, ...
    suffixes. Input objects and their bbox lists are never modified or aliased.
    """
    require(isinstance(size, (tuple, list)) and len(size) == 2 and
            all(type(value) is int and value > 0 for value in size), 'Invalid protection canvas size')
    require(isinstance(manifests, list), 'manifests must be a list of (language, manifest) pairs')
    result, seen_boxes, used_ids = [], set(), set()

    def append(region: dict, label: str) -> None:
        require(isinstance(region, dict), f'{label}: protection must be an object')
        identity = _identity(region.get('id'), label)
        bbox = list(box(region.get('bbox'), *size, label))
        key = tuple(bbox)
        if key in seen_boxes:
            return
        unique_id, suffix = identity, 2
        while unique_id in used_ids:
            unique_id = f'{identity}-{suffix}'
            suffix += 1
        seen_boxes.add(key)
        used_ids.add(unique_id)
        result.append({'id': unique_id, 'bbox': bbox})

    for region in _object_list(project_protected, 'project protected_regions'):
        append(region, 'Project protection')
    for item in manifests:
        require(isinstance(item, (tuple, list)) and len(item) == 2,
                'Expected (language, manifest) pair')
        language, manifest = item
        _identity(language, 'language')
        require(isinstance(manifest, dict), f'{language}: manifest must be an object')
        for region in _object_list(manifest.get('protected_regions', []), f'{language} protected_regions'):
            append(region, f'{language} protection')
        for region in _object_list(manifest.get('regions'), f'{language} regions'):
            require(region.get('action') in ('translate', 'preserve'), f'{language}: invalid region action')
            if region['action'] == 'preserve':
                append(region, f'{language} preserve')
    return result
