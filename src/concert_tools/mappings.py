"""Read-only enumeration of screen-control parameter mappings."""

from __future__ import annotations

import plistlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import ConcertFormatError
from .keyed_archive import KeyedArchive
from .manifest import inspect_patch_manifest

IDENTITY_PREFIX = "\x01IDENTITY:"


@dataclass(frozen=True)
class ScreenControlMapping:
    """One mapping owned by one screen control in a patch."""

    control: str
    mapping_index: int
    mapping_class: str | None
    target_instance_id: int | None
    target_channel: str | None
    plugin_slot: int | None
    parameter_index: int | None
    saved_value: float | None
    range_low: float | None
    range_high: float | None
    discrete_steps: bool
    momentary: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolved(archive: KeyedArchive, value: Any) -> Any:
    return archive.dereference(value)


def _optional_int(archive: KeyedArchive, value: Any) -> int | None:
    value = _resolved(archive, value)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(archive: KeyedArchive, value: Any) -> float | None:
    value = _resolved(archive, value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _class_name(archive: KeyedArchive, value: Any) -> str | None:
    value = _resolved(archive, value)
    name = value.get("$classname") if isinstance(value, dict) else None
    return name if isinstance(name, str) and name else None


def _root_mapping_references(archive: KeyedArchive) -> list[plistlib.UID]:
    root = archive.top_reference("root")
    try:
        return archive.array_items(root)
    except ConcertFormatError:
        value = archive.dereference(root)
        if isinstance(value, dict) and "kGInstIDKey" in value:
            return [root]
        raise ConcertFormatError("Screen-control mapping root is not an array.") from None


def _mapping_blob_entries(
    control: str,
    blob: bytes,
    channels_by_id: dict[int, str],
) -> list[ScreenControlMapping]:
    archive = KeyedArchive.loads(blob)
    entries: list[ScreenControlMapping] = []
    for mapping_index, reference in enumerate(_root_mapping_references(archive), start=1):
        mapping = archive.dereference(reference)
        if not isinstance(mapping, dict):
            raise ConcertFormatError(f"Mapping {mapping_index} for {control!r} is not a dictionary.")

        target_instance_id = _optional_int(archive, mapping.get("kGInstIDKey"))
        saved_value = None
        if mapping.get("kHasSavedValueKey") is not False:
            saved_value = _optional_float(archive, mapping.get("kSavedValueDoubleKey"))
            if saved_value is None:
                saved_value = _optional_float(archive, mapping.get("kSavedValueFloatKey"))

        entries.append(
            ScreenControlMapping(
                control=control,
                mapping_index=mapping_index,
                mapping_class=_class_name(archive, mapping.get("$class")),
                target_instance_id=target_instance_id,
                target_channel=channels_by_id.get(target_instance_id),
                plugin_slot=_optional_int(archive, mapping.get("slot")),
                parameter_index=_optional_int(archive, mapping.get("parameterIndex_1")),
                saved_value=saved_value,
                range_low=_optional_float(archive, mapping.get("rangeLow")),
                range_high=_optional_float(archive, mapping.get("rangeHigh")),
                discrete_steps=mapping.get("kDiscreteStepsKey") is True,
                momentary=mapping.get("momentaryType") is True,
            )
        )
    return entries


def enumerate_screen_control_mappings(patch_path: Path) -> tuple[ScreenControlMapping, ...]:
    """Return each mapping directly owned by the patch's screen controls."""
    manifest = inspect_patch_manifest(Path(patch_path))
    data_path = manifest.path / "data.plist"
    try:
        with data_path.open("rb") as handle:
            data = plistlib.load(handle)
        store = data["patch"]["engineNode"]["parameterMappingMap"]["storeDict"]
    except (EOFError, KeyError, TypeError, ValueError, OverflowError, plistlib.InvalidFileException) as error:
        raise ConcertFormatError(
            f"Patch has no readable screen-control mapping store: {manifest.path}"
        ) from error
    if not isinstance(store, dict):
        raise ConcertFormatError(f"Patch screen-control mapping store is not a dictionary: {manifest.path}")

    channels_by_id = {channel.instance_id: channel.name for channel in manifest.channels}
    entries: list[ScreenControlMapping] = []
    for raw_control, blob in store.items():
        if not isinstance(raw_control, str) or not isinstance(blob, bytes):
            raise ConcertFormatError(
                "Screen-control mapping entries must contain string keys and archive data."
            )
        control = raw_control.removeprefix(IDENTITY_PREFIX)
        try:
            entries.extend(_mapping_blob_entries(control, blob, channels_by_id))
        except ConcertFormatError as error:
            raise ConcertFormatError(f"Could not enumerate mappings for {control!r}: {error}") from error
    return tuple(entries)
