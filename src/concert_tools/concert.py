"""Generic concert-package inspection."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

from .bundle import confined_child
from .errors import ConcertFormatError
from .patch import PatchInfo, inspect_patch


@dataclass(frozen=True)
class ConcertInfo:
    path: Path
    patches: tuple[PatchInfo, ...]
    issues: tuple[InspectionIssue, ...] = ()


@dataclass(frozen=True)
class InspectionIssue:
    location: str
    message: str


def load_concert_data(path: Path) -> tuple[Path, dict]:
    """Load the root patch and plist dictionary from a concert bundle."""
    root = path / "Concert.patch"
    if path.is_symlink() or root.is_symlink() or (root / "data.plist").is_symlink():
        raise ConcertFormatError(f"Symlinked concert structure is not supported: {path}")
    try:
        with (root / "data.plist").open("rb") as handle:
            data = plistlib.load(handle)
    except (FileNotFoundError, ValueError, OverflowError, plistlib.InvalidFileException) as error:
        raise ConcertFormatError(f"Not a recognized concert bundle: {path}") from error
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ConcertFormatError(f"Concert root has an unrecognized nodes structure: {path}")
    return root, data


def inspect_concert(path: Path) -> ConcertInfo:
    root, data = load_concert_data(path)
    patches: list[PatchInfo] = []
    issues: list[InspectionIssue] = []
    for node in data.get("nodes", []):
        try:
            patch_path = confined_child(root, node, suffix=".patch", kind="patch node")
            patches.append(inspect_patch(patch_path))
        except ConcertFormatError as error:
            location = node if isinstance(node, str) else repr(node)
            issues.append(InspectionIssue(location, str(error)))
            patches.append(
                PatchInfo(
                    path=root / str(node),
                    name=Path(str(node)).stem,
                    channels=(),
                    error=str(error),
                )
            )
    return ConcertInfo(path=path, patches=tuple(patches), issues=tuple(issues))
