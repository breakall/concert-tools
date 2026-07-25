"""Filesystem boundaries for untrusted concert-bundle metadata."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ConcertFormatError


def confined_child(base: Path, name: object, *, suffix: str, kind: str) -> Path:
    """Resolve a single bundle child without allowing traversal or symlinks."""
    if not isinstance(name, str) or not name:
        raise ConcertFormatError(f"{kind} name must be a non-empty string.")
    relative = Path(name)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {".", ".."}:
        raise ConcertFormatError(f"Unsafe {kind} path: {name!r}.")
    if relative.suffix != suffix:
        raise ConcertFormatError(f"{kind} path must end in {suffix!r}: {name!r}.")
    candidate = base / relative
    if candidate.is_symlink():
        raise ConcertFormatError(f"Symlinked {kind} paths are not supported: {name!r}.")
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve(strict=False)
    if not candidate_resolved.is_relative_to(base_resolved):
        raise ConcertFormatError(f"{kind} path escapes its bundle: {name!r}.")
    return candidate


def reject_symlink_tree(path: Path, *, kind: str) -> None:
    """Reject symlinks before a copied bundle is opened for writing."""
    if path.is_symlink():
        raise ConcertFormatError(f"Symlinked {kind} is not supported: {path}")
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        for name in [*directories, *files]:
            candidate = root_path / name
            if candidate.is_symlink():
                raise ConcertFormatError(f"Symlink inside {kind} is not supported: {candidate}")


def reject_nested_destination(source: Path, destination: Path) -> None:
    source_resolved = source.resolve()
    destination_resolved = destination.resolve(strict=False)
    if destination_resolved == source_resolved or destination_resolved.is_relative_to(source_resolved):
        raise ConcertFormatError("Destination cannot be the source concert or a child of it.")
