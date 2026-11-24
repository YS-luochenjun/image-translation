"""Copy independent artifact paths, using filesystem cloning when available."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import sys


def copy_artifact(source: Path, destination: Path) -> str:
    if sys.platform == 'darwin':
        clonefile = getattr(ctypes.CDLL(None), 'clonefile', None)
        if clonefile is not None:
            clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
            clonefile.restype = ctypes.c_int
            if clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0:
                return 'clone'
    shutil.copyfile(source, destination)
    return 'copy'
