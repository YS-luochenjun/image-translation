"""Loopback-only review desk; feedback never changes Agent quality evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit
import webbrowser

from .review_groups import group_id, group_tasks, revision_instruction, summarize
from .project_monitor import ProjectMonitor
from .workflow_desk import WorkflowDesk

APP_ID = 'single-image-localizer-review-desk'
MAX_BODY = 128 * 1024
MAX_JSON = 8 * 1024 * 1024
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.webp': 'image/webp', '.gif': 'image/gif'}
DECISIONS = {'approved', 'changes_requested', 'comment'}


class DeskError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def validate_dsh_origin(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('DSH origin must be an exact loopback HTTP origin')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')
                 and parsed.port is not None and 1 <= parsed.port <= 65535
                 and parsed.username is None and parsed.password is None
                 and not parsed.path and not parsed.query and not parsed.fragment
                 and value == f'http://{parsed.hostname}:{parsed.port}')
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('DSH origin must be exact http://127.0.0.1:PORT or http://localhost:PORT without path')
    return value


def project_identity(project_root: Path | str) -> str:
    normalized = os.path.normcase(str(Path(project_root).resolve(strict=True)))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def digest(path: Path | None) -> str | None:
    if path is None:
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class ReviewDesk:
    """Discover known local formats without rendering or invoking quality gates."""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve(strict=True)
        self.lock = threading.RLock()
        self.monitor = ProjectMonitor(self)
        self.workflow = WorkflowDesk(self)

    def safe_path(self, value: str | Path, base: Path | None = None) -> Path:
        candidate = Path(str(value).replace('\\', '/'))
        if not candidate.is_absolute():
            candidate = (base or self.root) / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise DeskError(403, 'Path outside project')
        return resolved

    def read(self, path: Path, warnings: list[str]) -> dict:
        try:
            path = self.safe_path(path)
            if path.stat().st_size > MAX_JSON:
                raise ValueError('too large')
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError('expected object')
            return value
        except (OSError, ValueError, DeskError):
            warnings.append(f'Unreadable record: {self.relative(path)}')
            return {}

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    def existing(self, base: Path, value) -> Path | None:
        if not isinstance(value, (str, Path)) or not str(value):
            return None
        try:
            path = self.safe_path(value, base)
            return path if path.is_file() else None
        except (DeskError, OSError, ValueError):
            return None

    def discover(self) -> tuple[dict, list[str]]:
        warnings: list[str] = []
        manifests: dict[Path, dict] = {}
        receipts: dict[Path, dict] = {}
        for folder in ('runs', 'work'):
            start = self.root / folder
            if not start.is_dir() or self._linked(start):
                continue
            for current, dirs, files in os.walk(start, followlinks=False):
                dirs[:] = [name for name in dirs if not name.startswith('.')
                           and not self._linked(Path(current) / name)]
                for name in files:
                    if not (name == 'receipt.json' or (name.startswith('manifest') and name.endswith('.json'))
                            or (Path(current).name == 'semantics' and name.endswith('.json'))):
                        continue
                    path = Path(current) / name
                    if self._linked(path):
                        continue
                    record = self.read(path, warnings)
                    if isinstance(record.get('regions'), list) and 'source' in record:
                        manifests[path.resolve()] = record
                    elif name == 'receipt.json' and (record.get('stage') == 'render' or 'output_sha256' in record):
                        receipts[path.resolve()] = record
                    elif name.startswith('manifest') and record:
                        warnings.append(f'Invalid semantic record: {self.relative(path)}')
                    elif name == 'receipt.json' and record and record.get('stage') != 'background':
                        warnings.append(f'Unsupported receipt: {self.relative(path)}')
        tasks = {}
        used = set()
        for receipt_path, receipt in receipts.items():
            directory = receipt_path.parent
            plan_path = directory / 'plan.json'
            plan = self.read(plan_path, warnings) if plan_path.is_file() else {}
            manifest_path = self._manifest_for(directory, plan, receipt, manifests)
            manifest = manifests.get(manifest_path, {})
            if plan.get('semantic_manifest') and manifest_path is None:
                warnings.append(f'Semantic evidence missing or changed: {self.relative(directory)}')
            if manifest_path:
                used.add(manifest_path)
            try:
                task = self._task(directory, manifest_path, manifest, plan, receipt, warnings)
                tasks[task['id']] = task
            except (OSError, ValueError, TypeError, DeskError):
                warnings.append(f'Unreadable task: {self.relative(directory)}')
        for path, manifest in manifests.items():
            if path not in used and path.parent.name != 'semantics':
                try:
                    task = self._task(path.parent, path, manifest, {}, {}, warnings)
                    task['id'] = self.relative(path.parent) + '::' + path.name
                    tasks[task['id']] = task
                except (OSError, ValueError, TypeError, DeskError):
                    warnings.append(f'Unreadable task: {self.relative(path)}')
        return tasks, list(dict.fromkeys(warnings))

    @staticmethod
    def _linked(path):
        return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()

    def _manifest_for(self, directory, plan, receipt, manifests):
        inputs = receipt.get('input_sha256', {})
        if isinstance(inputs, dict):
            for value, expected in inputs.items():
                candidate = self.existing(directory, value)
                if candidate in manifests and digest(candidate) == expected:
                    return candidate
        if receipt.get('stage') == 'render':
            # Copied plans retain paths relative to their original input directory.
            # An unbound nearby manifest may belong to another language/version.
            return None
        expected = receipt.get('manifest_sha256')
        for path in manifests:
            if expected and digest(path) == expected:
                return path
        # Basic receipts bind the run's live manifest even after it changes;
        # expose the live hash so an old browser cannot approve the new bytes.
        if directory.parent.name == 'renders':
            candidate = directory.parent.parent / 'manifest.json'
            if candidate in manifests:
                return candidate
        return None

    def _task(self, directory, manifest_path, manifest, plan, receipt, warnings):
        source_data = manifest.get('source', {})
        source_value = source_data.get('path') if isinstance(source_data, dict) else source_data
        source = self.existing(directory, 'source.png')
        if source is None and manifest_path:
            source = self.existing(manifest_path.parent, source_value)
        output = self.existing(directory, 'translated.png') if receipt else None
        background = self.existing(directory, 'background.png') if receipt else None
        text_layer = self.existing(directory, 'text-layer.png') if receipt else None
        language = manifest.get('target_language') or plan.get('target_language') or ''
        if not language and receipt:
            language = directory.name if directory.parent.joinpath('job-render.json').is_file() else ''
        quality_path = directory / 'quality.json'
        recorded = self.read(quality_path, warnings) if quality_path.is_file() else {}
        status = 'needs_review' if output else 'pending'
        if recorded.get('status') == 'fail' or receipt.get('technical') == 'fail':
            status = 'fail'
        quality = {'status': status, 'recorded': recorded or None,
                   'receipt_status': receipt.get('status'), 'technical': receipt.get('technical'),
                   'semantic_manifest_bound': manifest_path is not None,
                   'manifest_binding': 'semantic_manifest' if manifest_path else 'render_plan',
                   'notice': 'Recorded quality is historical evidence, not current user acceptance.'}
        images = {}
        for role, path in (('source', source), ('output', output), ('background', background), ('text_layer', text_layer)):
            if path and path.suffix.lower() in IMAGE_TYPES:
                images[role] = path
        artifacts = []
        inventory = receipt.get('artifact_sha256', {})
        if isinstance(inventory, dict):
            for name in inventory:
                path = self.existing(directory, name)
                if path and path.suffix.lower() in IMAGE_TYPES:
                    images.setdefault(name, path)
                    artifacts.append({'name': name, 'url': self.asset_url(path)})
        regions = []
        for region in manifest.get('regions', []):
            if isinstance(region, dict):
                regions.append({key: region.get(key, [] if key == 'bbox' else '')
                                for key in ('id', 'action', 'source_text', 'target_text', 'bbox', 'notes')})
        paths = [path for path in (manifest_path, output, directory / 'receipt.json') if path and path.is_file()]
        updated = max((path.stat().st_mtime for path in paths), default=directory.stat().st_mtime)
        return {'id': self.relative(directory), 'title': self.relative(directory), 'language': language,
                'kind': 'layered' if receipt.get('stage') == 'render' else 'basic',
                'updated_at': datetime.fromtimestamp(updated, timezone.utc).isoformat(),
                'status': status, 'has_output': 'output' in images,
                'source_url': self.asset_url(images.get('source')),
                'output_url': self.asset_url(images.get('output')),
                'background_url': self.asset_url(images.get('background')),
                'text_layer_url': self.asset_url(images.get('text_layer')),
                'manifest_sha256': digest(manifest_path or self.existing(directory, 'plan.json')),
                'source_sha256': digest(source), 'output_sha256': digest(output),
                'regions': regions, 'quality': quality, 'artifacts': artifacts,
                '_images': images, '_manifest': manifest_path, '_output': output}

    def asset_url(self, path):
        return '/api/assets?path=' + quote(self.relative(path), safe='') if path else None

    def list_tasks(self):
        tasks, warnings = self.discover()
        summaries = sorted((summarize(item) for item in tasks.values()),
                           key=lambda item: (item['updated_at'], item['id']), reverse=True)
        return {'tasks': summaries, 'groups': group_tasks(summaries), 'warnings': warnings}

    def get_task(self, task_id):
        tasks, _ = self.discover()
        if task_id not in tasks:
            raise DeskError(404, 'Task not found')
        return tasks[task_id]

    def detail(self, task_id):
        with self.lock:
            task = self.get_task(task_id)
            result = {key: value for key, value in task.items() if not key.startswith('_')}
            result.update(self.monitor.task_timing(task))
            result['feedback'] = self.feedback_records(task_id)
            result['group_id'] = group_id(task)
            result['version_label'] = summarize(task)['version_label']
            return result

    def revision_prompt(self, task_id):
        with self.lock:
            task = self.get_task(task_id)
            return revision_instruction(task, self.feedback_records(task_id))

    def feedback_path(self, task_id):
        name = hashlib.sha256(task_id.encode('utf-8')).hexdigest() + '.jsonl'
        return self.safe_path(Path('.review-desk') / 'feedback' / name)

    def feedback_records(self, task_id):
        path = self.feedback_path(task_id)
        if not path.is_file():
            return []
        records = []
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        records.append(value)
                except ValueError:
                    continue
        return records

    def append_feedback(self, task_id, payload):
        if not isinstance(payload, dict):
            raise DeskError(400, 'Expected JSON object')
        decision = payload.get('decision')
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise DeskError(400, 'Invalid decision')
        for key in ('note', 'reviewer'):
            if not isinstance(payload.get(key), str) or not payload[key].strip() or len(payload[key]) > 10000:
                raise DeskError(400, key + ' is required (maximum 10000 characters)')
        regions = payload.get('regions', [])
        if not isinstance(regions, list) or len(regions) > 1000:
            raise DeskError(400, 'Invalid regions')
        with self.lock:
            task = self.get_task(task_id)
            valid_ids = {str(region['id']) for region in task['regions']}
            cleaned = []
            for region in regions:
                if (not isinstance(region, dict) or str(region.get('id')) not in valid_ids
                        or not isinstance(region.get('decision'), str) or region['decision'] not in DECISIONS
                        or not isinstance(region.get('note', ''), str)):
                    raise DeskError(400, 'Invalid region feedback')
                cleaned.append({'id': region['id'], 'decision': region['decision'], 'note': region.get('note', '')})
            if decision == 'approved' and any(region['decision'] == 'changes_requested' for region in cleaned):
                raise DeskError(400, 'Whole-image approval conflicts with requested region changes')
            for key in ('output_sha256', 'manifest_sha256', 'source_sha256'):
                if key not in payload or payload[key] != task[key]:
                    raise DeskError(409, 'Task artifacts changed; reload before saving feedback')
            if (decision == 'approved' or any(region['decision'] == 'approved' for region in cleaned)) and not task['has_output']:
                raise DeskError(400, 'Approval requires an output image')
            record = {'id': secrets.token_hex(12), 'task_id': task_id,
                      'created_at': datetime.now(timezone.utc).isoformat(),
                      'decision': decision, 'note': payload['note'].strip(), 'reviewer': payload['reviewer'].strip(),
                      'output_sha256': task['output_sha256'], 'manifest_sha256': task['manifest_sha256'],
                      'source_sha256': task['source_sha256'], 'regions': cleaned}
            path = self.feedback_path(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path = self.feedback_path(task_id)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            return record

    def asset(self, value):
        if not value or Path(value.replace('\\', '/')).is_absolute() or '..' in value.replace('\\', '/').split('/'):
            raise DeskError(403, 'Asset is not allowed')
        path = self.safe_path(value)
        tasks, _ = self.discover()
        allowed = {image for task in tasks.values() for image in task['_images'].values()}
        if path not in allowed or path.suffix.lower() not in IMAGE_TYPES:
            raise DeskError(403, 'Asset is not allowed')
        return path


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, project_root, port=0, dsh_origin=None):
        self.dsh_origin = validate_dsh_origin(dsh_origin)
        self.desk = ReviewDesk(project_root)
        self.project_id = project_identity(self.desk.root)
        self.token = secrets.token_urlsafe(32)
        self.web_root = Path(__file__).parent / 'review_web'
        super().__init__(('127.0.0.1', port), ReviewHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def log_message(self, *_args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': self.responses.get(code, ('HTTP error',))[0]})

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        ancestor = self.server.dsh_origin or "'none'"
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors " + ancestor)
        super().end_headers()

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self, token=False, iframe_navigation=False):
        if self.headers.get_all('Host', []) != [f'127.0.0.1:{self.server.server_port}']:
            raise DeskError(403, 'Invalid Host')
        origin = self.headers.get('Origin')
        if iframe_navigation and self.server.dsh_origin:
            referer = self.headers.get('Referer', '')
            try:
                parsed = urlsplit(referer)
                reference_origin = f'{parsed.scheme}://{parsed.netloc}'
            except ValueError:
                reference_origin = None
            if (self.headers.get('Sec-Fetch-Dest') == 'iframe'
                    and self.headers.get('Sec-Fetch-Site') == 'same-site'
                    and reference_origin == self.server.dsh_origin
                    and origin in (None, self.server.dsh_origin)):
                return
        if origin is not None and origin != self.server.origin:
            raise DeskError(403, 'Invalid Origin')
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            raise DeskError(403, 'Cross-site request refused')
        if token and not secrets.compare_digest(self.headers.get('X-Review-Token', ''), self.server.token):
            raise DeskError(403, 'Review token required')

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def do_OPTIONS(self):
        self.respond(405, {'error': 'Method not allowed'})

    def dispatch(self, post):
        self._body_consumed = False
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc:
                raise DeskError(400, 'Invalid request target')
            path = parsed.path
            is_public = path in ('/api/bootstrap', '/api/assets')
            self.check_origin(token=post or (path.startswith('/api/') and not is_public),
                              iframe_navigation=not post and path in ('/', '/index.html') and not parsed.query)
            if post:
                if path in ('/api/workflow/snapshot', '/api/workflow/index'):
                    payload = self.read_body()
                    if not isinstance(payload, dict):
                        raise DeskError(400, 'A workflow snapshot object is required')
                    session_ids = payload.get('session_ids')
                    if not isinstance(session_ids, list) or len(session_ids) > 100 or any(not isinstance(value, str) or not value or len(value) > 200 for value in session_ids):
                        raise DeskError(400, 'A bounded session_ids list is required')
                    workflow = self.server.desk.workflow
                    self.respond(200, workflow.session_index(session_ids) if path.endswith('/index') else workflow.snapshot(session_ids=session_ids))
                elif path == '/api/workflow/unassigned':
                    payload = self.read_body()
                    if not isinstance(payload, dict) or not isinstance(payload.get('run_root'), str):
                        raise DeskError(400, 'A historical run_root is required')
                    self.respond(200, self.server.desk.workflow.inspect_unassigned(payload['run_root']))
                elif path == '/api/workflow/ingest':
                    self.respond(200, self.server.desk.workflow.ingest(self.read_body()))
                elif path == '/api/workflow/comments':
                    self.respond(201, self.server.desk.workflow.comment(self.read_body()))
                elif path == '/api/workflow/delivery':
                    self.respond(200, self.server.desk.workflow.record_delivery(self.read_body()))
                elif path == '/api/workflow/results/save':
                    self.respond(201, self.server.desk.workflow.save_result(self.read_body()))
                elif path == '/api/workflow/bind':
                    self.respond(200, self.server.desk.workflow.bind(self.read_body()))
                elif path == '/api/monitor/evaluations':
                    self.respond(201, {'evaluation': self.server.desk.monitor.append_evaluation(self.read_body())})
                elif path.startswith('/api/tasks/') and path.endswith('/feedback'):
                    payload = self.read_body()
                    task_id = unquote(path[len('/api/tasks/'):-len('/feedback')])
                    self.respond(201, {'feedback': self.server.desk.append_feedback(task_id, payload)})
                else:
                    raise DeskError(404, 'Endpoint not found')
            elif path == '/api/bootstrap':
                self.respond(200, {'token': self.server.token, 'app': APP_ID,
                                   'project_id': self.server.project_id,
                                   'host_origin': self.server.dsh_origin,
                                   'capabilities': ['groups', 'revision_prompt', 'dsh_bridge']})
            elif path == '/api/workflow/asset':
                query = parse_qs(parsed.query)
                values = query.get('path', [])
                expected = query.get('sha256', [])
                if len(values) != 1 or len(expected) != 1 or set(query) != {'path', 'sha256'}:
                    raise DeskError(400, 'One stage artifact and hash are required')
                artifact = self.server.desk.workflow.artifact(values[0])
                if artifact is None:
                    raise DeskError(404, 'Stage artifact is unavailable')
                if digest(artifact) != expected[0]:
                    raise DeskError(409, 'Stage artifact changed')
                self.send_file(artifact)
            elif path == '/api/monitor':
                self.respond(200, self.server.desk.monitor.snapshot())
            elif path == '/api/tasks':
                self.respond(200, self.server.desk.list_tasks())
            elif path.startswith('/api/tasks/') and path.endswith('/revision-prompt'):
                task_id = unquote(path[len('/api/tasks/'):-len('/revision-prompt')])
                self.respond(200, self.server.desk.revision_prompt(task_id))
            elif path.startswith('/api/tasks/'):
                self.respond(200, self.server.desk.detail(unquote(path[len('/api/tasks/'):])) )
            elif path == '/api/workflow/session-ids':
                self.respond(200, self.server.desk.workflow.session_ids())
            elif path == '/api/assets':
                values = parse_qs(parsed.query).get('path', [])
                if len(values) != 1:
                    raise DeskError(400, 'One asset path is required')
                self.send_file(self.server.desk.asset(values[0]))
            elif path.startswith('/api/'):
                raise DeskError(404, 'Endpoint not found')
            else:
                self.static_file(path)
        except DeskError as error:
            self.close_connection = post
            if post:
                self.discard_pending_body()
            self.respond(error.status, {'error': str(error)})
        except (OSError, ValueError, TypeError, KeyError):
            self.close_connection = post
            if post:
                self.discard_pending_body()
            self.respond(500, {'error': 'Unable to process local review data'})

    def discard_pending_body(self):
        """Drain a small rejected request so Windows does not reset its response."""
        if self._body_consumed or self.headers.get('Transfer-Encoding'):
            return
        values = self.headers.get_all('Content-Length', [])
        if len(values) != 1 or not values[0].isdigit():
            return
        length = int(values[0])
        if not 0 < length <= min(MAX_BODY, 128 * 1024):
            return
        previous_timeout = self.connection.gettimeout()
        try:
            self.connection.settimeout(0.1)
            self.rfile.read(length)
            self._body_consumed = True
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous_timeout)

    def read_body(self):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            raise DeskError(400, 'One Content-Length is required')
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            raise DeskError(400, 'Invalid Content-Length') from None
        if not 0 < length <= MAX_BODY:
            raise DeskError(413, 'Request body too large or empty')
        if self.headers.get_content_type() != 'application/json':
            raise DeskError(415, 'Content-Type must be application/json')
        self.connection.settimeout(10)
        try:
            body = self.rfile.read(length)
            self._body_consumed = True
            if len(body) != length:
                raise ValueError('incomplete')
            return json.loads(body)
        except (ValueError, OSError):
            raise DeskError(400, 'Invalid JSON body') from None

    def send_file(self, path):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', IMAGE_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def static_file(self, path):
        root = self.server.web_root.resolve()
        candidate = (root / (unquote(path).lstrip('/') or 'index.html')).resolve()
        if not candidate.is_relative_to(root) or candidate.suffix not in ('.html', '.css', '.js', '.ico') or not candidate.is_file():
            raise DeskError(404, 'Page not found')
        self.send_file(candidate)


def create_server(project_root: Path | str, port: int = 0, dsh_origin: str | None = None) -> ReviewServer:
    """Construct a loopback server; caller owns serve_forever/shutdown/server_close."""
    return ReviewServer(project_root, port, dsh_origin=dsh_origin)


def _existing_service_matches(port: int, project_id: str, dsh_origin: str | None = None) -> bool:
    # HTTPConnection avoids proxies and redirect handling: this probe must never
    # leave the exact loopback address supplied by the caller.
    connection = HTTPConnection('127.0.0.1', port, timeout=2)
    try:
        connection.request('GET', '/api/bootstrap')
        response = connection.getresponse()
        if response.status != 200 or response.headers.get_content_type() != 'application/json':
            return False
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            return False
        identity = json.loads(body)
        return (isinstance(identity, dict) and identity.get('app') == APP_ID
                and identity.get('project_id') == project_id
                and (dsh_origin is None or identity.get('host_origin') == dsh_origin))
    except (OSError, HTTPException, ValueError):
        return False
    finally:
        connection.close()


def serve(project_root: Path | str, port: int = 0, open_browser: bool = False,
          dsh_origin: str | None = None) -> None:
    """Serve locally, or reuse a verified same-project desk on the fixed port."""
    dsh_origin = validate_dsh_origin(dsh_origin)
    project_id = project_identity(project_root)
    try:
        server = create_server(project_root, port, dsh_origin=dsh_origin)
    except OSError as error:
        address_in_use = error.errno == errno.EADDRINUSE or getattr(error, 'winerror', None) == 10048
        if not port or not address_in_use:
            raise
        if not _existing_service_matches(port, project_id, dsh_origin=dsh_origin):
            raise OSError(f'Port {port} is occupied by another application or project, or has a different DSH origin; choose another port.') from error
        url = f'http://127.0.0.1:{port}/'
        print(f'Reusing review desk: {url}', flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    with server:
        print(f'Review desk: {server.origin}/', flush=True)
        if open_browser:
            webbrowser.open(server.origin + '/')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
