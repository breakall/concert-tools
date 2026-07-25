"""Safe discovery and parsing of installed plug-in parameter metadata."""

from __future__ import annotations

import plistlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import ConcertFormatError


@dataclass(frozen=True)
class ParameterMetadataEntry:
    """One host parameter identifier and its semantic display name."""

    parameter_id: int
    name: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterMetadataTable:
    """A validated parameter-name table supplied by an installed host."""

    plugin_name: str
    path: Path
    entries: tuple[ParameterMetadataEntry, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "path": str(self.path),
            "entries": [entry.as_dict() for entry in self.entries],
        }


_PARAMETER_ENTRY = re.compile(r"^(\d+)\s+(.+)$")


def default_parameter_metadata_roots() -> tuple[Path, ...]:
    """Return host metadata roots in user-override then system order."""
    return (
        Path.home() / "Library/Application Support/Logic/Plug-In Settings",
        Path("/Library/Application Support/Logic/Plug-In Settings"),
    )


def _plugin_component(plugin_name: str) -> str:
    if (
        not isinstance(plugin_name, str)
        or not plugin_name
        or plugin_name in {".", ".."}
        or Path(plugin_name).name != plugin_name
        or any(not character.isprintable() for character in plugin_name)
    ):
        raise ConcertFormatError("Plugin name must be one non-empty path component.")
    return plugin_name


def load_parameter_metadata(path: Path, *, plugin_name: str | None = None) -> ParameterMetadataTable:
    """Load one ``CSParameterOrder.plist`` without following a table symlink."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.name != "CSParameterOrder.plist":
        raise ConcertFormatError(f"Not a regular CSParameterOrder.plist file: {path}")
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
    except (EOFError, OSError, OverflowError, ValueError, plistlib.InvalidFileException) as error:
        raise ConcertFormatError(f"Invalid parameter metadata plist: {path}") from error
    if not isinstance(payload, dict):
        raise ConcertFormatError(f"Parameter metadata root is not a dictionary: {path}")
    raw_entries = payload.get("ControlSurfaceParameterOrder")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ConcertFormatError(
            f"Parameter metadata has no non-empty ControlSurfaceParameterOrder array: {path}"
        )

    entries: list[ParameterMetadataEntry] = []
    for index, raw_entry in enumerate(raw_entries, start=1):
        if not isinstance(raw_entry, str):
            raise ConcertFormatError(f"Parameter metadata entry {index} is not a string: {path}")
        if raw_entry == "-1":
            continue
        match = _PARAMETER_ENTRY.fullmatch(raw_entry)
        if match is None:
            raise ConcertFormatError(f"Parameter metadata entry {index} is malformed: {path}")
        parameter_id = int(match.group(1))
        name = match.group(2).strip()
        if not name or any(not character.isprintable() for character in name):
            raise ConcertFormatError(f"Parameter metadata entry {index} has an invalid name: {path}")
        entries.append(ParameterMetadataEntry(parameter_id, name))

    resolved_name = _plugin_component(plugin_name if plugin_name is not None else path.parent.name)
    return ParameterMetadataTable(resolved_name, path, tuple(entries))


def discover_parameter_metadata(
    plugin_name: str,
    search_roots: Iterable[Path] | None = None,
) -> ParameterMetadataTable | None:
    """Return the first installed table for ``plugin_name``, or ``None``."""
    component = _plugin_component(plugin_name)
    roots = default_parameter_metadata_roots() if search_roots is None else tuple(search_roots)
    for raw_root in roots:
        root = Path(raw_root)
        plugin_directory = root / component
        if plugin_directory.is_symlink():
            raise ConcertFormatError(f"Plugin metadata directory is a symlink: {plugin_directory}")
        table_path = plugin_directory / "CSParameterOrder.plist"
        if table_path.is_symlink():
            raise ConcertFormatError(f"Plugin parameter metadata is a symlink: {table_path}")
        if table_path.is_file():
            return load_parameter_metadata(table_path, plugin_name=component)
    return None
