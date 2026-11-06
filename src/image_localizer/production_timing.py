"""Local production timing, kept outside artifact and review hash gates."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4


_active_output = ContextVar('production_timing_output', default=None)


def bind_timing_output(path):
    """Register a versioned output selected inside the basic render call."""
    _active_output.set((path, path.exists()))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def timed_production(*, input_name, output_name=None, scope='render'):
    """Time a call; failure journals never create or overwrite its output.

    Sidecars are written after receipts/pointers are sealed, so timing does not
    enter their artifact inventories or change existing review hashes.
    """
    def decorate(function):
        parameters = signature(function)

        @wraps(function)
        def measured(*args, **kwargs):
            bound = parameters.bind(*args, **kwargs)
            source = Path(bound.arguments[input_name])
            input_path = source / 'manifest.json' if input_name == 'run' else source / 'bundle.json' if source.is_dir() else source
            output = Path(bound.arguments[output_name]) if output_name else None
            output_existed = output.exists() if output else False
            token = _active_output.set(None)
            started_at, started = _now(), perf_counter()
            inputs, result, error = {}, None, None
            try:
                if input_path.is_file():
                    inputs[str(input_path.resolve())] = _digest(input_path)
                    if input_name == 'plan_path':
                        plan = json.loads(input_path.read_text(encoding='utf-8'))
                        value = plan.get('semantic_manifest') if isinstance(plan, dict) else None
                        if isinstance(value, str):
                            semantic = (input_path.parent / value).resolve()
                            if semantic.is_file():
                                inputs[str(semantic)] = _digest(semantic)
                result = function(*args, **kwargs)
                return result
            except BaseException as caught:
                error = caught
                raise
            finally:
                dynamic_output = _active_output.get()
                _active_output.reset(token)
                if output is None and dynamic_output is not None:
                    output, output_existed = dynamic_output
                duration = max(0.0, perf_counter() - started)
                record = {'schema_version': 1, 'operation': function.__name__,
                          'duration_scope': scope, 'clock': 'perf_counter',
                          'started_at': started_at, 'finished_at': _now(),
                          'duration_seconds': duration,
                          'status': 'failed' if error else 'completed',
                          'input_sha256': inputs, 'artifact_sha256': {},
                          'output_produced': False,
                          'notice': 'Local tool execution only; excludes Agent analysis, translation, review and human waiting.'}
                try:
                    if error is None and result is not None:
                        output = Path(result.get('output_dir') or result.get('output') or output)
                        names = ['translated.png', 'plan.json', 'background.png', 'job-render.json']
                        if function.__name__ == 'render_job':
                            names.extend(f'{language}/{name}' for language in result.get('languages', {})
                                         for name in ('translated.png', 'plan.json'))
                        for name in names:
                            path = output / name
                            if path.is_file():
                                record['artifact_sha256'][name] = _digest(path)
                        record['output_produced'] = bool(record['artifact_sha256'])
                        record['result_status'] = result.get('status')
                        destination = output / 'render-timing.json'
                    else:
                        record['error'] = {'type': type(error).__name__, 'message': str(error)[:2000]}
                        record['requested_output'] = str(output.resolve()) if output else None
                        record['partial_artifact_sha256'] = {
                            path.relative_to(output).as_posix(): _digest(path)
                            for path in output.rglob('translated.png') if path.is_file()
                        } if output and not output_existed and output.is_dir() else {}
                        base = output.parent if output else source
                        destination = base / '.render-timings' / (uuid4().hex + '.json')
                    _write(destination, record)
                except OSError:
                    # Preserve the original exception if timing cannot be persisted.
                    if error is None:
                        raise
        return measured
    return decorate
