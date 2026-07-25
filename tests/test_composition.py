from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from fixture_factory import make_component_patch

from concert_tools.archives import mapping_store_targets
from concert_tools.composition import compose_patch
from concert_tools.errors import ConcertFormatError
from concert_tools.ocua import channel_uuid_occurrences, iter_embedded_plists, read_mapping_targets


def _ui(identity: str, text: str | None, *, override: bool = True) -> dict:
    store = {"background": identity}
    contains = {"settingsDict": True, "text": text is not None}
    if text is not None:
        store["text"] = text
    return {
        "identity": identity,
        "storeDict": store,
        "containsDictionary": contains,
        "overrideDict": {identity: False} if override else {},
    }


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "First.patch"
    second = tmp_path / "Second.patch"
    make_component_patch(
        first,
        name="First",
        instance_ids=[10],
        controls=["Shared", "One"],
        labels=["Object Global", "Object One"],
        ui={
            "\x01IDENTITY:Object Global": _ui("\x01IDENTITY:Object Global", "global"),
            "\x01IDENTITY:Object One": _ui("\x01IDENTITY:Object One", "one"),
            "\x01IDENTITY:Object Two": _ui("\x01IDENTITY:Object Two", None, override=False),
        },
    )
    make_component_patch(
        second,
        name="Second",
        instance_ids=[20, 24],
        controls=["Shared", "Two"],
        labels=["Object Global", "Object Two"],
        ui={
            "\x01IDENTITY:Object Global": _ui("\x01IDENTITY:Object Global", None),
            "\x01IDENTITY:Object One": _ui("\x01IDENTITY:Object One", None, override=False),
            "\x01IDENTITY:Object Two": _ui("\x01IDENTITY:Object Two", "two"),
        },
    )
    return first, second


def test_compose_patch_merges_channels_mappings_and_richest_ui(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    output = tmp_path / "Combined.patch"

    manifest = compose_patch([first, second], output, name="Combined", instance_ids=[100, 104, 108])

    assert manifest.name == "Combined"
    assert [channel.instance_id for channel in manifest.channels] == [100, 104, 108]
    assert [channel.filename for channel in manifest.channels] == [
        "Layer 1.cst",
        "Layer 1 2.cst",
        "Layer 2.cst",
    ]
    with (output / "data.plist").open("rb") as handle:
        data = plistlib.load(handle)
    engine = data["patch"]["engineNode"]
    assert "patchPath" not in data["patch"]
    assert "imageNameForSetFolder" not in engine
    assert engine["hasProgramChange"] is True
    assert set(mapping_store_targets(engine["parameterMappingMap"]["storeDict"])) == {
        100,
        104,
        108,
    }
    ui = engine["uiPluginDataDict"]
    assert ui["\x01IDENTITY:Object Global"]["storeDict"]["text"] == "global"
    assert ui["\x01IDENTITY:Object Global"]["overrideDict"] == {}
    assert ui["\x01IDENTITY:Object One"]["storeDict"]["text"] == "one"
    assert ui["\x01IDENTITY:Object Two"]["storeDict"]["text"] == "two"

    mapping_hosts = []
    for channel in data["channels"]:
        setting = (output / channel["Filename"]).read_bytes()
        assert channel_uuid_occurrences(setting, channel["UUID"]) == 1
        try:
            mapping_hosts.append(set(read_mapping_targets(setting)))
        except ConcertFormatError:
            pass
    assert mapping_hosts == [{100, 104, 108}]


def test_compose_patch_uses_fresh_default_ids(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)

    manifest = compose_patch([first, second], tmp_path / "Combined.patch", name="Combined")

    assert [channel.instance_id for channel in manifest.channels] == [28, 32, 36]


def test_compose_patch_rejects_bad_assigned_ids_without_output(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    output = tmp_path / "Combined.patch"

    with pytest.raises(ValueError, match="must be unique"):
        compose_patch([first, second], output, name="Combined", instance_ids=[100, 100, 108])

    assert not output.exists()


def test_compose_patch_rejects_destination_inside_source(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    output = first / "Nested.patch"

    with pytest.raises(ConcertFormatError, match="Destination cannot be"):
        compose_patch([first, second], output, name="Combined")

    assert not output.exists()


def test_compose_patch_rejects_symlinked_channel_setting(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    setting = second / "Layer 2.cst"
    original = setting.read_bytes()
    setting.unlink()
    target = tmp_path / "outside.cst"
    target.write_bytes(original)
    try:
        setting.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable")

    with pytest.raises(ConcertFormatError, match="Symlink"):
        compose_patch([first, second], tmp_path / "Combined.patch", name="Combined")


def test_compose_patch_rejects_component_with_incomplete_mappings(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    data_path = second / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["patch"]["engineNode"]["parameterMappingMap"]["storeDict"].pop("\x01IDENTITY:Two")
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    with pytest.raises(ConcertFormatError, match="Outer and embedded mapping controls disagree"):
        compose_patch([first, second], tmp_path / "Combined.patch", name="Combined")


def test_composed_nonhost_archives_are_empty(tmp_path: Path) -> None:
    first, second = _sources(tmp_path)
    output = tmp_path / "Combined.patch"
    compose_patch([first, second], output, name="Combined")
    with (output / "data.plist").open("rb") as handle:
        data = plistlib.load(handle)

    second_host = (output / data["channels"][1]["Filename"]).read_bytes()
    embedded = list(iter_embedded_plists(second_host))
    assert len(embedded[0].value["$objects"]) == 3
    assert len(embedded[1].value["$objects"]) == 6
