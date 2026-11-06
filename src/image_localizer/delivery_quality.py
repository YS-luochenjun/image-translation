"""Read-only delivery gate; host scores never prove actual viewing or user acceptance."""
from __future__ import annotations

import hashlib
import math
import re
from io import BytesIO
from pathlib import Path

from PIL import Image

WEIGHTS = {'semantic_accuracy': 30, 'visual_continuity': 25,
           'overall_similarity': 20, 'readability': 15, 'composition': 10}
MINIMUMS = dict(zip(WEIGHTS, (5, 4, 3, 4, 3)))
CHECKS = ('amounts_numbers', 'claims', 'translation_complete', 'protected_identity',
          'protected_type', 'subject_integrity', 'no_visible_seams',
          'text_not_clipped', 'copy_edits_preserve_facts')


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object, label: str) -> None:
    _require(type(value) is str and bool(value.strip()), f'{label}: nonempty text required')


def _object(value: object, label: str) -> dict:
    _require(type(value) is dict, f'{label}: object required')
    return value


def _strings(value: object, label: str) -> list:
    _require(type(value) is list, f'{label}: array required')
    for item in value:
        _text(item, label)
    return value


def _check(item: object, label: str) -> bool:
    item = _object(item, label)
    _require(type(item.get('checked')) is bool, f'{label}.checked: bool required')
    _text(item.get('notes'), f'{label}.notes')
    return item['checked']


def _binding(value: object, label: str) -> dict:
    value = _object(value, label)
    _text(value.get('path'), f'{label}.path')
    sha = value.get('sha256')
    _require(type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha) is not None,
             f'{label}.sha256: lowercase SHA256 required')
    return value


def _image(binding: dict, base: Path) -> dict:
    path = Path(binding['path'])
    if not path.is_absolute():
        path = base / path
    data = path.read_bytes()
    _require(hashlib.sha256(data).hexdigest() == binding['sha256'], 'SHA256 mismatch')
    with Image.open(BytesIO(data)) as image:
        image.verify()
    with Image.open(BytesIO(data)) as image:
        image.load()
        return {'width': image.width, 'height': image.height,
                'format': image.format, 'bytes': len(data)}


def assess_delivery(review: dict, base: Path) -> dict:
    """Validate one member's declared review without writes or service calls.

    Required fields: requirement_id/source_id/language/member_id (text), source
    and artifact {path,sha256}, reviewer {identity,kind:self|independent},
    attestation {source_viewed,candidate_viewed,notes}, dimensions keyed by WEIGHTS
    with {score: finite 0..5,notes}, checks keyed by CHECKS with {checked,notes},
    requirements: nonempty [{id,checked,notes}], spec {width,height,max_bytes,
    formats:[Pillow format]}, blocking_issues/minor_differences: [text], and
    targeted_reworks: integer 0..2. Notes must describe actual observations.
    Optional unresolved_facts: [text] identifies missing facts that cannot be guessed.
    status is the submission gate; next_action preserves a repair opportunity for
    fixable drafts (even failed hard checks or scores below 70) within two rounds.
    Confirmed copy edits are allowed only while the facts check remains true.
    Tiny texture/shadow/nonidentity background differences may be recorded as
    minor differences; the host must consider these in its dimension notes.
    Malformed evidence raises ValueError; stale/unreadable files need review.
    Unexpected programming exceptions propagate; they never become a pass.
    """
    review = _object(review, 'review')
    _require(isinstance(base, Path), 'base: Path required')
    identity = {}
    for name in ('requirement_id', 'source_id', 'language', 'member_id'):
        _text(review.get(name), name)
        identity[name] = review[name]
    bindings = {name: _binding(review.get(name), name) for name in ('source', 'artifact')}
    reviewer = _object(review.get('reviewer'), 'reviewer')
    _text(reviewer.get('identity'), 'reviewer.identity')
    _require(reviewer.get('kind') in ('self', 'independent'), 'Invalid reviewer.kind')
    attestation = _object(review.get('attestation'), 'attestation')
    for name in ('source_viewed', 'candidate_viewed'):
        _require(type(attestation.get(name)) is bool, f'attestation.{name}: bool required')
    _text(attestation.get('notes'), 'attestation.notes')
    dimensions = _object(review.get('dimensions'), 'dimensions')
    _require(set(dimensions) == set(WEIGHTS), 'Exactly five dimensions required')
    scores = {}
    for name, item in dimensions.items():
        item = _object(item, name)
        score = item.get('score')
        _require(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 5,
                 f'{name}.score: finite number 0..5 required')
        _text(item.get('notes'), f'{name}.notes')
        scores[name] = score
    checks = _object(review.get('checks'), 'checks')
    _require(set(checks) == set(CHECKS), 'All named hard checks required')
    failures = [name for name in CHECKS if not _check(checks[name], name)]
    requirements = review.get('requirements')
    _require(type(requirements) is list and bool(requirements), 'Nonempty requirements required')
    seen = set()
    for item in requirements:
        item = _object(item, 'requirement')
        name = item.get('id')
        _text(name, 'requirement.id')
        _require(name not in seen, 'Duplicate requirement.id')
        seen.add(name)
        if not _check(item, f'requirement:{name}'):
            failures.append(f'requirement:{name}')
    spec = _object(review.get('spec'), 'spec')
    for name in ('width', 'height', 'max_bytes'):
        _require(type(spec.get(name)) is int and spec[name] > 0, f'spec.{name}: positive int required')
    formats = _strings(spec.get('formats'), 'spec.formats')
    _require(bool(formats), 'spec.formats must not be empty')
    unresolved = _strings(review.get('unresolved_facts', []), 'unresolved_facts')
    issues = _strings(review.get('blocking_issues'), 'blocking_issues')
    minor = _strings(review.get('minor_differences'), 'minor_differences')
    rounds = review.get('targeted_reworks')
    _require(type(rounds) is int and 0 <= rounds <= 2, 'targeted_reworks: integer 0..2 required')
    score = round(sum(scores[name] * weight / 5 for name, weight in WEIGHTS.items()), 6)
    result = {**identity, 'status': 'needs_review', 'score': score, 'reasons': [],
              'minor_differences': list(minor), 'targeted_reworks': rounds,
              'reworks_remaining': 2 - rounds, 'reviewer': dict(reviewer),
              'visual_assessment': 'host_declared', 'user_acceptance': 'not_assessed',
              'stop_refining': False, 'scope': 'member'}
    images, evidence_errors = {}, []
    for name, binding in bindings.items():
        try:
            images[name] = _image(binding, base)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as error:
            evidence_errors.append(f'{name}: {type(error).__name__}: {error}')
    result['images'] = images
    if 'artifact' in images:
        image = images['artifact']
        if any(image[name] != spec[name] for name in ('width', 'height')):
            failures.append('spec: actual pixel dimensions mismatch')
        if image['bytes'] > spec['max_bytes'] or image['format'] not in formats:
            failures.append('spec: illegal file size or format')
    if failures or issues or unresolved:
        result.update(status='blocked', reasons=failures + list(issues) + unresolved + evidence_errors)
    elif evidence_errors or not all(attestation[name] for name in ('source_viewed', 'candidate_viewed')):
        result['reasons'] = evidence_errors or ['Source and candidate must actually be viewed']
    elif score >= 80 and all(scores[name] >= value for name, value in MINIMUMS.items()):
        result.update(status='ready_for_submission', stop_refining=True)
    elif score >= 70 and rounds < 2:
        result.update(status='rework', reasons=['Score or dimension floor requires targeted rework'])
    else:
        result.update(status='blocked', reasons=['Rework limit reached' if rounds == 2 else 'Score below 70'])
    # A failed submission gate does not mean a repairable draft must stop.
    if evidence_errors or not all(attestation[name] for name in ('source_viewed', 'candidate_viewed')):
        result['next_action'] = 'review'
    elif result['status'] == 'ready_for_submission':
        result['next_action'] = 'submit'
    elif unresolved or rounds == 2:
        result['next_action'] = 'blocked'
    else:
        result['next_action'] = 'rework'
    return result
