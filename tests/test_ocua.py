import plistlib
import struct
import uuid

import pytest

from concert_tools.errors import ConcertFormatError
from concert_tools.ocua import (
    channel_uuid_occurrences,
    read_mapping_targets,
    read_sized_layout_text,
    rewrite_bplist_integer_objects,
    rewrite_channel_uuid,
    rewrite_mapping_targets,
    rewrite_sized_layout_text,
)


def keyed_archive(objects: list) -> bytes:
    return plistlib.dumps(
        {"$archiver": "NSKeyedArchiver", "$version": 100000, "$objects": objects, "$top": {}},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )


def chunk(archive: bytes) -> bytes:
    header = bytearray(28)
    struct.pack_into("<I", header, 0, len(archive) + 36)
    struct.pack_into("<I", header, 24, len(archive))
    return bytes(header) + archive + bytes(16) + b"UCuA"


def layout_archive(key: str, note: str) -> bytes:
    objects = [
        "$null",
        {"NSString": plistlib.UID(2), "NSAttributes": plistlib.UID(3)},
        key,
        {"NS.objects": [plistlib.UID(4)]},
        {"NSSize": 96.0},
        {"NSString": plistlib.UID(6), "NSAttributes": plistlib.UID(7)},
        note,
        {"NS.objects": [plistlib.UID(8)]},
        {"NSSize": 48.0},
    ]
    return keyed_archive(objects)


def mapping_archive() -> bytes:
    return keyed_archive(["$null", {"kGInstIDKey": 136}, {"kGInstIDKey": 140}, {"kGInstIDKey": 136}])


def test_integer_object_patch_preserves_archive_size() -> None:
    original = mapping_archive()
    revised = rewrite_bplist_integer_objects(original, {136: 156, 140: 160})
    assert len(revised) == len(original)
    assert read_values(revised) == [156, 160, 156]


def read_values(blob: bytes) -> list[int]:
    return [item["kGInstIDKey"] for item in plistlib.loads(blob)["$objects"] if isinstance(item, dict)]


def test_combined_mapping_and_layout_rewrite() -> None:
    data = chunk(mapping_archive()) + chunk(layout_archive("D", "Big intro"))
    assert read_mapping_targets(data) == [136, 140, 136]
    assert read_sized_layout_text(data, (96.0, 48.0)) == {96.0: "D", 48.0: "Big intro"}
    data = rewrite_mapping_targets(data, {136: 156, 140: 160})
    data = rewrite_sized_layout_text(data, {96.0: ("D", "F"), 48.0: ("Big intro", "")})
    assert read_mapping_targets(data) == [156, 160, 156]
    assert read_sized_layout_text(data, (96.0, 48.0)) == {96.0: "F", 48.0: ""}


def test_uuid_rewrite_requires_one_match() -> None:
    old = uuid.uuid4()
    new = uuid.uuid4()
    revised = rewrite_channel_uuid(
        structural_container(keyed_archive(["$null", {"UUIDBytes": old.bytes}])), old, new
    )
    assert old.bytes not in revised
    assert channel_uuid_occurrences(revised, new) == 1


def structural_block(archive: bytes) -> bytes:
    header = bytearray(56)
    header[:4] = b"UCuA"
    struct.pack_into("<I", header, 28, len(archive) + 36)
    struct.pack_into("<I", header, 52, len(archive))
    return bytes(header) + archive + bytes(16)


def legacy_structural_block(archive: bytes) -> bytes:
    header = bytearray(56)
    header[:4] = b"UCuA"
    struct.pack_into("<I", header, 28, len(archive) + 20)
    struct.pack_into("<I", header, 52, len(archive))
    return bytes(header) + archive


def structural_container(*archives: bytes) -> bytes:
    header = bytearray(36)
    header[:4] = b"OCuA"
    return bytes(header) + b"".join(structural_block(archive) for archive in archives)


def test_structural_scanner_accepts_legacy_block_without_post_plist_identity() -> None:
    header = bytearray(36)
    header[:4] = b"OCuA"
    data = bytes(header) + legacy_structural_block(mapping_archive())

    assert read_mapping_targets(data) == [136, 140, 136]


def test_structural_scanner_ignores_plugin_internal_bplist_magic() -> None:
    noise = plistlib.dumps("not a system archive", fmt=plistlib.FMT_BINARY)
    valid = mapping_archive()
    data = structural_container(valid) + noise

    assert read_mapping_targets(data) == [136, 140, 136]


def test_structural_scanner_rejects_malformed_block_length() -> None:
    data = bytearray(structural_container(mapping_archive()))
    struct.pack_into("<I", data, 36 + 28, len(data) * 2)

    with pytest.raises(ConcertFormatError, match="UCuA block length is invalid"):
        read_mapping_targets(bytes(data))


def test_ucua_bytes_inside_archive_do_not_terminate_it_early() -> None:
    archive = plistlib.dumps(
        {"$objects": ["$null", {"kGInstIDKey": 136}], "noise": b"embedded-UCuA-marker"},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )

    assert read_mapping_targets(chunk(archive)) == [136]
