from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest
from fixture_factory import make_concert

from concert_tools.cli import main
from concert_tools.errors import ConcertFormatError
from concert_tools.mappings import enumerate_screen_control_mappings


def _mapping_archive() -> bytes:
    objects = [
        "$null",
        {"NS.objects": [plistlib.UID(2), plistlib.UID(8)], "$class": plistlib.UID(10)},
        {
            "$class": plistlib.UID(3),
            "kGInstIDKey": 10,
            "slot": plistlib.UID(4),
            "parameterIndex_1": plistlib.UID(5),
            "kHasSavedValueKey": True,
            "kSavedValueDoubleKey": 0.75,
            "rangeLow": plistlib.UID(6),
            "rangeHigh": plistlib.UID(7),
            "kDiscreteStepsKey": False,
            "momentaryType": False,
        },
        {
            "$classname": "MAPlugInParameterMapping",
            "$classes": ["MAPlugInParameterMapping", "MAMapping", "NSObject"],
        },
        12,
        29,
        450,
        -1,
        {
            "$class": plistlib.UID(9),
            "kGInstIDKey": 20,
            "kHasSavedValueKey": True,
            "kSavedValueFloatKey": 1.0,
            "rangeLow": 0,
            "rangeHigh": 1,
            "kDiscreteStepsKey": True,
            "momentaryType": True,
        },
        {
            "$classname": "MAChannelParameterMapping",
            "$classes": ["MAChannelParameterMapping", "MAMapping", "NSObject"],
        },
        {"$classname": "NSMutableArray", "$classes": ["NSMutableArray", "NSArray"]},
    ]
    return plistlib.dumps(
        {
            "$version": 100000,
            "$archiver": "NSKeyedArchiver",
            "$top": {"root": plistlib.UID(1)},
            "$objects": objects,
        },
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )


def _install_mapping_fixture(patch: Path) -> None:
    data_path = patch / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["patch"]["engineNode"]["parameterMappingMap"]["storeDict"] = {
        "\x01IDENTITY:Performance Knob": _mapping_archive()
    }
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def test_enumerates_screen_control_mappings_and_resolves_channels(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    _install_mapping_fixture(patch)

    mappings = enumerate_screen_control_mappings(patch)

    assert len(mappings) == 2
    first, second = mappings
    assert first.control == "Performance Knob"
    assert first.mapping_index == 1
    assert first.mapping_class == "MAPlugInParameterMapping"
    assert (first.target_instance_id, first.target_channel) == (10, "Layer 1")
    assert (first.plugin_slot, first.parameter_index) == (12, 29)
    assert (first.saved_value, first.range_low, first.range_high) == (0.75, 450.0, -1.0)
    assert not first.discrete_steps
    assert not first.momentary
    assert second.mapping_class == "MAChannelParameterMapping"
    assert (second.target_instance_id, second.target_channel) == (20, "Layer 2")
    assert second.plugin_slot is None
    assert second.parameter_index is None
    assert second.discrete_steps
    assert second.momentary


def test_mappings_cli_is_an_independent_json_call(tmp_path: Path, capsys) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    _install_mapping_fixture(patch)

    assert main(["mappings", str(patch), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    assert payload[0]["control"] == "Performance Knob"
    assert payload[0]["target_channel"] == "Layer 1"


def test_rejects_malformed_mapping_archive(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    data_path = patch / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["patch"]["engineNode"]["parameterMappingMap"]["storeDict"] = {
        "\x01IDENTITY:Broken": b"not a keyed archive"
    }
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    with pytest.raises(ConcertFormatError, match="Could not enumerate mappings for 'Broken'"):
        enumerate_screen_control_mappings(patch)
