"""Concrete, source-bound visual requirements; never an automatic style score."""
from __future__ import annotations

from .contracts import box, require


def validate_visual_targets(targets: object, size: tuple[int, int]) -> list[dict]:
    require(isinstance(targets, list) and len(targets) <= 20, 'visual_targets must be a list of at most20 items')
    identifiers = set()
    for target in targets:
        require(isinstance(target, dict) and set(target) == {'id', 'bbox', 'source_observation', 'criterion'},
                'Visual target requires id, bbox, source_observation and criterion')
        identity = target['id']
        require(isinstance(identity, str) and identity.strip() and identity not in identifiers,
                'Visual target IDs must be nonempty and unique')
        identifiers.add(identity)
        box(target['bbox'], *size, f'Visual target {identity}')
        for field in ('source_observation', 'criterion'):
            require(isinstance(target[field], str) and target[field].strip(), f'{identity}: {field} required')
    return targets


def comparison_template(targets: list[dict]) -> list[dict]:
    return [{**target, 'target_observation': '', 'verdict': None, 'evidence': []} for target in targets]


def check_comparisons(targets: list[dict], comparisons: object, artifacts: set[str]) -> list[bool | None]:
    """Require specific observations without pretending to validate their truth."""
    if not targets:
        return []
    require(isinstance(comparisons, list) and len(comparisons) == len(targets),
            'Each visual target requires exactly one style comparison')
    by_id = {}
    for comparison in comparisons:
        require(isinstance(comparison, dict), 'Style comparison must be an object')
        identity = comparison.get('id')
        require(isinstance(identity, str) and identity not in by_id, 'Duplicate style comparison ID')
        by_id[identity] = comparison
    results = []
    for target in targets:
        comparison = by_id.get(target['id'])
        require(comparison is not None, 'Missing style comparison ID')
        require(set(comparison) == set(target) | {'target_observation', 'verdict', 'evidence'},
                'Unknown or missing style comparison fields')
        require(all(comparison[key] == value for key, value in target.items()),
                'Style comparison changed the source requirement')
        verdict = comparison['verdict']
        require(verdict in (None, 'pass', 'fail', 'uncertain'), 'Invalid style comparison verdict')
        observation = comparison['target_observation']
        evidence = comparison['evidence']
        require(isinstance(observation, str), 'target_observation must be text')
        require(isinstance(evidence, list) and all(isinstance(name, str) and name in artifacts for name in evidence),
                'Style evidence must reference current hashed artifacts')
        if verdict == 'fail':
            results.append(False)
        elif verdict != 'pass' or not observation.strip() or 'source.png' not in evidence or not (
                {'translated.png', 'comparison.png', 'view.png'} & set(evidence)):
            results.append(None)
        else:
            results.append(True)
    return results
