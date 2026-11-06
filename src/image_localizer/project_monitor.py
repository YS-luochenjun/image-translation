"""Bounded project evidence index and explicitly human-authored evaluations.

File contents are untrusted data. Pixel checks never certify visual quality.
This module never runs benchmarks, models, quality gates or session-log scans.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time

from .review_groups import group_tasks

RUBRIC_VERSION = 'image-agent-v1'
DIMENSIONS = ('semantic', 'style', 'background', 'composition', 'process')
BINDINGS = ('source_sha256', 'manifest_sha256', 'output_sha256')
STATUSES = ('pass', 'fail', 'pending', 'unknown')
MAX_ENTRIES = 10000
MAX_DEPTH = 12
MAX_TASKS = 300
MAX_BENCHMARKS = 40
MAX_JSON_BYTES = 512 * 1024
MAX_READ_BYTES = 16 * 1024 * 1024
MAX_HASH_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_HISTORY_LINES = 2000
CACHE_SECONDS = 5


def _now():
    return datetime.now(timezone.utc).isoformat()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _constant(_value):
    raise ValueError('Non-finite JSON value')


def _json(data):
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=_object, parse_constant=_constant)


def _counts(values, keys=STATUSES):
    return {key: values.count(key) for key in keys}


def _bound(record, task):
    return all(key in record and record[key] == task.get(key) for key in BINDINGS)


class EvidenceReader:
    """One snapshot's aggregate budgets; refuse links and reparse points."""

    def __init__(self, root):
        self.root = root
        self.warnings = []
        self.read_bytes = 0
        self.hash_bytes = 0
        self.hashes = {}

    def warn(self, message):
        if message not in self.warnings and len(self.warnings) < 200:
            self.warnings.append(message)

    def relative(self, path):
        try:
            return Path(path).relative_to(self.root).as_posix()
        except ValueError:
            return '[outside project]'

    def safe(self, value, base=None):
        if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 4096:
            raise ValueError('Invalid local evidence path')
        candidate = Path(str(value).replace('\\', '/'))
        if not candidate.is_absolute():
            candidate = (base or self.root) / candidate
        candidate = Path(os.path.abspath(candidate))
        if not candidate.is_relative_to(self.root):
            raise ValueError('Evidence outside project')
        current = self.root
        for part in candidate.relative_to(self.root).parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Linked evidence refused')
        return candidate

    def existing(self, value, base=None):
        try:
            path = self.safe(value, base)
            if stat.S_ISREG(path.stat().st_mode):
                return path
        except (OSError, ValueError):
            self.warn('Missing or unsafe evidence: ' + self.relative((base or self.root) / str(value)))
        return None

    def read(self, path, maximum=MAX_JSON_BYTES, hashing=False):
        path = self.safe(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('Evidence exceeds file budget or is not a regular file')
        used = self.hash_bytes if hashing else self.read_bytes
        budget = MAX_HASH_BYTES if hashing else MAX_READ_BYTES
        if used + info.st_size > budget:
            raise ValueError('Evidence exceeds aggregate read budget')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Evidence changed while opening')
            data = stream.read(maximum + 1)
        self.safe(path)
        if len(data) > maximum or used + len(data) > budget:
            raise ValueError('Evidence grew beyond read budget')
        if hashing:
            self.hash_bytes += len(data)
        else:
            self.read_bytes += len(data)
        return data

    def record(self, path):
        try:
            value = _json(self.read(path))
            if not isinstance(value, dict):
                raise ValueError('Expected JSON object')
            return value
        except (OSError, ValueError, RecursionError):
            self.warn('Unreadable or unsafe record: ' + self.relative(path))
            return {}

    def digest(self, path):
        if path is None:
            return None
        if path not in self.hashes:
            try:
                self.hashes[path] = hashlib.sha256(self.read(path, MAX_FILE_BYTES, hashing=True)).hexdigest()
            except (OSError, ValueError):
                self.warn('Hash unavailable: ' + self.relative(path))
                self.hashes[path] = None
        return self.hashes[path]

    def inventory(self):
        result = []
        stack = [(self.root / folder, 0) for folder in ('work', 'runs')]
        visited = 0
        while stack:
            directory, depth = stack.pop()
            try:
                directory = self.safe(directory)
                if not directory.exists():
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > MAX_ENTRIES:
                            self.warn('Scan entry limit reached; monitor is a bounded partial index')
                            return result
                        path = Path(entry.path)
                        try:
                            self.safe(path)
                            if entry.is_dir(follow_symlinks=False):
                                if not entry.name.startswith('.'):
                                    if depth < MAX_DEPTH:
                                        stack.append((path, depth + 1))
                                    else:
                                        self.warn('Scan depth limit reached: ' + self.relative(path))
                            elif entry.is_file(follow_symlinks=False):
                                name = entry.name
                                if (name in ('receipt.json', 'report.json', 'benchmark.json') or
                                        (name.startswith('manifest') and name.endswith('.json')) or
                                        (directory.name == 'semantics' and name.endswith('.json'))):
                                    result.append(path)
                        except (ValueError, OSError):
                            self.warn('Unsafe evidence skipped: ' + self.relative(path))
            except (OSError, ValueError):
                self.warn('Unsafe or unreadable directory: ' + self.relative(directory))
        return sorted(result)

    def history(self, path):
        if not path.exists():
            return []
        try:
            data = self.read(path, MAX_HISTORY_BYTES)
        except (OSError, ValueError):
            self.warn('History unavailable or exceeds budget: ' + self.relative(path))
            return []
        records = []
        lines = data.splitlines()
        if len(lines) > MAX_HISTORY_LINES:
            self.warn('History line limit reached: ' + self.relative(path))
        for index, line in enumerate(lines[:MAX_HISTORY_LINES]):
            try:
                record = _json(line)
                if not isinstance(record, dict):
                    raise ValueError('Expected history object')
                records.append(record)
            except (ValueError, RecursionError):
                self.warn(f'Corrupt history line {index + 1}: ' + self.relative(path))
        return records


def _render_timing(reader, directory, manifest_path, plan_path, hashes):
    fields = {'duration_seconds': None, 'duration_scope': None,
              'timing_started_at': None, 'timing_finished_at': None,
              'timing_status': 'not_collected',
              'agent_translation_duration_seconds': None}
    path = directory / 'render-timing.json'
    if not path.exists():
        return fields
    record = reader.record(path)
    try:
        duration = record.get('duration_seconds')
        if (record.get('schema_version') != 1 or record.get('duration_scope') != 'render'
                or record.get('clock') != 'perf_counter' or record.get('status') != 'completed'
                or type(duration) not in (int, float) or not 0 <= duration < float('inf')):
            raise ValueError('Invalid timing schema')
        for key in ('started_at', 'finished_at'):
            timestamp = record.get(key)
            if not isinstance(timestamp, str) or len(timestamp) > 80 or datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError('Invalid timing timestamp')
        inputs, artifacts = record.get('input_sha256'), record.get('artifact_sha256')
        if (not isinstance(inputs, dict) or not inputs or len(inputs) > 128
                or not isinstance(artifacts, dict) or not hashes['output_sha256']
                or artifacts.get('translated.png') != hashes['output_sha256']):
            raise ValueError('Timing has no matching output')
        required = manifest_path or plan_path
        if required is None or inputs.get(str(required.resolve())) != reader.digest(required):
            raise ValueError('Timing has no matching manifest or plan')
        for value, expected in inputs.items():
            if not isinstance(expected, str) or reader.digest(reader.existing(value, directory)) != expected:
                raise ValueError('Timing input changed')
        if plan_path and artifacts.get('plan.json') != reader.digest(plan_path):
            raise ValueError('Timing plan changed')
    except (ValueError, TypeError, OverflowError):
        fields['timing_status'] = 'stale_or_invalid'
        reader.warn('Timing evidence missing or stale bindings: ' + reader.relative(path))
        return fields
    return {**fields, 'duration_seconds': duration, 'duration_scope': 'render',
            'timing_started_at': record['started_at'], 'timing_finished_at': record['finished_at'],
            'timing_status': 'collected'}


class ProjectMonitor:
    def __init__(self, desk):
        self.desk = desk
        self.root = desk.root
        self._cached = None
        self._cached_at = 0

    def task_timing(self, task):
        """Use the same bound timing evidence in review detail and monitor rows."""
        reader = EvidenceReader(self.root)
        directory = task['_output'].parent if task.get('_output') else self.root / task['id'].split('::', 1)[0]
        plan = directory / 'plan.json'
        plan_path = reader.existing(plan) if plan.exists() else None
        return _render_timing(reader, directory, task.get('_manifest'), plan_path,
                              {key: task.get(key) for key in BINDINGS})

    def snapshot(self, fresh=False):
        with self.desk.lock:
            if not fresh and self._cached is not None and time.monotonic() - self._cached_at < CACHE_SECONDS:
                return deepcopy(self._cached)
            result = self._snapshot()
            self._cached = result
            self._cached_at = time.monotonic()
            return deepcopy(result)

    def _snapshot(self):
        reader = EvidenceReader(self.root)
        inventory = reader.inventory()
        manifests, receipts, benchmarks = {}, {}, []
        for path in inventory:
            record = reader.record(path)
            if not record:
                continue
            if isinstance(record.get('regions'), list) and 'source' in record:
                manifests[path] = record
            elif path.name == 'receipt.json' and (record.get('stage') == 'render' or 'output_sha256' in record):
                receipts[path] = record
            elif path.name in ('report.json', 'benchmark.json'):
                if len(benchmarks) < MAX_BENCHMARKS:
                    benchmark = self._benchmark(reader, path, record)
                    if benchmark:
                        benchmarks.append(benchmark)
                else:
                    reader.warn('Benchmark limit reached; index is partial')
            elif path.name.startswith('manifest') or path.parent.name == 'semantics':
                reader.warn('Invalid semantic record: ' + reader.relative(path))
            elif path.name == 'receipt.json' and record.get('stage') != 'background':
                reader.warn('Unsupported receipt record: ' + reader.relative(path))
        tasks, used = [], set()
        for path, receipt in receipts.items():
            if len(tasks) >= MAX_TASKS:
                reader.warn('Task limit reached; task counts cover indexed tasks only')
                break
            directory = path.parent
            plan_path = directory / 'plan.json'
            plan = reader.record(plan_path) if plan_path.exists() else {}
            manifest_path = None
            inputs = receipt.get('input_sha256', {})
            if isinstance(inputs, dict):
                for value, expected in list(inputs.items())[:128]:
                    candidate = reader.existing(value, directory)
                    if candidate in manifests and reader.digest(candidate) == expected:
                        manifest_path = candidate
                        break
            if manifest_path is None and receipt.get('stage') != 'render':
                expected = receipt.get('manifest_sha256')
                manifest_path = next((p for p in manifests if expected and reader.digest(p) == expected), None)
                if manifest_path is None and directory.parent.name == 'renders':
                    nearby = directory.parent.parent / 'manifest.json'
                    manifest_path = nearby if nearby in manifests else None
            if manifest_path:
                used.add(manifest_path)
            try:
                tasks.append(self._task(reader, directory, manifest_path, manifests.get(manifest_path, {}), plan, receipt))
            except (OSError, ValueError, TypeError):
                reader.warn('Unreadable task: ' + reader.relative(directory))
        for path, manifest in manifests.items():
            if path not in used and path.parent.name != 'semantics':
                if len(tasks) >= MAX_TASKS:
                    reader.warn('Task limit reached; task counts cover indexed tasks only')
                    break
                try:
                    task = self._task(reader, path.parent, path, manifest, {}, {})
                except (OSError, ValueError, TypeError):
                    reader.warn('Unreadable task: ' + reader.relative(path))
                    continue
                task['id'] += '::' + path.name
                if not task['source_sha256']:
                    task['group_id'] = 'unresolved:' + hashlib.sha256(task['id'].encode()).hexdigest()
                tasks.append(task)
        tasks.sort(key=lambda task: (task['updated_at'] or '', task['id']), reverse=True)
        by_id = {task['id']: task for task in tasks}
        evaluations = []
        for record in reader.history(self.root / '.review-desk' / 'evaluations.jsonl'):
            if not self._valid_saved(record):
                reader.warn('Invalid evaluation record ignored')
                continue
            task = by_id.get(record['task_id'])
            evaluations.append({**record, 'current_version': task is not None and _bound(record, task)})
        current = [item for item in evaluations if item['current_version']]
        scores = {}
        for dimension in DIMENSIONS:
            values = [item['scores'][dimension] for item in current if item['scores'][dimension] is not None]
            scores[dimension] = {'mean': sum(values) / len(values) if values else None,
                                 'scored': len(values), 'total': len(current)}
        summary = {'tasks_total': len(tasks),
                   'production': {'with_output': sum(task['has_output'] for task in tasks),
                                  'pending': sum(not task['has_output'] for task in tasks)},
                   'technical': _counts([task['technical_status'] for task in tasks]),
                   'agent': _counts([task['agent_status'] for task in tasks]),
                   'user': _counts([task['user_status'] for task in tasks],
                                   ('approved', 'changes_requested', 'comment', 'not_recorded')),
                   'evaluations': {'total': len(evaluations), 'current': len(current)},
                   'dimension_scores': scores}
        return {'schema_version': 1, 'generated_at': _now(), 'summary': summary, 'tasks': tasks,
                'groups': group_tasks(tasks), 'benchmarks': benchmarks, 'evaluations': evaluations,
                'warnings': reader.warnings}

    def _task(self, reader, directory, manifest_path, manifest, plan, receipt):
        def optional(name):
            path = directory / name
            return reader.existing(path) if path.exists() else None

        source = optional('source.png')
        if source is None and manifest_path:
            source_record = manifest.get('source', {})
            value = source_record.get('path') if isinstance(source_record, dict) else source_record
            if isinstance(value, str):
                source = reader.existing(value, manifest_path.parent)
        output = optional('translated.png') if receipt else None
        plan_path = optional('plan.json')
        hashes = {'source_sha256': reader.digest(source), 'manifest_sha256': reader.digest(manifest_path or plan_path),
                  'output_sha256': reader.digest(output)}
        task_id = reader.relative(directory)
        source_hash = hashes['source_sha256']
        language = manifest.get('target_language') or plan.get('target_language')
        if not isinstance(language, str):
            language = ''
        if not language and (directory.parent / 'job-render.json').exists():
            language = directory.name
        quality_path, review_path = optional('quality.json'), optional('review.json')
        quality = reader.record(quality_path) if quality_path else {}
        review = reader.record(review_path) if review_path else {}
        inventory = receipt.get('artifact_sha256', {})
        expected_output = receipt.get('output_sha256') or (inventory.get('translated.png') if isinstance(inventory, dict) else None)
        output_bound = bool(hashes['output_sha256'] and expected_output == hashes['output_sha256'])
        expected_manifest = receipt.get('manifest_sha256')
        manifest_bound = not expected_manifest or expected_manifest == hashes['manifest_sha256']
        expected_source = receipt.get('source_sha256') or (inventory.get('source.png') if isinstance(inventory, dict) else None)
        source_bound = not expected_source or expected_source == hashes['source_sha256']
        expected_plan = inventory.get('plan.json') if isinstance(inventory, dict) else None
        plan_bound = not expected_plan or expected_plan == reader.digest(plan_path)
        technical = 'pending' if not output else 'unknown'
        if output and output_bound and manifest_bound and source_bound and plan_bound:
            value = receipt.get('technical') or quality.get('technical')
            technical = value if isinstance(value, str) and value in STATUSES else 'unknown'
        elif output:
            reader.warn('Technical evidence missing or stale: ' + task_id)
        agent = 'pending' if not review else 'unknown'
        review_manifest = review.get('manifest_sha256') if receipt.get('stage') != 'render' else review.get('plan_sha256')
        actual_manifest = hashes['manifest_sha256'] if receipt.get('stage') != 'render' else reader.digest(plan_path)
        review_bound = bool(hashes['output_sha256'] and review.get('output_sha256') == hashes['output_sha256']
                            and actual_manifest and review_manifest == actual_manifest)
        # Only expose recorded self-review gate results; never compute approval
        # from check booleans or pixel metrics.
        if (review_bound and source_bound and manifest_bound and plan_bound
                and review.get('review_kind') == 'self' and quality.get('review_kind') == 'self'):
            value = quality.get('status')
            agent = value if isinstance(value, str) and value in STATUSES else 'unknown'
        if review and not review_bound:
            reader.warn('Agent review is stale or unbound: ' + task_id)
        feedback_path = self.root / '.review-desk' / 'feedback' / (hashlib.sha256(task_id.encode()).hexdigest() + '.jsonl')
        current_feedback = [record for record in reader.history(feedback_path)
                            if record.get('task_id') == task_id and _bound(record, hashes)
                            and record.get('decision') in ('approved', 'changes_requested', 'comment')]
        paths = [path for path in (manifest_path, output, optional('receipt.json'), quality_path, review_path) if path]
        updated = max((path.stat().st_mtime for path in paths), default=None)
        return {'id': task_id, 'title': task_id, 'language': language or 'unknown',
                'group_id': 'source:' + source_hash if source_hash else 'unresolved:' + hashlib.sha256(task_id.encode()).hexdigest(),
                'version_label': ('待制作' if output is None else directory.parent.name if directory.name == language else directory.name),
                'has_output': output is not None,
                'updated_at': datetime.fromtimestamp(updated, timezone.utc).isoformat() if updated is not None else None,
                'technical_status': technical, 'agent_status': agent,
                'user_status': current_feedback[-1]['decision'] if current_feedback else 'not_recorded',
                **hashes, **_render_timing(reader, directory, manifest_path, plan_path, hashes), 'cost': None,
                'source_url': self.desk.asset_url(source), 'output_url': self.desk.asset_url(output),
                'evidence': {'receipt': reader.relative(directory / 'receipt.json') if receipt else None,
                             'quality': reader.relative(quality_path) if quality_path else None,
                             'review': reader.relative(review_path) if review_path else None}}

    def _benchmark(self, reader, path, record):
        cases = record.get('cases')
        candidates = record.get('candidates')
        schema = 'unknown'
        values = []
        local_warnings = []
        if type(record.get('schema_version')) is int and record['schema_version'] == 1:
            if isinstance(cases, list) and 'technical_expectations' in record:
                schema = 'benchmark-v1'
                for case in cases[:64]:
                    if not isinstance(case, dict):
                        local_warnings.append('Invalid benchmark case')
                        continue
                    value = case.get('technical')
                    values.append(value if isinstance(value, str) and value in STATUSES else 'unknown')
                    report_value = case.get('report')
                    if isinstance(report_value, str):
                        report_path = reader.existing(report_value, path.parent)
                        actual = reader.digest(report_path)
                        if actual is None or actual != case.get('report_sha256'):
                            local_warnings.append('Missing or changed case evidence: ' + str(case.get('id', 'unknown'))[:128])
                if len(cases) > 64:
                    local_warnings.append('Case count limit reached')
            elif isinstance(candidates, list) and 'binding' in record and 'technical' in record:
                schema = 'comparison-v1'
                values = [item.get('technical') if isinstance(item, dict) and item.get('technical') in STATUSES else 'unknown'
                          for item in candidates[:64]]
        if schema == 'unknown' and path.name != 'benchmark.json':
            return None
        if schema == 'unknown':
            local_warnings.append('Unknown benchmark schema; evidence is descriptive only')
        for message in local_warnings:
            reader.warn(reader.relative(path) + ': ' + message)
        return {'id': reader.relative(path), 'path': reader.relative(path), 'sha256': reader.digest(path),
                'schema': schema, 'technical_counts': _counts(values), 'evidence': record,
                'warnings': local_warnings}

    @staticmethod
    def _validate(payload):
        required = {'task_id', *BINDINGS, 'reviewer', 'agent_label', 'rubric_version', 'scores', 'note'}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError('Expected task_id, three hashes, reviewer, agent_label, rubric_version, scores and note')
        for field, maximum in (('task_id', 4096), ('reviewer', 200), ('note', 10000)):
            if not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > maximum:
                raise ValueError(field + ' must be a nonempty bounded string')
        if not isinstance(payload['agent_label'], str) or len(payload['agent_label']) > 200:
            raise ValueError('agent_label must be a string of at most 200 characters')
        for key in BINDINGS:
            value = payload[key]
            if value is not None and (not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)):
                raise ValueError('Invalid SHA256 binding')
        if payload['rubric_version'] != RUBRIC_VERSION:
            raise ValueError('rubric_version must be ' + RUBRIC_VERSION)
        scores = payload['scores']
        if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
            raise ValueError('scores must contain exactly the five rubric dimensions')
        if any(value is not None and (type(value) is not int or not 1 <= value <= 5) for value in scores.values()):
            raise ValueError('Scores must be integers 1..5 or null')

    @classmethod
    def _valid_saved(cls, record):
        fields = {'task_id', *BINDINGS, 'reviewer', 'agent_label', 'rubric_version', 'scores', 'note'}
        try:
            cls._validate({key: record[key] for key in fields})
            return isinstance(record.get('id'), str) and isinstance(record.get('created_at'), str)
        except (KeyError, ValueError):
            return False

    def append_evaluation(self, payload):
        from .review_desk import DeskError
        try:
            self._validate(payload)
        except ValueError as error:
            raise DeskError(400, str(error)) from None
        with self.desk.lock:
            snapshot = self.snapshot(fresh=True)
            task = next((task for task in snapshot['tasks'] if task['id'] == payload['task_id']), None)
            if task is None:
                raise DeskError(404, 'Task not found in bounded project index')
            if not _bound(payload, task):
                raise DeskError(409, 'Task artifacts changed; reload before saving evaluation')
            if not task['has_output'] and any(payload['scores'][key] is not None for key in DIMENSIONS if key != 'process'):
                raise DeskError(400, 'Visual scores require an output; only process may be scored before production')
            if task['has_output'] and any(task[key] is None for key in BINDINGS):
                raise DeskError(409, 'Artifact identity unavailable; evaluation cannot bind this output')
            reader = EvidenceReader(self.root)
            path = self.root / '.review-desk' / 'evaluations.jsonl'
            try:
                path = reader.safe(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path = reader.safe(path)
                record = {**payload, 'id': secrets.token_hex(12), 'created_at': _now()}
                for field in ('reviewer', 'agent_label', 'note'):
                    record[field] = record[field].strip()
                encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
                if path.exists():
                    history = reader.read(path, MAX_HISTORY_BYTES)
                    if len(history.splitlines()) >= MAX_HISTORY_LINES:
                        raise DeskError(409, 'Evaluation history line limit reached')
                    if history and not history.endswith(b'\n'):
                        encoded = b'\n' + encoded
                if path.exists() and path.stat().st_size + len(encoded) > MAX_HISTORY_BYTES:
                    raise DeskError(409, 'Evaluation history byte limit reached')
                flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
                with os.fdopen(os.open(path, flags, 0o600), 'ab') as stream:
                    stream.write(encoded)
            except ValueError as error:
                raise DeskError(403, 'Unsafe evaluation history path') from error
            self._cached = None
            return {**record, 'current_version': True}
