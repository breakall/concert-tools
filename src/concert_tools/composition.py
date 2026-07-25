"""Vendor-neutral composition of compatible, pre-positioned patch components."""

from __future__ import annotations

import copy
import plistlib
import struct
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .archives import instance_id_values, mapping_store_targets
from .bundle import confined_child, reject_nested_destination, reject_symlink_tree
from .errors import ConcertFormatError
from .keyed_archive import ArchiveBuilder, KeyedArchive, rewrite_decoded_mapping_ids
from .manifest import PatchManifest, inspect_patch_manifest
from .ocua import channel_uuid_occurrences, iter_embedded_plists, rewrite_channel_uuid
from .patch import load_patch_data, safe_patch_filename

_IDENTITY_PREFIX = "\x01IDENTITY:"


@dataclass
class _Component:
    path: Path
    data: dict[str, Any]
    channels: list[dict[str, Any]]
    settings: list[bytes]
    replacements: dict[int, int]
    mapping_store: dict[str, bytes]
    host_channel: int
    mapping_index: int
    metadata_index: int
    mapping_archive: KeyedArchive
    metadata_archive: KeyedArchive
    control_arrays: dict[str, plistlib.UID]
    identity_keys: dict[str, plistlib.UID]
    metadata_fields: dict[str, plistlib.UID]


def _foundation_dictionary_by_string(
    archive: KeyedArchive, reference: plistlib.UID
) -> dict[str, tuple[plistlib.UID, plistlib.UID]]:
    result: dict[str, tuple[plistlib.UID, plistlib.UID]] = {}
    for key_ref, value_ref in archive.dictionary_entries(reference):
        key = archive.dereference(key_ref)
        if not isinstance(key, str):
            raise ConcertFormatError("Foundation dictionary key is not a string.")
        if key in result:
            raise ConcertFormatError(f"Foundation dictionary repeats key {key!r}.")
        result[key] = (key_ref, value_ref)
    return result


def _metadata_parts(
    archive: KeyedArchive,
) -> tuple[
    plistlib.UID,
    dict[str, plistlib.UID],
    dict[str, plistlib.UID],
    dict[str, tuple[plistlib.UID, plistlib.UID]],
]:
    root = archive.top_reference("dictionary")
    fields = _foundation_dictionary_by_string(archive, root)
    required = {"IdentityIndexLookup", "ContainsBasedOnMapping", "UUID", "kLgControlLabelLookupKey"}
    if not required.issubset(fields):
        raise ConcertFormatError("Channel metadata archive does not contain the expected fields.")

    identity_lookup = fields["IdentityIndexLookup"][1]
    identities: dict[str, plistlib.UID] = {}
    indexes: dict[str, int] = {}
    for identity_ref, index_ref in archive.dictionary_entries(identity_lookup):
        name = archive.identity_name(identity_ref)
        index = archive.dereference(index_ref)
        if name in identities or isinstance(index, bool) or not isinstance(index, int):
            raise ConcertFormatError("Identity index lookup is malformed.")
        identities[name] = identity_ref
        indexes[name] = index
    return root, identities, indexes, fields


def _mapping_arrays(archive: KeyedArchive) -> dict[int, plistlib.UID]:
    root = archive.top_reference("dictionary")
    result: dict[int, plistlib.UID] = {}
    for key_ref, value_ref in archive.dictionary_entries(root):
        index = archive.dereference(key_ref)
        if isinstance(index, bool) or not isinstance(index, int) or index in result:
            raise ConcertFormatError("Embedded mapping dictionary has invalid indexes.")
        archive.array_items(value_ref)
        result[index] = value_ref
    return result


def _embedded_blob(setting: bytes, index: int) -> bytes:
    embedded = list(iter_embedded_plists(setting))
    if index >= len(embedded):
        raise ConcertFormatError("Embedded plist index is out of range.")
    return setting[embedded[index].start : embedded[index].end]


def _find_host(settings: list[bytes], expected_ids: set[int]) -> tuple[int, int, int]:
    matches: list[tuple[int, int, int]] = []
    for channel_index, setting in enumerate(settings):
        embedded = list(iter_embedded_plists(setting))
        mapping_indexes = [
            index
            for index, item in enumerate(embedded)
            if set(instance_id_values(item.value)) == expected_ids
        ]
        for mapping_index in mapping_indexes:
            for metadata_index, item in enumerate(embedded):
                if metadata_index == mapping_index or not isinstance(item.value, dict):
                    continue
                try:
                    archive = KeyedArchive(item.value, item.value["$objects"])
                    fields = _foundation_dictionary_by_string(archive, archive.top_reference("dictionary"))
                except (KeyError, TypeError, ConcertFormatError):
                    continue
                if "IdentityIndexLookup" in fields:
                    matches.append((channel_index, mapping_index, metadata_index))
    if len(matches) != 1:
        raise ConcertFormatError(f"Expected one mapping/metadata host channel; found {len(matches)}.")
    return matches[0]


def _load_component(path: Path, assigned_ids: list[int]) -> _Component:
    reject_symlink_tree(path, kind="source patch")
    data = load_patch_data(path)
    try:
        channels = data["channels"]
        engine = data["patch"]["engineNode"]
        mapping_store = engine["parameterMappingMap"]["storeDict"]
    except (KeyError, TypeError) as error:
        raise ConcertFormatError(f"Patch has an unrecognized composition structure: {path}") from error
    if not isinstance(channels, list) or not channels or any(not isinstance(item, dict) for item in channels):
        raise ConcertFormatError(f"Patch channels are malformed: {path}")
    if not isinstance(mapping_store, dict) or any(
        not isinstance(key, str) or not isinstance(value, bytes) for key, value in mapping_store.items()
    ):
        raise ConcertFormatError(f"Patch mapping store is malformed: {path}")
    if len(assigned_ids) != len(channels):
        raise ConcertFormatError("Assigned instance-ID count does not match component channels.")

    old_ids = [channel.get("Channel_instID") for channel in channels]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in old_ids):
        raise ConcertFormatError(f"Patch channel instance IDs are malformed: {path}")
    if len(set(old_ids)) != len(old_ids):
        raise ConcertFormatError(f"Patch channel instance IDs are not unique: {path}")
    if set(mapping_store_targets(mapping_store)) != set(old_ids):
        raise ConcertFormatError(f"Patch mappings do not target exactly its channels: {path}")
    replacements = dict(zip(old_ids, assigned_ids, strict=True))

    settings: list[bytes] = []
    for channel in channels:
        setting_path = confined_child(path, channel.get("Filename"), suffix=".cst", kind="channel strip")
        if not setting_path.is_file():
            raise ConcertFormatError(f"Missing channel-strip setting: {setting_path}")
        settings.append(setting_path.read_bytes())
    host_channel, mapping_index, metadata_index = _find_host(settings, set(old_ids))

    mapping_archive = KeyedArchive.loads(_embedded_blob(settings[host_channel], mapping_index))
    metadata_archive = KeyedArchive.loads(_embedded_blob(settings[host_channel], metadata_index))
    rewrite_count = rewrite_decoded_mapping_ids(mapping_archive.value, replacements)
    if rewrite_count < 1:
        raise ConcertFormatError(f"Embedded mapping archive has no rewritable targets: {path}")
    _root, identity_keys, identity_indexes, metadata_fields = _metadata_parts(metadata_archive)
    indexed_arrays = _mapping_arrays(mapping_archive)
    if set(identity_indexes.values()) != set(indexed_arrays):
        raise ConcertFormatError("Identity lookup and embedded mapping indexes disagree.")
    control_arrays = {name: indexed_arrays[index] for name, index in identity_indexes.items()}

    outer_names = {name.removeprefix(_IDENTITY_PREFIX) for name in mapping_store}
    if outer_names != set(control_arrays):
        raise ConcertFormatError("Outer and embedded mapping controls disagree.")
    return _Component(
        path,
        data,
        channels,
        settings,
        replacements,
        mapping_store,
        host_channel,
        mapping_index,
        metadata_index,
        mapping_archive,
        metadata_archive,
        control_arrays,
        identity_keys,
        {name: value_ref for name, (_key_ref, value_ref) in metadata_fields.items()},
    )


def _merge_array_refs(
    builder: ArchiveBuilder, candidates: list[tuple[KeyedArchive, plistlib.UID]]
) -> plistlib.UID:
    items = [
        builder.import_reference(archive, item)
        for archive, array_ref in candidates
        for item in archive.array_items(array_ref)
    ]
    return builder.foundation_array(items, candidates[0])


def _merge_mapping_blob(blobs: list[tuple[bytes, dict[int, int]]]) -> bytes:
    candidates: list[tuple[KeyedArchive, plistlib.UID]] = []
    for blob, replacements in blobs:
        archive = KeyedArchive.loads(blob)
        if rewrite_decoded_mapping_ids(archive.value, replacements) < 1:
            raise ConcertFormatError("Mapping archive has no rewritable instance IDs.")
        candidates.append((archive, archive.top_reference("root")))
    builder = ArchiveBuilder()
    root = _merge_array_refs(builder, candidates)
    return builder.dumps(top_key="root", top_reference=root)


def _ui_score(value: Any) -> tuple[int, int]:
    if not isinstance(value, dict):
        return 0, 0
    contains = value.get("containsDictionary")
    store = value.get("storeDict")
    return (
        sum(item is True for item in contains.values()) if isinstance(contains, dict) else 0,
        len(store) if isinstance(store, dict) else 0,
    )


def _rewrite_nested_mapping_archives(value: Any, replacements: dict[int, int]) -> Any:
    if isinstance(value, bytes) and value.startswith(b"bplist00"):
        try:
            decoded = plistlib.loads(value)
        except (OverflowError, ValueError, plistlib.InvalidFileException):
            return value
        if rewrite_decoded_mapping_ids(decoded, replacements):
            return plistlib.dumps(decoded, fmt=plistlib.FMT_BINARY, sort_keys=False)
        return value
    if isinstance(value, dict):
        return {key: _rewrite_nested_mapping_archives(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [_rewrite_nested_mapping_archives(child, replacements) for child in value]
    return copy.deepcopy(value)


def _merge_ui(components: list[_Component]) -> tuple[dict[str, Any], dict[str, int]]:
    ui_sources: list[dict[str, Any]] = []
    for component in components:
        ui = component.data["patch"]["engineNode"].get("uiPluginDataDict")
        if not isinstance(ui, dict):
            raise ConcertFormatError(f"Patch UI data is malformed: {component.path}")
        ui_sources.append(ui)

    ordered_keys = list(dict.fromkeys(key for ui in ui_sources for key in ui))
    merged: dict[str, Any] = {}
    choices: dict[str, int] = {}
    for key in ordered_keys:
        candidates = [(index, ui[key]) for index, ui in enumerate(ui_sources) if key in ui]
        chosen_index, chosen = max(candidates, key=lambda pair: (_ui_score(pair[1]), -pair[0]))
        revised = _rewrite_nested_mapping_archives(chosen, components[chosen_index].replacements)
        if all(
            isinstance(value, dict) and value.get("overrideDict") == {key: False}
            for _index, value in candidates
        ):
            revised["overrideDict"] = {}
        merged[key] = revised
        choices[key.removeprefix(_IDENTITY_PREFIX)] = chosen_index
    return merged, choices


def _dictionary_named_entries(
    archive: KeyedArchive, reference: plistlib.UID
) -> dict[str, tuple[plistlib.UID, plistlib.UID]]:
    result: dict[str, tuple[plistlib.UID, plistlib.UID]] = {}
    for key_ref, value_ref in archive.dictionary_entries(reference):
        try:
            name = archive.identity_name(key_ref)
        except ConcertFormatError:
            raw_name = archive.dereference(key_ref)
            if not isinstance(raw_name, str):
                raise
            name = raw_name
        result.setdefault(name, (key_ref, value_ref))
    return result


def _compose_embedded_archives(
    components: list[_Component], control_order: list[str], ui_choices: dict[str, int]
) -> tuple[bytes, bytes, list[bytes]]:
    mapping_builder = ArchiveBuilder()
    mapping_entries: list[tuple[plistlib.UID, plistlib.UID]] = []
    for index, name in enumerate(control_order):
        candidates = [
            (component.mapping_archive, component.control_arrays[name])
            for component in components
            if name in component.control_arrays
        ]
        mapping_entries.append((mapping_builder.add(index), _merge_array_refs(mapping_builder, candidates)))
    first_mapping_root = components[0].mapping_archive.top_reference("dictionary")
    mapping_root = mapping_builder.foundation_dictionary(
        mapping_entries, (components[0].mapping_archive, first_mapping_root)
    )
    combined_mapping = mapping_builder.dumps(top_key="dictionary", top_reference=mapping_root)

    metadata_builder = ArchiveBuilder()
    first_metadata = components[0].metadata_archive
    first_root = first_metadata.top_reference("dictionary")
    first_fields = _foundation_dictionary_by_string(first_metadata, first_root)

    identity_entries: list[tuple[plistlib.UID, plistlib.UID]] = []
    for index, name in enumerate(control_order):
        component = next(item for item in components if name in item.identity_keys)
        identity_entries.append(
            (
                metadata_builder.import_reference(component.metadata_archive, component.identity_keys[name]),
                metadata_builder.add(index),
            )
        )
    identity_template = (first_metadata, first_fields["IdentityIndexLookup"][1])
    identity_lookup = metadata_builder.foundation_dictionary(identity_entries, identity_template)

    merged_fields: dict[str, plistlib.UID] = {"IdentityIndexLookup": identity_lookup}
    for field in ("ContainsBasedOnMapping", "kLgControlLabelLookupKey"):
        by_component = [
            _dictionary_named_entries(component.metadata_archive, component.metadata_fields[field])
            for component in components
        ]
        names = list(dict.fromkeys(name for entries in by_component for name in entries))
        entries: list[tuple[plistlib.UID, plistlib.UID]] = []
        for name in names:
            candidates = [index for index, values in enumerate(by_component) if name in values]
            chosen = ui_choices.get(name)
            if chosen not in candidates:
                chosen = candidates[-1]
            key_ref, value_ref = by_component[chosen][name]
            entries.append(
                (
                    metadata_builder.import_reference(components[chosen].metadata_archive, key_ref),
                    metadata_builder.import_reference(components[chosen].metadata_archive, value_ref),
                )
            )
        template_component = max(range(len(by_component)), key=lambda index: len(by_component[index]))
        merged_fields[field] = metadata_builder.foundation_dictionary(
            entries,
            (
                components[template_component].metadata_archive,
                components[template_component].metadata_fields[field],
            ),
        )
    merged_fields["UUID"] = metadata_builder.import_reference(
        first_metadata, components[0].metadata_fields["UUID"]
    )

    root_entries = [
        (metadata_builder.add(field), merged_fields[field])
        for field in ("IdentityIndexLookup", "ContainsBasedOnMapping", "UUID", "kLgControlLabelLookupKey")
    ]
    metadata_root = metadata_builder.foundation_dictionary(root_entries, (first_metadata, first_root))
    combined_metadata = metadata_builder.dumps(top_key="dictionary", top_reference=metadata_root)

    pruned_metadata: list[bytes] = []
    for component in components:
        builder = ArchiveBuilder()
        archive = component.metadata_archive
        root = archive.top_reference("dictionary")
        uuid_value = component.metadata_fields["UUID"]
        new_root = builder.foundation_dictionary(
            [(builder.add("UUID"), builder.import_reference(archive, uuid_value))], (archive, root)
        )
        pruned_metadata.append(builder.dumps(top_key="dictionary", top_reference=new_root))
    return combined_mapping, combined_metadata, pruned_metadata


def _empty_mapping_archive(component: _Component) -> bytes:
    builder = ArchiveBuilder()
    root = component.mapping_archive.top_reference("dictionary")
    empty = builder.foundation_dictionary([], (component.mapping_archive, root))
    return builder.dumps(top_key="dictionary", top_reference=empty)


def _replace_embedded(setting: bytes, replacements: dict[int, bytes]) -> bytes:
    embedded = list(iter_embedded_plists(setting))
    if any(index < 0 or index >= len(embedded) for index in replacements):
        raise ConcertFormatError("Embedded plist replacement index is out of range.")
    revised = setting
    for index in sorted(replacements, reverse=True):
        item = embedded[index]
        replacement = replacements[index]
        old_length = item.end - item.start
        far_overhead = struct.unpack_from("<I", revised, item.start - 28)[0] - old_length
        if far_overhead not in {20, 36}:
            raise ConcertFormatError("Embedded plist uses an unrecognized UCuA length convention.")
        revised = revised[: item.start] + replacement + revised[item.end :]
        mutable = bytearray(revised)
        struct.pack_into("<I", mutable, item.start - 4, len(replacement))
        struct.pack_into("<I", mutable, item.start - 28, len(replacement) + far_overhead)
        revised = bytes(mutable)
    return revised


def _unique_setting_name(filename: str, used: set[str]) -> str:
    path = Path(filename)
    candidate = filename
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{path.stem} {suffix}{path.suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _default_instance_ids(sources: list[Path]) -> list[int]:
    manifests = [inspect_patch_manifest(path) for path in sources]
    old_ids = [channel.instance_id for manifest in manifests for channel in manifest.channels]
    next_id = ((max(old_ids) // 4) + 1) * 4
    return [next_id + index * 4 for index in range(len(old_ids))]


def compose_patch(
    sources: list[Path],
    destination: Path,
    *,
    name: str,
    instance_ids: list[int] | None = None,
    selected: bool = False,
) -> PatchManifest:
    """Combine compatible pre-positioned patches into one standalone patch bundle."""
    sources = [Path(path) for path in sources]
    destination = Path(destination)
    if not sources:
        raise ValueError("At least one source patch is required.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Combined patch name must be a non-empty string.")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite {destination}")
    if destination.is_symlink():
        raise ConcertFormatError(f"Refusing a symlinked destination: {destination}")
    if destination.suffix.casefold() != ".patch":
        raise ValueError("Combined patch destination must end in .patch.")
    for source in sources:
        reject_nested_destination(source, destination)

    assigned = list(instance_ids) if instance_ids is not None else _default_instance_ids(sources)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in assigned):
        raise ValueError("Assigned instance IDs must be non-negative integers.")
    if len(set(assigned)) != len(assigned):
        raise ValueError("Assigned instance IDs must be unique.")
    channel_counts = [len(inspect_patch_manifest(path).channels) for path in sources]
    if sum(channel_counts) != len(assigned):
        raise ValueError("Assigned instance-ID count does not match all source channels.")

    components: list[_Component] = []
    offset = 0
    for source, count in zip(sources, channel_counts, strict=True):
        components.append(_load_component(source, assigned[offset : offset + count]))
        offset += count

    ui_data, ui_choices = _merge_ui(components)
    control_order = list(dict.fromkeys(name for component in components for name in component.control_arrays))
    outer_store: dict[str, bytes] = {}
    for control in control_order:
        full_name = f"{_IDENTITY_PREFIX}{control}"
        blobs = [
            (component.mapping_store[full_name], component.replacements)
            for component in components
            if full_name in component.mapping_store
        ]
        outer_store[full_name] = _merge_mapping_blob(blobs)
    combined_mapping, combined_metadata, pruned_metadata = _compose_embedded_archives(
        components, control_order, ui_choices
    )

    output_data = copy.deepcopy(components[0].data)
    patch = output_data["patch"]
    engine = patch["engineNode"]
    patch.pop("patchPath", None)
    patch["selected"] = selected
    engine.pop("imageNameForSetFolder", None)
    engine["name"] = name.strip()
    engine["hasProgramChange"] = True
    engine["uiPluginDataDict"] = ui_data
    engine["parameterMappingMap"]["storeDict"] = outer_store

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="concert-tools-compose-", dir=destination.parent) as raw_temp:
        temporary = Path(raw_temp) / safe_patch_filename(name)
        temporary.mkdir()
        output_channels: list[dict[str, Any]] = []
        used_filenames: set[str] = set()
        first_icon = patch.get("iconID")
        global_host_written = False
        for component_index, component in enumerate(components):
            for channel_index, (channel, setting) in enumerate(
                zip(component.channels, component.settings, strict=True)
            ):
                revised_channel = copy.deepcopy(channel)
                old_filename = channel.get("Filename")
                confined_child(component.path, old_filename, suffix=".cst", kind="channel strip")
                filename = _unique_setting_name(old_filename, used_filenames)
                is_component_host = channel_index == component.host_channel
                if is_component_host:
                    if not global_host_written:
                        setting = _replace_embedded(
                            setting,
                            {
                                component.mapping_index: combined_mapping,
                                component.metadata_index: combined_metadata,
                            },
                        )
                        global_host_written = True
                    else:
                        setting = _replace_embedded(
                            setting,
                            {
                                component.mapping_index: _empty_mapping_archive(component),
                                component.metadata_index: pruned_metadata[component_index],
                            },
                        )
                old_uuid = channel.get("UUID")
                new_uuid = str(uuid.uuid4()).upper()
                setting = rewrite_channel_uuid(setting, old_uuid, new_uuid)
                revised_channel["Filename"] = filename
                revised_channel["UUID"] = new_uuid
                revised_channel["Channel_instID"] = component.replacements[channel["Channel_instID"]]
                if "Custom_icon" in revised_channel and first_icon is not None:
                    revised_channel["Custom_icon"] = first_icon
                (temporary / filename).write_bytes(setting)
                output_channels.append(revised_channel)
        if not global_host_written:
            raise ConcertFormatError("No output mapping host was written.")
        output_data["channels"] = output_channels
        with (temporary / "data.plist").open("wb") as handle:
            plistlib.dump(output_data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

        if set(mapping_store_targets(outer_store)) != set(assigned):
            raise ConcertFormatError("Combined outer mappings do not target every output channel.")
        mapping_hosts = 0
        for channel in output_channels:
            setting = (temporary / channel["Filename"]).read_bytes()
            if channel_uuid_occurrences(setting, channel["UUID"]) != 1:
                raise ConcertFormatError("Combined channel UUID failed structural validation.")
            matches = [instance_id_values(item.value) for item in iter_embedded_plists(setting)]
            matches = [values for values in matches if values]
            if matches:
                mapping_hosts += 1
                if len(matches) != 1 or set(matches[0]) != set(assigned):
                    raise ConcertFormatError("Combined embedded mappings are incomplete.")
        if mapping_hosts != 1:
            raise ConcertFormatError(f"Expected one combined mapping host; found {mapping_hosts}.")
        temporary.rename(destination)
    return inspect_patch_manifest(destination)
