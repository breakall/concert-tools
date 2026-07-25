"""Read-only, vendor-neutral descriptions of patch bundles."""

from __future__ import annotations

import plistlib
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .bundle import confined_child
from .errors import ConcertFormatError


@dataclass(frozen=True)
class ChannelManifest:
    """The stable identity and setting file of one channel strip."""

    name: str
    filename: str
    uuid: str
    instance_id: int


@dataclass(frozen=True)
class PatchManifest:
    """A minimal description that does not depend on a particular template."""

    path: Path
    name: str
    channels: tuple[ChannelManifest, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConcertFormatError(f"Patch {field} must be a non-empty string.")
    return value


def inspect_patch_manifest(path: Path) -> PatchManifest:
    """Read and validate the portable identity fields of a ``.patch`` bundle."""
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or path.suffix.casefold() != ".patch":
        raise ConcertFormatError(f"Not a regular .patch bundle: {path}")

    data_path = path / "data.plist"
    if data_path.is_symlink() or not data_path.is_file():
        raise ConcertFormatError(f"Patch has no regular data.plist: {path}")
    try:
        with data_path.open("rb") as handle:
            data = plistlib.load(handle)
    except (EOFError, OverflowError, ValueError, plistlib.InvalidFileException) as error:
        raise ConcertFormatError(f"Invalid patch data.plist: {path}") from error
    if not isinstance(data, dict):
        raise ConcertFormatError(f"Patch data.plist is not a dictionary: {path}")

    raw_patch = data.get("patch")
    if not isinstance(raw_patch, dict) or not isinstance(raw_patch.get("engineNode"), dict):
        raise ConcertFormatError(f"Patch engine node is not a dictionary: {path}")
    engine = raw_patch["engineNode"]
    name = _required_string(engine.get("name"), field="name")
    raw_channels = data.get("channels")
    if not isinstance(raw_channels, list) or not raw_channels:
        raise ConcertFormatError(f"Patch channels must be a non-empty array: {path}")

    channels: list[ChannelManifest] = []
    for index, raw in enumerate(raw_channels, start=1):
        if not isinstance(raw, dict):
            raise ConcertFormatError(f"Patch channel {index} is not a dictionary: {path}")
        channel_name = _required_string(raw.get("Channel_name"), field=f"channel {index} name")
        filename = _required_string(raw.get("Filename"), field=f"channel {index} filename")
        channel_uuid = _required_string(raw.get("UUID"), field=f"channel {index} UUID")
        try:
            uuid.UUID(channel_uuid)
        except ValueError as error:
            raise ConcertFormatError(
                f"Patch channel {index} has an invalid UUID: {channel_uuid!r}"
            ) from error
        instance_id = raw.get("Channel_instID")
        if isinstance(instance_id, bool) or not isinstance(instance_id, int):
            raise ConcertFormatError(f"Patch channel {index} instance ID must be an integer.")
        setting_path = confined_child(path, filename, suffix=".cst", kind="channel strip")
        if not setting_path.is_file():
            raise ConcertFormatError(f"Patch channel {index} setting is missing: {setting_path}")
        channels.append(ChannelManifest(channel_name, filename, channel_uuid, instance_id))

    return PatchManifest(path=path, name=name, channels=tuple(channels))
