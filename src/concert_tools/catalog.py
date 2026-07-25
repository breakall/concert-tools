"""Generic discovery of patch bundles on disk."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ConcertFormatError


def scan_patch_bundles(root: Path) -> list[Path]:
    """Return physical ``.patch`` bundles below *root* without following symlinks.

    A patch bundle is treated as an opaque package here. Reading and interpreting
    its contents is deliberately left to :func:`inspect_patch_manifest`.
    """
    root = Path(root)
    if root.is_symlink():
        raise ConcertFormatError(f"Symlinked catalog roots are not supported: {root}")
    if not root.is_dir():
        raise ConcertFormatError(f"Patch catalog root is not a directory: {root}")
    if root.suffix.casefold() == ".patch":
        return [root]

    patches: list[Path] = []
    for current, directories, _files in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained: list[str] = []
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            if candidate.suffix.casefold() == ".patch":
                patches.append(candidate)
            else:
                retained.append(name)
        directories[:] = retained
    return sorted(patches, key=lambda path: path.relative_to(root).as_posix().casefold())
