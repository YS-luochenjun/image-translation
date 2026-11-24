"""Store recent output fingerprints locally and emit short revision references."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _store(directory, scope, revision, fingerprints, since):
    with closing(sqlite3.connect(directory / '.output-checkpoints.sqlite3')) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS checkpoints '
                           '(scope TEXT, revision TEXT, fingerprints TEXT, UNIQUE(scope, revision))')
        previous = None
        if since and since != revision and re.fullmatch(r'[0-9a-f]{24}', since):
            row = connection.execute('SELECT fingerprints FROM checkpoints WHERE scope=? AND revision=?',
                                     (scope, since)).fetchone()
            if row:
                previous = json.loads(row[0])
        if not connection.execute('SELECT 1 FROM checkpoints WHERE scope=? AND revision=?',
                                  (scope, revision)).fetchone():
            connection.execute('INSERT OR IGNORE INTO checkpoints VALUES (?, ?, ?)',
                               (scope, revision, _json(fingerprints)))
            connection.execute('DELETE FROM checkpoints WHERE scope=? AND rowid NOT IN '
                               '(SELECT rowid FROM checkpoints WHERE scope=? ORDER BY rowid DESC LIMIT 8)',
                               (scope, scope))
    return previous


def checkpoint_output(result: dict, since: str | None, *, scope: str, directory: Path,
                      always: tuple[str, ...] = (), collections: dict | None = None,
                      snapshot: dict | None = None) -> dict:
    collections = collections or {}
    fingerprints = {
        'fields': {key: _digest(value) for key, value in result.items()},
        'collections': {name: {key: _digest(value) for key, value in items.items()}
                        for name, items in collections.items()},
    }
    revision = _digest([scope, fingerprints])[:24]
    if since == revision:
        return {**{key: result[key] for key in always}, 'mode': 'unchanged', 'checkpoint': revision}
    try:
        previous = _store(directory, scope, revision, fingerprints, since)
    except (sqlite3.Error, ValueError):
        previous = None
    if previous is None:
        return {**result, **(snapshot or {}), 'mode': 'snapshot', 'checkpoint': revision}
    output = {key: value for key, value in result.items()
              if key in always or previous['fields'].get(key) != fingerprints['fields'][key]}
    removed = sorted(previous['fields'].keys() - fingerprints['fields'].keys())
    if removed:
        output['removed_fields'] = removed
    for name, items in collections.items():
        before = previous['collections'].get(name, {})
        after = fingerprints['collections'][name]
        changed = [value for key, value in items.items() if before.get(key) != after[key]]
        deleted = sorted(before.keys() - after.keys())
        if changed or deleted:
            output[name] = {'upsert': changed, 'remove': deleted}
    return {**output, 'mode': 'delta', 'checkpoint': revision}
