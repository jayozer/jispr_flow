"""Crash-safe whole-file writes: same-directory tmp file + ``os.replace``.

The one shared primitive behind every "rewrite the whole file" operation in
this project: the personalization JSON stores, history rotation/retention
rewrites, and scratchpad note saves. A crash mid-write -- process kill, disk
full, power loss -- can then never truncate or destroy the existing file:
the new content only becomes visible via the final atomic rename, and until
that rename the original file is untouched.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> float:
    """Atomically replace ``path``'s content with ``text`` (UTF-8).

    Writes to a tmp file in the same directory (same filesystem, so the
    final ``os.replace`` is an atomic rename; uniquely named via
    ``tempfile.NamedTemporaryFile`` so concurrent writers never collide on a
    shared ``<name>.tmp``), then renames it over ``path``. On any failure
    the tmp file is removed and ``path`` is left exactly as it was.

    Returns the resulting file's ``st_mtime``, statted on the tmp file
    *before* the rename publishes it (a rename preserves mtime). A caller
    that tracks "the mtime of my own last save" -- the scratchpad window's
    autosave conflict detection -- therefore gets a value that can never
    absorb a concurrent writer landing just after the rename: a race-free
    lower bound.
    """
    path = Path(path)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        try:
            tmp.write(text)
        finally:
            tmp.close()
        mtime = os.stat(tmp.name).st_mtime
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    return mtime
