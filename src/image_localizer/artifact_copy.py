"""Copy independent artifact paths, using filesystem cloning when available."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4


def clone_artifact(source: Path, destination: Path) -> bool:
    if sys.platform == 'darwin':
        clonefile = getattr(ctypes.CDLL(None), 'clonefile', None)
        if clonefile is not None:
            clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
            clonefile.restype = ctypes.c_int
            return clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0
    return False


def copy_artifact(source: Path, destination: Path) -> str:
    if clone_artifact(source, destination):
        return 'clone'
    shutil.copyfile(source, destination)
    return 'copy'


def stage_artifact(source: Path, destination: Path, *, expected_sha256=None) -> dict:
    from .contracts import require
    from .pipeline import sha256

    source = source.resolve(strict=True)
    require(source.is_file() and not destination.is_symlink(), 'Expected a regular source and local destination')
    digest = sha256(source)
    require(expected_sha256 is None or digest == expected_sha256, 'Artifact bytes differ from reviewed evidence')
    mode = 'reused'
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f'.stage-{uuid4().hex}')
        try:
            mode = copy_artifact(source, temporary)
            require(sha256(temporary) == digest, 'Artifact changed during staging')
            try:
                os.link(temporary, destination)
            except FileExistsError:
                mode = 'reused'
        finally:
            temporary.unlink(missing_ok=True)
    require(not destination.is_symlink() and destination.is_file() and sha256(destination) == digest,
            'Existing destination differs; use a new revision path')
    return {'path': str(destination.resolve()), 'sha256': digest, 'mode': mode}
