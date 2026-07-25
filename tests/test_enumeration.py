from __future__ import annotations

import plistlib
import struct
from pathlib import Path

import pytest
from fixture_factory import make_concert

from concert_tools import enumeration
from concert_tools.cli import main
from concert_tools.enumeration import (
    enumerate_channel_strips,
    enumerate_parameters,
    enumerate_patches,
    enumerate_plugins,
)
from concert_tools.errors import ConcertFormatError


def _named_plugin_block(
    *,
    slot: int,
    role: int,
    name: str,
    preset: str,
    payload: bytes,
) -> bytes:
    prefix = bytearray(208)
    prefix[:4] = b"UCuA"
    struct.pack_into("<H", prefix, 18, slot)
    prefix[50 : 50 + len(preset)] = preset.encode()
    prefix[156 : 156 + len(name)] = name.encode()
    prefix[187] = role
    block = prefix + payload
    struct.pack_into("<I", block, 28, len(block) - 36)
    return bytes(block)


def _pst_payload(
    values: tuple[float | bytes, ...] = (0.25, b"\xca\xf2\x49\x71", 42.0),
    *,
    declared_count: int | None = None,
    tail: bytes = b"",
) -> bytes:
    parameter_data = b"".join(
        value if isinstance(value, bytes) else struct.pack("<f", value) for value in values
    )
    payload = parameter_data + tail
    count = len(values) if declared_count is None else declared_count
    return struct.pack("<III8sI", 24 + len(payload), 1, count, b"GAMETSPP", 0) + payload


def _au_state() -> dict[str, object]:
    return {
        "name": "Synthetic State",
        "manufacturer": int.from_bytes(b"Acme", "big"),
        "type": int.from_bytes(b"aufx", "big"),
        "subtype": int.from_bytes(b"Test", "big"),
        "version": 1,
        "data": b"saved-state",
    }


def _channel_strip(*blocks: bytes) -> bytes:
    header = bytearray(36)
    header[:4] = b"OCuA"
    struct.pack_into("<I", header, 28, 0)
    return bytes(header) + b"".join(blocks)


def _plugin_fixture(path: Path) -> Path:
    state = plistlib.dumps(_au_state(), fmt=plistlib.FMT_XML, sort_keys=False)
    system_state = plistlib.dumps(
        {"$archiver": "NSKeyedArchiver", "$objects": ["$null"]},
        fmt=plistlib.FMT_BINARY,
    )
    system_prefix = bytearray(56)
    system_prefix[:4] = b"UCuA"
    struct.pack_into("<H", system_prefix, 18, 30)
    system_block = system_prefix + system_state
    struct.pack_into("<I", system_block, 28, len(system_block) - 36)
    path.write_bytes(
        _channel_strip(
            _named_plugin_block(
                slot=2,
                role=0x08,
                name="Synth",
                preset="Lead.pst",
                payload=_pst_payload(),
            ),
            _named_plugin_block(
                slot=4,
                role=0,
                name="Third FX",
                preset="Wide.aupreset",
                payload=state,
            ),
            _named_plugin_block(
                slot=18,
                role=0x02,
                name="Arpeggiator",
                preset="Up.pst",
                payload=_pst_payload(),
            ),
            bytes(system_block),
        )
    )
    return path


def test_enumerate_patches_returns_only_direct_concert_children(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")

    patches = enumerate_patches(concert)

    assert [(patch.index, patch.name) for patch in patches] == [(1, "Template"), (2, "Second")]
    assert patches[0].path == concert / "Concert.patch" / "Template.patch"


def test_enumerate_patches_does_not_parse_channel_strips(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    data_path = patch / "data.plist"
    with data_path.open("rb") as handle:
        data = plistlib.load(handle)
    data["channels"] = "deliberately malformed for the next primitive"
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    patches = enumerate_patches(concert)

    assert patches[0].name == "Template"
    assert patches[0].error is None
    with pytest.raises(ConcertFormatError, match="channels must be a non-empty array"):
        enumerate_channel_strips(patch)


def test_enumerate_channel_strips_returns_paths_and_identities(tmp_path: Path) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"

    strips = enumerate_channel_strips(patch)

    assert [(strip.index, strip.name, strip.instance_id) for strip in strips] == [
        (1, "Layer 1", 10),
        (2, "Layer 2", 20),
    ]
    assert strips[0].path == patch / "Layer 1.cst"


def test_enumerate_plugins_reports_order_role_format_and_component(tmp_path: Path) -> None:
    channel_strip = _plugin_fixture(tmp_path / "Synthetic.cst")

    plugins = enumerate_plugins(channel_strip)

    assert [(plugin.index, plugin.slot, plugin.role, plugin.name) for plugin in plugins] == [
        (1, 2, "instrument", "Synth"),
        (2, 4, "audio_effect", "Third FX"),
        (3, 18, "midi_effect", "Arpeggiator"),
    ]
    assert plugins[0].format == "pst"
    assert plugins[1].format == "aupreset"
    assert (plugins[1].manufacturer, plugins[1].type, plugins[1].subtype) == (
        "Acme",
        "aufx",
        "Test",
    )


def test_enumerate_plugins_decodes_length_prefixed_binary_au_state(tmp_path: Path) -> None:
    state = plistlib.dumps(_au_state(), fmt=plistlib.FMT_BINARY, sort_keys=False)
    payload = struct.pack("<I", len(state)) + state
    channel_strip = tmp_path / "Binary State.cst"
    channel_strip.write_bytes(
        _channel_strip(
            _named_plugin_block(
                slot=14,
                role=0,
                name="Binary FX",
                preset="Binary.aupreset",
                payload=payload,
            )
        )
    )

    plugins = enumerate_plugins(channel_strip)

    assert len(plugins) == 1
    assert plugins[0].slot == 14
    assert plugins[0].format == "aupreset"
    assert plugins[0].subtype == "Test"


def test_enumerate_plugins_rejects_malformed_or_symlinked_cst(tmp_path: Path) -> None:
    malformed = tmp_path / "Malformed.cst"
    malformed_data = bytearray(b"OCuA" + bytes(32))
    struct.pack_into("<I", malformed_data, 28, 999)
    malformed.write_bytes(malformed_data)
    with pytest.raises(ConcertFormatError, match="invalid OCuA header"):
        enumerate_plugins(malformed)

    target = _plugin_fixture(tmp_path / "Target.cst")
    alias = tmp_path / "Alias.cst"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ConcertFormatError, match="Not a regular"):
        enumerate_plugins(alias)


def test_enumerate_parameters_queries_only_selected_plugin(tmp_path: Path, monkeypatch) -> None:
    channel_strip = _plugin_fixture(tmp_path / "Synthetic.cst")
    observed = {}

    def fake_query(state, component):
        observed["state"] = state
        observed["component"] = component
        return [
            {
                "address": "42",
                "identifier": "mix",
                "key_path": "output.mix",
                "name": "Mix",
                "value": 0.25,
                "minimum": 0.0,
                "maximum": 1.0,
                "unit": 0,
                "unit_name": "%",
                "display_value": "25%",
                "flags": 3,
                "value_strings": [],
            }
        ]

    monkeypatch.setattr(enumeration, "_query_au_parameters", fake_query)

    parameters = enumerate_parameters(channel_strip, 2)

    assert observed == {
        "state": _au_state(),
        "component": (
            int.from_bytes(b"Acme", "big"),
            int.from_bytes(b"aufx", "big"),
            int.from_bytes(b"Test", "big"),
        ),
    }
    assert len(parameters) == 1
    assert parameters[0].source == "audio_unit"
    assert parameters[0].name == "Mix"
    assert parameters[0].display_value == "25%"


def test_enumerate_parameters_reads_declared_native_pst_float_slots(tmp_path: Path) -> None:
    channel_strip = _plugin_fixture(tmp_path / "Synthetic.cst")

    parameters = enumerate_parameters(channel_strip, 1, metadata_roots=())

    assert [(parameter.address, parameter.value) for parameter in parameters] == [
        ("pst:0", 0.25),
        ("pst:2", 42.0),
    ]
    assert parameters[0].source == "native_pst"
    assert parameters[0].name is None
    assert parameters[0].byte_offset == 0
    assert parameters[0].raw_bits == "0x3e800000"


def test_enumerate_parameters_enriches_native_slots_from_installed_metadata(tmp_path: Path) -> None:
    channel_strip = _plugin_fixture(tmp_path / "Synthetic.cst")
    metadata_root = tmp_path / "Metadata"
    metadata_path = metadata_root / "Synth" / "CSParameterOrder.plist"
    metadata_path.parent.mkdir(parents=True)
    with metadata_path.open("wb") as handle:
        plistlib.dump(
            {"ControlSurfaceParameterOrder": ["1 Frequency"]},
            handle,
            fmt=plistlib.FMT_BINARY,
        )

    parameters = enumerate_parameters(channel_strip, 1, metadata_roots=[metadata_root])

    assert parameters[0].name is None
    assert parameters[1].address == "pst:2"
    assert parameters[1].identifier == "logic:1"
    assert parameters[1].name == "Frequency"
    assert parameters[1].metadata_source == str(metadata_path)


def test_enumerate_parameters_bounds_native_pst_count_and_preserves_nonfinite(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "Malformed PST.cst"
    malformed.write_bytes(
        _channel_strip(
            _named_plugin_block(
                slot=2,
                role=0x08,
                name="Bad Native",
                preset="Bad.pst",
                payload=_pst_payload((1.0,), declared_count=2),
            )
        )
    )
    with pytest.raises(ConcertFormatError, match="declares 2 float slots but contains only 1"):
        enumerate_parameters(malformed, 1)

    nonfinite = tmp_path / "Nonfinite PST.cst"
    nonfinite.write_bytes(
        _channel_strip(
            _named_plugin_block(
                slot=2,
                role=0x08,
                name="Native",
                preset="Native.pst",
                payload=_pst_payload((float("nan"),), tail=b"ignored tail"),
            )
        )
    )
    parameter = enumerate_parameters(nonfinite, 1)[0]
    assert parameter.value is None
    assert parameter.raw_bits == "0x7fc00000"


def test_each_cli_call_emits_only_its_immediate_level(tmp_path: Path, monkeypatch, capsys) -> None:
    concert = make_concert(tmp_path / "Fixture.concert")
    patch = concert / "Concert.patch" / "Template.patch"
    channel_strip = _plugin_fixture(tmp_path / "Synthetic.cst")

    assert main(["patches", str(concert), "--json"]) == 0
    patch_payload = capsys.readouterr().out
    assert '"name": "Template"' in patch_payload
    assert "channel_strips" not in patch_payload

    assert main(["channel-strips", str(patch), "--json"]) == 0
    strip_payload = capsys.readouterr().out
    assert '"name": "Layer 1"' in strip_payload
    assert "plugins" not in strip_payload

    assert main(["plugins", str(channel_strip), "--json"]) == 0
    plugin_payload = capsys.readouterr().out
    assert '"name": "Third FX"' in plugin_payload
    assert "parameters" not in plugin_payload

    assert main(["parameters", str(channel_strip), "--plugin", "1", "--no-metadata", "--json"]) == 0
    native_parameter_payload = capsys.readouterr().out
    assert '"source": "native_pst"' in native_parameter_payload
    assert '"byte_offset": 0' in native_parameter_payload
    assert "plugins" not in native_parameter_payload

    metadata_root = tmp_path / "Metadata"
    metadata_path = metadata_root / "Synth" / "CSParameterOrder.plist"
    metadata_path.parent.mkdir(parents=True)
    with metadata_path.open("wb") as handle:
        plistlib.dump(
            {"ControlSurfaceParameterOrder": ["1 Frequency"]},
            handle,
            fmt=plistlib.FMT_BINARY,
        )
    assert (
        main(
            [
                "parameter-metadata",
                "Synth",
                "--metadata-root",
                str(metadata_root),
                "--json",
            ]
        )
        == 0
    )
    metadata_payload = capsys.readouterr().out
    assert '"parameter_id": 1' in metadata_payload
    assert '"name": "Frequency"' in metadata_payload

    monkeypatch.setattr(
        enumeration,
        "_query_au_parameters",
        lambda _state, _component: [
            {
                "address": "1",
                "identifier": "gain",
                "key_path": "gain",
                "name": "Gain",
                "value": 0.5,
                "minimum": 0.0,
                "maximum": 1.0,
                "unit": 0,
                "flags": 0,
            }
        ],
    )
    assert main(["parameters", str(channel_strip), "--plugin", "2", "--json"]) == 0
    parameter_payload = capsys.readouterr().out
    assert '"name": "Gain"' in parameter_payload
    assert "plugins" not in parameter_payload
