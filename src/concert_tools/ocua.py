"""Focused transformations for the undocumented OCuA/UCuA channel-strip format."""

from __future__ import annotations

import plistlib
import struct
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .archives import instance_id_values
from .errors import ConcertFormatError


@dataclass(frozen=True)
class EmbeddedPlist:
    start: int
    end: int
    value: Any


def _ucua_blocks(data: bytes) -> tuple[tuple[int, int], ...] | None:
    """Return validated UCuA block boundaries for a complete OCuA container.

    A few unit-level helpers use bare plist/chunk fragments, so non-OCuA data returns
    ``None`` and can use the legacy fragment scanner. A value that claims to be an
    OCuA container is instead validated strictly and fails closed.
    """
    if not data.startswith(b"OCuA"):
        return None
    if len(data) < 36:
        raise ConcertFormatError("OCuA header is truncated.")
    header_size = struct.unpack_from("<I", data, 28)[0] + 36
    if header_size < 36 or header_size > len(data):
        raise ConcertFormatError("OCuA header length is invalid.")
    if header_size == len(data) or data[header_size : header_size + 4] != b"UCuA":
        raise ConcertFormatError("OCuA container does not begin with a UCuA block.")

    blocks: list[tuple[int, int]] = []
    position = header_size
    while data[position : position + 4] == b"UCuA":
        if position + 32 > len(data):
            raise ConcertFormatError("UCuA block header is truncated.")
        block_size = struct.unpack_from("<I", data, position + 28)[0] + 36
        end = position + block_size
        if block_size <= 36 or end > len(data):
            raise ConcertFormatError("UCuA block length is invalid.")
        blocks.append((position, end))
        position = end
    return tuple(blocks)


def _iter_fragment_plists(data: bytes) -> Iterator[EmbeddedPlist]:
    """Parse the historical bare chunks used by focused unit tests."""
    search_from = 0
    while True:
        start = data.find(b"bplist00", search_from)
        if start < 0:
            return
        search_from = start + 8
        marker_search = start + 8
        while True:
            next_chunk = data.find(b"UCuA", marker_search)
            if next_chunk < 16:
                break
            end = next_chunk - 16
            if end <= start + 8:
                marker_search = next_chunk + 1
                continue
            try:
                value = plistlib.loads(data[start:end])
            except (plistlib.InvalidFileException, ValueError, OverflowError):
                marker_search = next_chunk + 1
                continue
            yield EmbeddedPlist(start, end, value)
            break


def iter_embedded_plists(data: bytes) -> Iterator[EmbeddedPlist]:
    """Yield length-delimited binary plists from validated UCuA blocks."""
    blocks = _ucua_blocks(data)
    if blocks is None:
        yield from _iter_fragment_plists(data)
        return

    for block_start, block_end in blocks:
        # The MainStage system archives we edit use a 56-byte UCuA prefix and a
        # 16-byte suffix. Both the payload and block lengths sit immediately
        # before the plist, which lets us reject plugin-internal bplist strings.
        start = block_start + 56
        if block_end <= start or data[start : start + 8] != b"bplist00":
            continue
        near_length = struct.unpack_from("<I", data, start - 4)[0]
        far_length = struct.unpack_from("<I", data, start - 28)[0]
        if block_end == start + near_length + 16 and far_length == near_length + 36:
            end = block_end - 16
        elif block_end == start + near_length and far_length == near_length + 20:
            # Older exported libraries omit the 16-byte post-plist identity.
            end = block_end
        else:
            raise ConcertFormatError("Embedded plist payload length does not match its UCuA block.")
        try:
            value = plistlib.loads(data[start:end])
        except (plistlib.InvalidFileException, ValueError, OverflowError) as error:
            raise ConcertFormatError("Embedded UCuA binary plist is invalid.") from error
        yield EmbeddedPlist(start, end, value)


def _channel_uuid_values(value: Any) -> list[bytes]:
    values: list[bytes] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "UUIDBytes" and isinstance(child, bytes):
                values.append(child)
            else:
                values.extend(_channel_uuid_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_channel_uuid_values(child))
    return values


def channel_uuid_occurrences(data: bytes, value: str | uuid.UUID) -> int:
    """Count structurally identified channel UUID values in UCuA archives."""
    expected = uuid.UUID(str(value)).bytes
    return sum(
        candidate == expected
        for embedded in iter_embedded_plists(data)
        for candidate in _channel_uuid_values(embedded.value)
    )


def rewrite_channel_uuid(data: bytes, old: str | uuid.UUID, new: str | uuid.UUID) -> bytes:
    """Replace one structurally identified channel UUID without reserializing its archive."""
    old_bytes = uuid.UUID(str(old)).bytes
    new_bytes = uuid.UUID(str(new)).bytes
    structural_count = channel_uuid_occurrences(data, old)
    raw_count = data.count(old_bytes)
    if structural_count != 1 or raw_count != 1:
        raise ConcertFormatError(
            f"Expected one structurally identified channel UUID; found {structural_count} "
            f"({raw_count} raw byte occurrences)."
        )
    return data.replace(old_bytes, new_bytes, 1)


def _layout_text_indexes(objects: list[Any], font_sizes: tuple[float, ...]) -> dict[float, int] | None:
    sized_text: dict[float, list[int]] = {size: [] for size in font_sizes}
    for candidate in objects:
        if not isinstance(candidate, dict):
            continue
        string_ref = candidate.get("NSString")
        attributes_ref = candidate.get("NSAttributes")
        if not isinstance(string_ref, plistlib.UID) or not isinstance(attributes_ref, plistlib.UID):
            continue
        if attributes_ref.data >= len(objects) or string_ref.data >= len(objects):
            raise ConcertFormatError("Layout archive contains an out-of-range object reference.")
        attributes = objects[attributes_ref.data]
        if not isinstance(attributes, dict):
            continue
        for reference in attributes.get("NS.objects", []):
            if not isinstance(reference, plistlib.UID):
                continue
            if reference.data >= len(objects):
                raise ConcertFormatError("Layout archive contains an out-of-range font reference.")
            font = objects[reference.data]
            if isinstance(font, dict) and font.get("NSSize") in sized_text:
                sized_text[font["NSSize"]].append(string_ref.data)
    if sized_text and all(len(indexes) == 1 for indexes in sized_text.values()):
        return {size: indexes[0] for size, indexes in sized_text.items()}
    return None


def read_sized_layout_text(data: bytes, font_sizes: tuple[float, ...]) -> dict[float, str]:
    """Read one embedded layout archive using caller-selected font sizes.

    An empty result means the selected sizes are not present in this channel-strip
    setting. Multiple matching archives fail closed.
    """
    if not font_sizes or len(set(font_sizes)) != len(font_sizes):
        raise ValueError("Font sizes must be a non-empty, unique tuple.")
    matches: list[dict[float, str]] = []
    for embedded in iter_embedded_plists(data):
        if not isinstance(embedded.value, dict) or not isinstance(embedded.value.get("$objects"), list):
            continue
        objects = embedded.value["$objects"]
        indexes = _layout_text_indexes(objects, font_sizes)
        if indexes:
            values = {size: objects[index] for size, index in indexes.items()}
            if all(isinstance(value, str) for value in values.values()):
                matches.append(values)
    if len(matches) > 1:
        raise ConcertFormatError(f"Expected at most one matching layout archive; found {len(matches)}.")
    return matches[0] if matches else {}


def rewrite_sized_layout_text(
    data: bytes,
    replacements: dict[float, tuple[str, str]],
) -> bytes:
    """Rewrite caller-selected strings in one embedded layout archive."""
    if not replacements:
        raise ValueError("At least one sized-text replacement is required.")
    font_sizes = tuple(replacements)
    matches: list[tuple[EmbeddedPlist, dict[float, int]]] = []
    for embedded in iter_embedded_plists(data):
        if not isinstance(embedded.value, dict) or not isinstance(embedded.value.get("$objects"), list):
            continue
        objects = embedded.value["$objects"]
        indexes = _layout_text_indexes(objects, font_sizes)
        if indexes and all(objects[indexes[size]] == old for size, (old, _new) in replacements.items()):
            matches.append((embedded, indexes))
    if len(matches) != 1:
        raise ConcertFormatError(f"Expected one matching layout archive; found {len(matches)}.")

    embedded, indexes = matches[0]
    for size, (_old, new) in replacements.items():
        embedded.value["$objects"][indexes[size]] = new
    replacement = plistlib.dumps(embedded.value, fmt=plistlib.FMT_BINARY, sort_keys=False)
    old_length = embedded.end - embedded.start
    if struct.unpack_from("<I", data, embedded.start - 4)[0] != old_length:
        raise ConcertFormatError("Embedded plist payload length was not recognized.")
    if struct.unpack_from("<I", data, embedded.start - 28)[0] != old_length + 36:
        raise ConcertFormatError("Embedded plist chunk length was not recognized.")
    revised = bytearray(data[: embedded.start] + replacement + data[embedded.end :])
    struct.pack_into("<I", revised, embedded.start - 4, len(replacement))
    struct.pack_into("<I", revised, embedded.start - 28, len(replacement) + 36)
    return bytes(revised)


@dataclass(frozen=True)
class _BPlistTable:
    offsets: tuple[int, ...]
    reference_size: int
    offset_table: int


def _bplist_table(archive: bytes) -> _BPlistTable:
    if len(archive) < 40 or not archive.startswith(b"bplist00"):
        raise ConcertFormatError("Invalid embedded binary plist.")
    trailer = archive[-32:]
    offset_size = trailer[6]
    reference_size = trailer[7]
    object_count = int.from_bytes(trailer[8:16], "big")
    offset_table = int.from_bytes(trailer[24:32], "big")
    if (
        not offset_size
        or not reference_size
        or object_count > len(archive)
        or offset_table > len(archive) - 32
        or offset_table + object_count * offset_size > len(archive) - 32
    ):
        raise ConcertFormatError("Invalid binary-plist offset table.")
    offsets = tuple(
        int.from_bytes(
            archive[offset_table + index * offset_size : offset_table + (index + 1) * offset_size], "big"
        )
        for index in range(object_count)
    )
    if any(offset < 8 or offset >= offset_table for offset in offsets):
        raise ConcertFormatError("Binary-plist object offset is outside the object table.")
    return _BPlistTable(offsets, reference_size, offset_table)


def _object_header(archive: bytes, offset: int, limit: int) -> tuple[int, int, int]:
    marker = archive[offset]
    object_type, info = marker >> 4, marker & 0x0F
    payload = offset + 1
    variable_length_types = {4, 5, 6, 7, 10, 11, 12, 13}
    if info != 0x0F or object_type not in variable_length_types:
        return object_type, info, payload
    if payload >= limit or archive[payload] >> 4 != 1:
        raise ConcertFormatError("Binary-plist extended object length is invalid.")
    integer_size = 1 << (archive[payload] & 0x0F)
    end = payload + 1 + integer_size
    if end > limit:
        raise ConcertFormatError("Binary-plist extended object length is truncated.")
    return object_type, int.from_bytes(archive[payload + 1 : end], "big"), end


def _string_object(archive: bytes, table: _BPlistTable, object_id: int) -> str | None:
    if object_id >= len(table.offsets):
        raise ConcertFormatError("Binary-plist reference points outside the object table.")
    object_type, length, payload = _object_header(archive, table.offsets[object_id], table.offset_table)
    encodings = {5: ("ascii", 1), 6: ("utf-16-be", 2), 7: ("utf-8", 1)}
    if object_type not in encodings:
        return None
    encoding, width = encodings[object_type]
    end = payload + length * width
    if end > table.offset_table:
        raise ConcertFormatError("Binary-plist string object is truncated.")
    try:
        return archive[payload:end].decode(encoding)
    except UnicodeDecodeError as error:
        raise ConcertFormatError("Binary-plist string object is invalid.") from error


def _object_references(archive: bytes, table: _BPlistTable) -> dict[int, list[tuple[str, str | None]]]:
    """Map object IDs to every container reference that targets them."""
    result: dict[int, list[tuple[str, str | None]]] = {}

    def read_references(payload: int, count: int) -> list[int]:
        end = payload + count * table.reference_size
        if end > table.offset_table:
            raise ConcertFormatError("Binary-plist reference collection is truncated.")
        references = []
        for index in range(count):
            start = payload + index * table.reference_size
            reference = int.from_bytes(archive[start : start + table.reference_size], "big")
            if reference >= len(table.offsets):
                raise ConcertFormatError("Binary-plist reference points outside the object table.")
            references.append(reference)
        return references

    for object_id, offset in enumerate(table.offsets):
        object_type, count, payload = _object_header(archive, offset, table.offset_table)
        if object_type in {10, 11, 12}:  # array, ordered set, set
            for target in read_references(payload, count):
                result.setdefault(target, []).append(("collection", None))
        elif object_type == 13:  # dictionary: keys followed by values
            keys = read_references(payload, count)
            values = read_references(payload + count * table.reference_size, count)
            for target in keys:
                result.setdefault(target, []).append(("dictionary-key", None))
            for key_id, target in zip(keys, values, strict=True):
                result.setdefault(target, []).append(
                    ("dictionary-value", _string_object(archive, table, key_id))
                )
    return result


def rewrite_bplist_integer_objects(archive: bytes, replacements: dict[int, int]) -> bytes:
    """Change mapping integers only when every object reference is mapping-owned."""
    if any(
        not isinstance(old, int) or isinstance(old, bool) or not isinstance(new, int) or isinstance(new, bool)
        for old, new in replacements.items()
    ):
        raise ConcertFormatError("Instance-ID replacements must contain integers.")
    table = _bplist_table(archive)
    references = _object_references(archive, table)
    revised = bytearray(archive)
    found = {old: 0 for old in replacements}
    for object_id, offset in enumerate(table.offsets):
        marker = archive[offset]
        if marker >> 4 != 1:
            continue
        size = 1 << (marker & 0x0F)
        if offset + 1 + size > table.offset_table:
            raise ConcertFormatError("Binary-plist integer object is truncated.")
        old_value = int.from_bytes(archive[offset + 1 : offset + 1 + size], "big")
        if old_value not in replacements:
            continue
        owners = references.get(object_id, [])
        if not owners or any(owner != ("dictionary-value", "kGInstIDKey") for owner in owners):
            raise ConcertFormatError(
                f"Instance-ID integer {old_value} is shared with a non-mapping object; refusing an unsafe edit."
            )
        new_value = replacements[old_value]
        if new_value < 0 or new_value >= 1 << (size * 8):
            raise ConcertFormatError(f"Instance ID {new_value} does not fit the existing integer object.")
        revised[offset + 1 : offset + 1 + size] = new_value.to_bytes(size, "big")
        found[old_value] += 1
    if any(count < 1 for count in found.values()):
        raise ConcertFormatError(f"Could not locate every instance-ID object: {found}")
    return bytes(revised)


def find_mapping_targets(data: bytes) -> list[int] | None:
    """Return the sole embedded mapping group, or ``None`` when absent."""
    matches = [instance_id_values(item.value) for item in iter_embedded_plists(data)]
    matches = [values for values in matches if values]
    if len(matches) > 1:
        raise ConcertFormatError(f"Expected at most one embedded mapping archive; found {len(matches)}.")
    return matches[0] if matches else None


def read_mapping_targets(data: bytes) -> list[int]:
    targets = find_mapping_targets(data)
    if targets is None:
        raise ConcertFormatError("Expected one embedded mapping archive; found 0.")
    return targets


def rewrite_mapping_targets(data: bytes, replacements: dict[int, int]) -> bytes:
    matches = [
        (item, instance_id_values(item.value))
        for item in iter_embedded_plists(data)
        if instance_id_values(item.value)
    ]
    if len(matches) != 1:
        raise ConcertFormatError(f"Expected one embedded mapping archive; found {len(matches)}.")
    embedded, old_values = matches[0]
    if set(old_values) != set(replacements):
        raise ConcertFormatError(
            f"Mapping archive targets {sorted(set(old_values))}; expected {sorted(replacements)}."
        )
    replacement = rewrite_bplist_integer_objects(data[embedded.start : embedded.end], replacements)
    if set(instance_id_values(plistlib.loads(replacement))) != set(replacements.values()):
        raise ConcertFormatError("Mapping archive failed validation after rewriting.")
    return data[: embedded.start] + replacement + data[embedded.end :]
