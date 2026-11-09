"""Receipt-driven image intake. No connector calls, credentials or platform writes."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .languages import LANGUAGE_ALIASES, LANGUAGE_ALIAS_GROUPS

QUERY = {'scope': 'all', 'view': 'summary', 'asset_type': 'image',
         'pagination': 'cursor', 'limit': 200, 'offset': 0}
ALIASES = {'english': 'en', '英语': 'en', '英文': 'en',
           '日语': 'ja', '日文': 'ja',
           '中文': 'zh-CN', '简体中文': 'zh-CN', 'zh-cn': 'zh-CN',
           'traditional chinese': 'zh-TW', '繁体中文': 'zh-TW',
           '韩语': 'ko', '德语': 'de', '法语': 'fr', '西班牙语': 'es',
           '葡萄牙语': 'pt', '印尼语': 'id', '印地语': 'hi', '越南语': 'vi',
           '阿拉伯语': 'ar', '俄语': 'ru', **LANGUAGE_ALIASES}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def payload(value):
    """Unwrap observed MCP JSON envelopes; error responses fail closed."""
    for _ in range(8):
        _require(isinstance(value, dict) and not value.get('isError'), 'Expected successful JSON receipt')
        if 'structuredContent' in value:
            value = value['structuredContent']
        elif 'data' in value and isinstance(value['data'], dict):
            value = value['data']
        elif 'content' in value and isinstance(value['content'], list):
            texts = [item['text'] for item in value['content']
                     if isinstance(item, dict) and item.get('type') == 'text']
            _require(len(texts) == 1, 'Expected one JSON text receipt')
            value = json.loads(texts[0])
        else:
            return value
    raise ValueError('Receipt envelope too deep')


def _identity(row):
    _require(isinstance(row, dict), 'Expected requirement object')
    try:
        valid = str(UUID(row.get('id'))) == row['id'].lower()
    except (ValueError, AttributeError, TypeError):
        valid = False
    _require(valid and isinstance(row.get('code'), str), 'Requirement needs UUID and code')


def _next_cursor(response):
    """Read either explicit has_more or observed live_keyset cursor evidence."""
    own_cursor = 'next_cursor' in response
    cursor = response.get('next_cursor')
    valid_cursor = isinstance(cursor, str) and bool(cursor.strip())
    if 'has_more' in response:
        _require(type(response['has_more']) is bool, 'Summary receipt needs explicit pagination evidence')
        if response['has_more']:
            _require(own_cursor and valid_cursor, 'Incomplete pagination needs a fresh next_cursor')
            return cursor
        _require(not own_cursor or cursor is None, 'Conflicting terminal pagination cursor')
        return None
    meta = response.get('query_meta')
    _require(own_cursor and isinstance(meta, dict) and meta.get('pagination') == 'live_keyset'
             and (cursor is None or valid_cursor), 'Summary receipt needs explicit live_keyset next_cursor')
    return cursor


def discover(spec: dict | None = None) -> dict:
    """Validate actual summary pages and return P0-first brief-reading actions."""
    _require(spec is None or isinstance(spec, dict), 'Discovery receipt must be an object')
    spec = spec or {}
    _require(isinstance(spec.get('query', {}), dict), 'Query must be an object')
    query = {**QUERY, **spec.get('query', {})}
    required_modes = spec.get('required_ai_modes')
    if required_modes is not None:
        _require(isinstance(required_modes, list) and bool(required_modes)
                 and all(isinstance(mode, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,47}', mode)
                         for mode in required_modes)
                 and len(set(required_modes)) == len(required_modes),
                 'required_ai_modes must contain unique exact configured mode IDs')
        requested_modes = query.get('ai_mode')
        if requested_modes is not None:
            _require((requested_modes if isinstance(requested_modes, list) else [requested_modes]) == required_modes,
                     'Query ai_mode conflicts with required_ai_modes')
        query['ai_mode'] = required_modes[0] if len(required_modes) == 1 else required_modes
    asset_types = query['asset_type'] if isinstance(query['asset_type'], list) else [query['asset_type']]
    _require(bool(asset_types) and all(kind in ('image', 'button') for kind in asset_types)
             and len(set(asset_types)) == len(asset_types) and query['view'] == 'summary'
             and query['pagination'] == 'cursor' and query['offset'] == 0
             and not query.get('cursor'), 'Use image/button summary cursor query starting at the first page')
    result = {'schema_status': 'reference_only', 'contract_instruction':
              'Discover the current requirement_list contract before host execution.',
              'external_write': False, 'platform_claimed': False, 'complete': False,
              'state': 'needs_summary', 'host_actions': [{'tool': 'requirement_list', 'arguments': query}]}
    if 'summary_pages' not in spec:
        return result
    pages = spec['summary_pages']
    _require(isinstance(pages, list) and pages, 'Need actual summary page receipts, including empty scans')
    rows, ids, codes, cursors = [], set(), set(), set()
    cursor = None
    for position, page in enumerate(pages):
        _require(isinstance(page, dict) and isinstance(page.get('arguments'), dict), 'Page needs actual arguments')
        expected = {**query, **({'cursor': cursor} if cursor else {})}
        _require(page['arguments'] == expected and (position == 0 or cursor),
                 'Keep identical filters and follow the returned cursor chain')
        response = payload(page.get('response'))
        _require(isinstance(response.get('items'), list), 'Summary receipt needs items')
        next_cursor = _next_cursor(response)
        for row in response['items']:
            _identity(row)
            identity = row['id'].lower()
            _require(identity not in ids and row['code'] not in codes, 'Duplicate or conflicting requirement id/code')
            ids.add(identity)
            codes.add(row['code'])
            if row.get('assetType') in asset_types:
                mode = row.get('aiMode')
                classification = 'needs_brief'
                if required_modes is not None:
                    classification = ('selected' if mode in required_modes else
                                      'needs_classification' if not mode else 'excluded_ai_mode')
                rows.append({**row, 'translation_hint': bool(re.search(
                    r'翻译|译成|translate|translation|localiz', str(row.get('title', '')), re.I)),
                    'classification': classification})
        cursor = next_cursor
        _require(cursor is None or cursor not in cursors, 'Incomplete pagination needs a fresh next_cursor')
        cursors.add(cursor)
    rows.sort(key=lambda row: (row.get('priority') != 'P0', str(row.get('priority', 'P9')), row['code']))
    actions = [{'tool': 'requirement_list', 'arguments': {**query, 'cursor': cursor}}] if cursor else []
    actions.extend({'tool': 'requirement_list', 'arguments': {'scope': query['scope'], 'view': 'blind_brief', 'requirement_ids': [row['id']]},
                    'code': row['code'], 'reason': 'Read brief before translation classification'} for row in rows
                   if row['classification'] != 'excluded_ai_mode')
    return {**result, 'complete': cursor is None, 'state': 'partial' if cursor else ('found' if rows else 'empty'),
            'requirements': rows, 'host_actions': actions, 'next_cursor': cursor,
            'selected': [row for row in rows if row['classification'] == 'selected'],
            'needs_classification': [row for row in rows if row['classification'] == 'needs_classification'],
            'selection_note': 'Exact AI mode selection only; inspect brief and sources before local acceptance.'}


def request_folder(code: str, root: Path) -> Path:
    _require(isinstance(code, str) and re.fullmatch(r'[A-Z][A-Z0-9]{1,15}-\d{8}-\d{3,6}', code),
             'Expected exact Fireplay requirement code')
    folder = Path(root).absolute() / 'state' / 'fireplay' / code
    for part in (folder, *folder.parents):
        _require(not part.is_symlink() and not (part.exists() and getattr(part.stat(), 'st_file_attributes', 0) & 0x400),
                 'Linked intake folders are unsupported')
    return folder


@contextmanager
def _lock(folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError('Requirement is locked; inspect the active operation before retrying') from error
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink()


def _write(path, value, *, immutable=False):
    if immutable and path.exists():
        _require(json.loads(path.read_text(encoding='utf-8')) == value, 'Immutable snapshot conflict')
        return
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save(folder, record):
    versions = folder / 'versions'
    versions.mkdir(exist_ok=True)
    _write(versions / (record['content_version'] + '.json'), record)
    _write(folder / 'request.json', record)


def load_record(code: str, root: Path) -> dict:
    return json.loads((request_folder(code, root) / 'request.json').read_text(encoding='utf-8'))


def _exact(response, code):
    rows = payload(response).get('items')
    _require(isinstance(rows, list), 'Receipt needs items')
    seen_ids, seen_codes = set(), set()
    for row in rows:
        _identity(row)
        _require(row['id'] not in seen_ids and row['code'] not in seen_codes, 'Duplicate or conflicting requirement id/code')
        seen_ids.add(row['id'])
        seen_codes.add(row['code'])
    matches = [row for row in rows if row['code'] == code]
    _require(len(matches) == 1, 'Need exactly one exact summary/brief code match')
    return matches[0]


def _http(url):
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password


def _locale(value):
    if not isinstance(value, str):
        return None
    text = value.strip().replace('_', '-')
    alias = ALIASES.get(text.lower())
    if alias:
        return alias
    parts = text.split('-')
    if parts[0].lower() not in set(LANGUAGE_ALIAS_GROUPS) | {'uk'}:
        return None
    if not re.fullmatch(r'[a-zA-Z]{2,3}(?:-[a-zA-Z]{2}|-[a-zA-Z]{4}|-\d{3}){0,2}', text):
        return None
    return '-'.join([parts[0].lower(), *[part.title() if len(part) == 4 else part.upper() for part in parts[1:]]])


def _content(brief, decision, material_receipt):
    text = '\n'.join(str(brief.get(key) or '') for key in ('description', 'refText'))
    evidence = text + '\n' + json.dumps(brief, ensure_ascii=False)
    if material_receipt is not None:
        evidence += json.dumps(payload(material_receipt), ensure_ascii=False)
    blockers = []
    quote = decision.get('source_quote')
    justified = isinstance(quote, str) and bool(quote.strip()) and quote in evidence
    intent = bool(re.search(r'翻译|译成|translate|translation|localiz', text, re.I)) if text else None
    if re.search(r'(?:不(?:要|需|用)?|无需|禁止)\s*翻译|(?:do not|don.t|no|without)\s+(?:translate|translation)', text, re.I):
        intent = False
    if 'translation_intent' in decision:
        _require(type(decision['translation_intent']) is bool and justified
                 and decision.get('explanation'), 'Intent decision needs brief evidence and host explanation')
        intent = decision['translation_intent']
    if intent is not True:
        blockers.append('translation_not_confirmed')
    sources = []
    if 'sources' in decision:
        _require(isinstance(decision['sources'], list), 'Decision sources must be a list')
        for source in decision['sources']:
            _require(isinstance(source, dict) and _http(source.get('url'))
                     and not re.search(r'\.(mp4|mov|webm|mkv)(?:$|[?#])', source['url'], re.I),
                     'Source needs an HTTP image URL')
            source_quote = source.get('source_quote') or quote
            _require(isinstance(source_quote, str) and source_quote and source_quote in evidence
                     and source['url'] in evidence, 'Source needs actual brief/material receipt evidence')
            sources.append({**source, 'index': len(sources) + 1})
    else:
        for url in re.findall(r'https?://[^\s<>"\)\]]+', text):
            url = url.rstrip('.,;，。；')
            if _http(url) and re.search(r'\.(png|jpe?g|webp|gif|bmp|tiff?)(?:$|[?#])', url, re.I):
                sources.append({'index': len(sources) + 1, 'url': url, 'source_quote': url})
    unique = []
    for source in sources:
        if source['url'] not in {item['url'] for item in unique}:
            unique.append({**source, 'index': len(unique) + 1})
    sources = unique
    if not sources:
        blockers.append('image_sources_unresolved')
    raw_targets = brief.get('locales') or []
    if 'targets' in decision:
        _require(justified and decision.get('explanation'), 'Target decision needs source_quote and host explanation')
        raw_targets = decision['targets']
    elif not raw_targets:
        match = re.search(r'(?:目标语言|目标语种|target(?: languages?| locales?)?)\s*[:：]\s*([^\n.;。]+)', text, re.I)
        raw_targets = re.split(r'[,，、/;；]', match.group(1)) if match else []
    _require(isinstance(raw_targets, list), 'Targets/locales must be a list')
    targets, unresolved = [], []
    for value in raw_targets:
        locale = _locale(value)
        if locale and locale not in targets:
            targets.append(locale)
        elif not locale:
            unresolved.append(value)
    if not targets or unresolved:
        blockers.append('target_locales_unresolved')
    return {'translation_intent': intent, 'sources': sources, 'targets': targets,
            'raw_targets': raw_targets, 'unresolved_targets': unresolved, 'blockers': blockers}


def register(code: str, spec: dict, root: Path) -> dict:
    """Save exact summary/brief receipts and immutable content snapshots."""
    folder = request_folder(code, root)
    spec = payload(spec)
    if 'summary_pages' in spec:
        scan = discover({'query': spec.get('query', {}), 'summary_pages': spec['summary_pages']})
        summary = _exact({'items': scan['requirements']}, code)
    else:
        summary = _exact(spec.get('summary'), code)
    brief = _exact(spec.get('brief'), code)
    _require(summary['id'] == brief['id'], 'Requirement UUID mismatch')
    _require(summary.get('updatedAt') and summary['updatedAt'] == brief.get('updatedAt'),
             'Requirement changed between summary and brief; refresh both')
    _require(summary.get('assetType') == brief.get('assetType')
             and brief.get('assetType') in ('image', 'button'), 'Only image/button requirements are supported')
    decision = spec.get('decision', {})
    _require(isinstance(decision, dict), 'Decision must be an object')
    parsed = _content(brief, decision, spec.get('material_receipt'))
    platform_status = summary.get('status')
    if not platform_status:
        parsed['blockers'].append('platform_status_unresolved')
    if str(platform_status).lower() in ('done', 'completed', 'cancelled', 'canceled'):
        parsed['blockers'].append('platform_requirement_closed')
    content = {key: brief.get(key) for key in ('id', 'code', 'title', 'description', 'refText', 'assetType', 'quantity', 'locales')}
    frozen = {'content': content, 'parsed': {key: parsed[key] for key in parsed if key != 'blockers'}}
    version = _hash(frozen)
    with _lock(folder):
        previous = load_record(code, root) if (folder / 'request.json').exists() else None
        _require(previous is None or previous['requirement_id'] == brief['id'],
                 'Registered requirement UUID changed; do not replace another identity')
        same = previous is not None and previous['content_version'] == version
        record = {'schema_version': 1, 'code': code, 'requirement_id': brief['id'],
                  'content_version': version, 'platform_status': platform_status, 'platform_claimed': False,
                  'updatedAt': summary['updatedAt'], 'priority': summary.get('priority'), **parsed,
                  'state': 'blocked' if parsed['blockers'] else 'ready', 'downloads': {},
                  'accepted_versions': previous.get('accepted_versions', []) if previous else []}
        saved_version = folder / 'versions' / (version + '.json')
        if same:
            record['downloads'] = previous.get('downloads', {})
        elif saved_version.exists():
            record['downloads'] = json.loads(saved_version.read_text(encoding='utf-8')).get('downloads', {})
        if not record['blockers']:
            if version in record['accepted_versions']:
                record['state'] = 'accepted'
            elif record['accepted_versions']:
                record['state'] = 'revision_pending'
        snapshots = folder / 'snapshots'
        snapshots.mkdir(exist_ok=True)
        _write(snapshots / (version + '.json'), frozen, immutable=True)
        _write(snapshots / ('receipt-' + _hash(spec) + '.json'), spec, immutable=True)
        _save(folder, record)
    return record


def status(code: str, root: Path) -> dict:
    return load_record(code, root)


def accept(code: str, root: Path) -> dict:
    """Accept the current resolved local version without platform writes."""
    folder = request_folder(code, root)
    with _lock(folder):
        record = load_record(code, root)
        _require(not record['blockers'] and record['translation_intent'] is True
                 and record['sources'] and record['targets'], 'Requirement has unresolved intake blockers')
        if record['state'] == 'accepted':
            return record
        record['state'] = 'accepted'
        if record['content_version'] not in record['accepted_versions']:
            record['accepted_versions'].append(record['content_version'])
        _write(folder / 'snapshots' / ('accepted-' + record['content_version'] + '.json'), record, immutable=True)
        _save(folder, record)
        return record


def record_download(code: str, index: int, entry: dict, root: Path) -> dict:
    folder = request_folder(code, root)
    with _lock(folder):
        record = load_record(code, root)
        _require(record['state'] == 'accepted' and not record['blockers'], 'Accept the current local version before download')
        _require(any(source['index'] == index for source in record['sources']), 'Unknown source index')
        _require(isinstance(entry, dict) and entry.get('content_version') == record['content_version'],
                 'Download content version mismatch')
        record['downloads'][str(index)] = entry
        _save(folder, record)
        return record
