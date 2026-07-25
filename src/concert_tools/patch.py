"""Patch-level inspection and safe cloning."""

from __future__ import annotations

import plistlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .archives import (
    mapping_store_targets,
    read_attributed_plain_text,
    rewrite_attributed_plain_text,
    rewrite_mapping_store,
)
from .bundle import confined_child, reject_symlink_tree
from .errors import ConcertFormatError
from .ocua import (
    find_mapping_targets,
    read_sized_layout_text,
    rewrite_channel_uuid,
    rewrite_mapping_targets,
    rewrite_sized_layout_text,
)


@dataclass(frozen=True)
class PatchInfo:
    path: Path
    name: str
    channels: tuple[dict, ...]
    error: str | None = None


def _text_blob(ui_data: dict, control: str) -> bytes:
    try:
        blob = ui_data[control]["storeDict"]["text"]
    except (KeyError, TypeError) as error:
        raise ConcertFormatError(f"Patch does not contain text control {control!r}.") from error
    if not isinstance(blob, bytes):
        raise ConcertFormatError(f"Text control {control!r} has an unrecognized value.")
    return blob


def load_patch_data(path: Path) -> dict:
    try:
        with (path / "data.plist").open("rb") as handle:
            return plistlib.load(handle)
    except (FileNotFoundError, ValueError, OverflowError, plistlib.InvalidFileException) as error:
        raise ConcertFormatError(f"Not a recognized patch bundle: {path}") from error


def inspect_patch(path: Path) -> PatchInfo:
    data = load_patch_data(path)
    try:
        engine = data["patch"]["engineNode"]
        channels = tuple(data["channels"])
    except (KeyError, TypeError) as error:
        raise ConcertFormatError(f"Patch has an unrecognized structure: {path}") from error
    return PatchInfo(
        path=path,
        name=engine.get("name", path.stem),
        channels=channels,
    )


def read_patch_text(path: Path, control: str) -> str:
    """Read one caller-selected attributed-text screen control."""
    data = load_patch_data(path)
    try:
        ui_data = data["patch"]["engineNode"]["uiPluginDataDict"]
    except (KeyError, TypeError) as error:
        raise ConcertFormatError(f"Patch has an unrecognized UI structure: {path}") from error
    if not isinstance(ui_data, dict):
        raise ConcertFormatError(f"Patch has an unrecognized UI structure: {path}")
    return read_attributed_plain_text(_text_blob(ui_data, control))


def rewrite_patch_text(
    path: Path,
    control: str,
    value: str,
    *,
    embedded_font_size: float | None = None,
) -> None:
    """Rewrite one caller-selected text control and its optional embedded layout copy."""
    data = load_patch_data(path)
    try:
        ui_data = data["patch"]["engineNode"]["uiPluginDataDict"]
        channels = data["channels"]
    except (KeyError, TypeError) as error:
        raise ConcertFormatError(f"Patch has an unrecognized structure: {path}") from error
    if not isinstance(ui_data, dict) or not isinstance(channels, list):
        raise ConcertFormatError(f"Patch has an unrecognized structure: {path}")
    old_value = read_attributed_plain_text(_text_blob(ui_data, control))

    revised_settings: dict[Path, bytes] = {}
    if embedded_font_size is not None:
        for channel in channels:
            if not isinstance(channel, dict):
                raise ConcertFormatError("Patch channels must be dictionary objects.")
            setting_path = confined_child(
                path, channel.get("Filename"), suffix=".cst", kind="channel-strip setting"
            )
            setting = setting_path.read_bytes()
            texts = read_sized_layout_text(setting, (embedded_font_size,))
            if texts:
                if texts[embedded_font_size] != old_value:
                    raise ConcertFormatError("Outer and embedded text values disagree.")
                revised_settings[setting_path] = rewrite_sized_layout_text(
                    setting,
                    {embedded_font_size: (old_value, value)},
                )
        if len(revised_settings) != 1:
            raise ConcertFormatError(
                f"Expected one embedded layout copy for font size {embedded_font_size}; "
                f"found {len(revised_settings)}."
            )

    with tempfile.TemporaryDirectory(prefix="concert-tools-text-") as raw_temp:
        ui_data[control]["storeDict"]["text"] = rewrite_attributed_plain_text(
            _text_blob(ui_data, control), value, Path(raw_temp)
        )
    for setting_path, setting in revised_settings.items():
        setting_path.write_bytes(setting)
    with (path / "data.plist").open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def safe_patch_filename(name: str) -> str:
    cleaned = "".join("-" if char in "/:\\" else char for char in name).strip().rstrip(".")
    if not cleaned:
        raise ValueError("Patch name becomes empty after filename sanitization.")
    return f"{cleaned}.patch"


def clone_patch(
    source: Path,
    destination: Path,
    *,
    name: str,
    instance_ids: list[int],
    selected: bool = False,
) -> PatchInfo:
    """Clone a patch and rewrite its graph identities without template policy."""
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    reject_symlink_tree(source, kind="source patch")
    try:
        shutil.copytree(source, destination)
        data = load_patch_data(destination)
        patch = data["patch"]
        engine = patch["engineNode"]
        channels = data["channels"]
        if not isinstance(channels, list) or any(not isinstance(channel, dict) for channel in channels):
            raise ConcertFormatError("Patch channels must be an array of dictionary objects.")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in instance_ids):
            raise ConcertFormatError("Assigned channel instance IDs must be integers.")
        if len(set(instance_ids)) != len(instance_ids):
            raise ConcertFormatError("Assigned channel instance IDs are not unique.")
        if len(channels) != len(instance_ids):
            raise ConcertFormatError(
                f"Patch contains {len(channels)} channels, but {len(instance_ids)} instance IDs were supplied."
            )

        replacements = {
            channel["Channel_instID"]: assigned
            for channel, assigned in zip(channels, instance_ids, strict=True)
        }
        if len(replacements) != len(channels):
            raise ConcertFormatError("Template channel instance IDs are not unique.")
        try:
            mapping_store = engine["parameterMappingMap"]["storeDict"]
        except (KeyError, TypeError) as error:
            raise ConcertFormatError("Patch does not contain a parameter mapping store.") from error
        old_targets = mapping_store_targets(mapping_store)
        if not old_targets or set(old_targets) != set(replacements):
            raise ConcertFormatError(
                f"Outer mappings target {sorted(set(old_targets))}; expected {sorted(replacements)}."
            )
        if not rewrite_mapping_store(mapping_store, replacements):
            raise ConcertFormatError("No per-control instance-ID mappings were found.")
        if set(mapping_store_targets(mapping_store)) != set(replacements.values()):
            raise ConcertFormatError("Outer mappings failed validation after rewriting.")

        mapping_settings = 0
        for channel, assigned in zip(channels, instance_ids, strict=True):
            setting_path = confined_child(
                destination, channel.get("Filename"), suffix=".cst", kind="channel-strip setting"
            )
            if not setting_path.is_file():
                raise ConcertFormatError(f"Missing channel-strip setting {setting_path.name!r}.")
            setting = setting_path.read_bytes()
            targets = find_mapping_targets(setting)
            if targets is not None:
                if set(targets) != set(replacements):
                    raise ConcertFormatError(
                        f"Embedded mappings target {sorted(set(targets))}; expected {sorted(replacements)}."
                    )
                setting = rewrite_mapping_targets(setting, replacements)
                mapping_settings += 1
            old_uuid = channel["UUID"]
            new_uuid = str(uuid.uuid4()).upper()
            setting_path.write_bytes(rewrite_channel_uuid(setting, old_uuid, new_uuid))
            channel["UUID"] = new_uuid
            channel["Channel_instID"] = assigned
        if mapping_settings != 1:
            raise ConcertFormatError(f"Expected one embedded mapping host; found {mapping_settings}.")

        patch["selected"] = selected
        engine["name"] = name
        with (destination / "data.plist").open("wb") as handle:
            plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return inspect_patch(destination)
