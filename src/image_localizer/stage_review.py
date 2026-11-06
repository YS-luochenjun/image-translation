"""Explicit background approval binding shared by job and lower-level rendering."""
from pathlib import Path

from .contracts import require
from .pipeline import read_json, sha256

BACKGROUND_CHECKS = ('no_residual_text', 'seam_continuity', 'protected_content')


def verify_background_review(review_path: Path, *, source_sha256: str,
                             background: Path, receipt: Path) -> dict:
    require(review_path.is_file(), 'Background review is missing; inspect background before lettering')
    review = read_json(review_path)
    expected = {'source_sha256': source_sha256, 'background_sha256': sha256(background),
                'receipt_sha256': sha256(receipt)}
    for key, value in expected.items():
        require(review.get(key) == value, f'Background review has stale {key}')
    require(isinstance(review.get('reviewer'), str) and review['reviewer'].strip(), 'Background reviewer required')
    require(review.get('review_kind') in ('self', 'independent'), 'Invalid background review kind')
    require(isinstance(review.get('checks'), dict) and all(review['checks'].get(key) is True for key in BACKGROUND_CHECKS),
            'Background checks must be actually completed and pass before lettering')
    require(isinstance(review.get('observations'), str) and review['observations'].strip(),
            'Specific background observations required')
    issues = review.get('issues')
    require(isinstance(issues, list) and all(isinstance(issue, dict) and issue.get('severity') == 'minor'
                and isinstance(issue.get('note'), str) and issue['note'].strip() for issue in issues),
            'Background has invalid or blocking issues')
    return review
