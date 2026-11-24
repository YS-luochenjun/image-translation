"""Serialize admission to the optimization workspace's Fireplay queue."""
from __future__ import annotations

from pathlib import Path

from .pipeline import read_json


def monitor_directory(root: Path) -> Path:
    return root / 'work/fireplay-monitor'


def _read_optional(path: Path, default: dict) -> dict:
    try:
        return read_json(path)
    except FileNotFoundError:
        return default


class Capacity:
    def __init__(self, root: Path):
        self.path = monitor_directory(root) / 'admissions.json'
        config = root / 'config/fireplay-monitor.json'
        if not config.exists():
            config = Path(__file__).resolve().parents[2] / 'config/fireplay-monitor.json'
        policy = read_json(config)
        self.limit = policy['max_active_requirements']
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError('Fireplay capacity must be a positive integer')
        self.entries = _read_optional(self.path, {'requirements': {}})['requirements']
        assignments = _read_optional(self.path.with_name('assignments.json'), {'requirements': {}})['requirements']
        self.active = set()
        for requirement_id in self.entries.keys() | assignments.keys():
            admission = self.entries.get(requirement_id, {})
            assignment = assignments.get(requirement_id, {})
            state = assignment.get('state')
            if assignment.get('unknown_operation') or assignment.get('pending_operation') or state in ('unknown', 'dispatch_unknown'):
                self.active.add(requirement_id)
            elif (admission.get('state') != 'released' and not assignment.get('delivery_verified_by_controller')
                  and state not in policy['capacity_terminal_states']
                  and (admission.get('state') == 'reserved' or assignment.get('responsible_thread_id')
                       or state in policy['capacity_active_states'])):
                self.active.add(requirement_id)

    def available(self, requirement_id: str) -> bool:
        return requirement_id in self.active or len(self.active) < self.limit

    def summary(self) -> dict:
        return {'limit': self.limit, 'active': len(self.active), 'available': max(0, self.limit - len(self.active))}

    def reserve(self, requirement_id: str, code: str) -> None:
        if not self.available(requirement_id):
            raise ValueError('Fireplay acceptance capacity reached')
        if self.entries.get(requirement_id, {}).get('state') != 'reserved':
            self._save(requirement_id, code, 'reserved')
        self.active.add(requirement_id)

    def release(self, requirement_id: str, code: str) -> None:
        if self.entries.get(requirement_id, {}).get('state') != 'released':
            self._save(requirement_id, code, 'released')

    def _save(self, requirement_id: str, code: str, state: str) -> None:
        from .fireplay import _write

        self.entries[requirement_id] = {'code': code, 'state': state}
        _write(self.path, {'requirements': self.entries})
