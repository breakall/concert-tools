"""Structural validation for concert and patch identities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .archives import mapping_store_targets
from .bundle import confined_child
from .concert import load_concert_data
from .errors import ConcertFormatError
from .ocua import channel_uuid_occurrences, find_mapping_targets
from .patch import load_patch_data


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    location: str
    message: str


def validate_concert(path: Path) -> list[ValidationIssue]:
    """Return all detectable identity, mapping, and display inconsistencies."""
    issues: list[ValidationIssue] = []
    try:
        root, root_data = load_concert_data(path)
    except ConcertFormatError as error:
        return [ValidationIssue("error", str(path), str(error))]
    seen_uuids: dict[str, str] = {}
    seen_ids: dict[int, str] = {}
    for node in root_data.get("nodes", []):
        try:
            patch_path = confined_child(root, node, suffix=".patch", kind="patch node")
        except ConcertFormatError as error:
            issues.append(ValidationIssue("error", str(node), str(error)))
            continue
        try:
            data = load_patch_data(patch_path)
            engine = data["patch"]["engineNode"]
            channels = data["channels"]
        except (ConcertFormatError, KeyError, TypeError) as error:
            issues.append(ValidationIssue("error", node, f"Unreadable patch structure: {error}"))
            continue
        if not isinstance(channels, list):
            issues.append(ValidationIssue("error", node, "Patch channels value is not an array."))
            continue
        ids: set[int] = set()
        setting_files: list[tuple[Path, bytes]] = []
        for channel_index, channel in enumerate(channels):
            if not isinstance(channel, dict):
                issues.append(
                    ValidationIssue("error", node, f"Channel {channel_index + 1} is not a dictionary object.")
                )
                continue
            location = f"{node}/{channel.get('Filename', '?')}"
            channel_uuid = channel.get("UUID")
            instance_id = channel.get("Channel_instID")
            if not isinstance(channel_uuid, str):
                issues.append(
                    ValidationIssue("error", location, "Channel UUID is missing or is not a string.")
                )
            elif channel_uuid in seen_uuids:
                issues.append(
                    ValidationIssue("error", location, f"UUID is also used by {seen_uuids[channel_uuid]}.")
                )
            else:
                seen_uuids[channel_uuid] = location
            if not isinstance(instance_id, int) or isinstance(instance_id, bool):
                issues.append(
                    ValidationIssue("error", location, "Channel instance ID is missing or is not an integer.")
                )
            elif instance_id in seen_ids:
                issues.append(
                    ValidationIssue(
                        "error", location, f"Instance ID is also used by {seen_ids[instance_id]}."
                    )
                )
            else:
                seen_ids[instance_id] = location
                ids.add(instance_id)
            try:
                setting_path = confined_child(
                    patch_path, channel.get("Filename"), suffix=".cst", kind="channel-strip setting"
                )
            except ConcertFormatError as error:
                issues.append(ValidationIssue("error", location, str(error)))
                continue
            if not setting_path.is_file():
                issues.append(ValidationIssue("error", location, "Channel-strip setting is missing."))
                continue
            setting = setting_path.read_bytes()
            setting_files.append((setting_path, setting))
            try:
                count = channel_uuid_occurrences(setting, channel_uuid)
            except (ConcertFormatError, ValueError, AttributeError) as error:
                count = 0
                issues.append(
                    ValidationIssue("error", location, f"Could not validate channel-strip container: {error}")
                )
            if count != 1:
                issues.append(
                    ValidationIssue(
                        "error", location, f"Structurally identified channel UUID occurs {count} times."
                    )
                )

        mapping_store = engine.get("parameterMappingMap", {}).get("storeDict")
        if not isinstance(mapping_store, dict):
            outer_targets: list[int] = []
        else:
            try:
                outer_targets = mapping_store_targets(mapping_store)
            except ConcertFormatError as error:
                outer_targets = []
                issues.append(ValidationIssue("error", node, str(error)))
        if outer_targets and set(outer_targets) != ids:
            issues.append(
                ValidationIssue(
                    "error",
                    node,
                    f"Outer mappings target {sorted(set(outer_targets))}, channels are {sorted(ids)}.",
                )
            )

        embedded_mapping_settings: list[tuple[Path, list[int]]] = []
        for setting_path, setting in setting_files:
            try:
                targets = find_mapping_targets(setting)
            except ConcertFormatError as error:
                issues.append(ValidationIssue("error", str(setting_path.relative_to(root)), str(error)))
            else:
                if targets is None:
                    continue
                embedded_mapping_settings.append((setting_path, targets))
        if outer_targets and len(embedded_mapping_settings) != 1:
            issues.append(
                ValidationIssue(
                    "error",
                    node,
                    f"Expected one embedded mapping host; found {len(embedded_mapping_settings)}.",
                )
            )
        if len(embedded_mapping_settings) == 1:
            setting_path, targets = embedded_mapping_settings[0]
            if set(targets) != ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        str(setting_path.relative_to(root)),
                        f"Embedded mappings target {sorted(set(targets))}, channels are {sorted(ids)}.",
                    )
                )
    return issues
