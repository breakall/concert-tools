from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from fixture_factory import make_concert

from concert_tools.errors import ConcertFormatError
from concert_tools.manifest import inspect_patch_manifest


def test_inspect_patch_manifest_describes_channels(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"

    manifest = inspect_patch_manifest(patch)

    assert manifest.name == "Template"
    assert [channel.name for channel in manifest.channels] == ["Layer 1", "Layer 2"]
    assert [channel.instance_id for channel in manifest.channels] == [10, 20]
    assert manifest.as_dict()["path"] == str(patch)


def test_inspect_patch_manifest_rejects_missing_setting(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    (patch / "Layer 1.cst").unlink()

    with pytest.raises(ConcertFormatError, match="setting is missing"):
        inspect_patch_manifest(patch)


def test_inspect_patch_manifest_rejects_boolean_instance_id(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    data_path = patch / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["channels"][0]["Channel_instID"] = True
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY)

    with pytest.raises(ConcertFormatError, match="instance ID must be an integer"):
        inspect_patch_manifest(patch)


def test_inspect_patch_manifest_rejects_malformed_engine_node(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    data_path = patch / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["patch"]["engineNode"] = []
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY)

    with pytest.raises(ConcertFormatError, match="engine node is not a dictionary"):
        inspect_patch_manifest(patch)
