from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from concert_tools.enumeration import PluginParameter, enrich_parameters
from concert_tools.errors import ConcertFormatError
from concert_tools.parameter_metadata import (
    ParameterMetadataEntry,
    ParameterMetadataTable,
    discover_parameter_metadata,
    load_parameter_metadata,
)


def _write_table(root: Path, plugin_name: str, entries: list[object]) -> Path:
    plugin = root / plugin_name
    plugin.mkdir(parents=True)
    path = plugin / "CSParameterOrder.plist"
    with path.open("wb") as handle:
        plistlib.dump(
            {
                "ControlSurfaceParameterOrder": entries,
                "UnrelatedFutureHostKey": ["preserved by the host"],
            },
            handle,
            fmt=plistlib.FMT_BINARY,
            sort_keys=False,
        )
    return path


def _raw_parameter(index: int, value: float = 0.0) -> PluginParameter:
    return PluginParameter(
        address=f"pst:{index}",
        identifier=None,
        key_path=None,
        name=None,
        value=value,
        minimum=None,
        maximum=None,
        unit=None,
        unit_name=None,
        display_value=None,
        flags=None,
        source="native_pst",
        byte_offset=index * 4,
        raw_bits="0x00000000",
    )


def test_loads_unordered_sparse_repeated_ids_placeholders_and_future_keys(tmp_path: Path) -> None:
    path = _write_table(
        tmp_path,
        "Channel EQ",
        ["22 Peak 4", "-1", "1 Low Cut Frequency", "22 Peak 4 Gain"],
    )

    table = load_parameter_metadata(path)

    assert table.plugin_name == "Channel EQ"
    assert [(entry.parameter_id, entry.name) for entry in table.entries] == [
        (22, "Peak 4"),
        (1, "Low Cut Frequency"),
        (22, "Peak 4 Gain"),
    ]


def test_discovery_prefers_first_root_and_rejects_traversal_or_symlinks(tmp_path: Path) -> None:
    first = tmp_path / "First"
    second = tmp_path / "Second"
    first_path = _write_table(first, "Gain", ["4 User Gain"])
    _write_table(second, "Gain", ["4 System Gain"])

    table = discover_parameter_metadata("Gain", [first, second])

    assert table is not None
    assert table.path == first_path
    assert table.entries[0].name == "User Gain"
    assert discover_parameter_metadata("Missing", [first, second]) is None
    with pytest.raises(ConcertFormatError, match="one non-empty path component"):
        discover_parameter_metadata("../Gain", [first])
    with pytest.raises(ConcertFormatError, match="one non-empty path component"):
        discover_parameter_metadata("Gain\0", [first])

    alias_root = tmp_path / "Aliases"
    alias_root.mkdir()
    try:
        (alias_root / "Gain").symlink_to(first / "Gain", target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ConcertFormatError, match="directory is a symlink"):
        discover_parameter_metadata("Gain", [alias_root])

    table_alias_root = tmp_path / "TableAliases"
    table_alias_plugin = table_alias_root / "Gain"
    table_alias_plugin.mkdir(parents=True)
    (table_alias_plugin / "CSParameterOrder.plist").symlink_to(first_path)
    with pytest.raises(ConcertFormatError, match="parameter metadata is a symlink"):
        discover_parameter_metadata("Gain", [table_alias_root])


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([], "no non-empty"),
        (["not-prefixed"], "malformed"),
        (["-2"], "malformed"),
        (["4 Gain", 5], "is not a string"),
        (["4 Gain\tInjected"], "invalid name"),
    ],
)
def test_rejects_malformed_parameter_tables(
    tmp_path: Path,
    entries: list[object],
    message: str,
) -> None:
    path = _write_table(tmp_path, "Gain", entries)

    with pytest.raises(ConcertFormatError, match=message):
        load_parameter_metadata(path)


def test_enrichment_uses_explicit_id_offset_and_preserves_unknown_slots(tmp_path: Path) -> None:
    source = tmp_path / "CSParameterOrder.plist"
    source.write_bytes(b"not read by this unit test")
    table = ParameterMetadataTable(
        "Gain",
        source,
        (
            ParameterMetadataEntry(0, "Mode"),
            ParameterMetadataEntry(4, "Short Gain"),
            ParameterMetadataEntry(4, "Gain"),
        ),
    )
    parameters = (_raw_parameter(0), _raw_parameter(1), _raw_parameter(5, 2.0))

    enriched = enrich_parameters(parameters, table)

    assert enriched[0] == parameters[0]
    assert (enriched[1].identifier, enriched[1].name) == ("logic:0", "Mode")
    assert (enriched[2].identifier, enriched[2].name) == ("logic:4", "Gain")
    assert enriched[2].metadata_source == str(source)
    with pytest.raises(ValueError, match="non-negative integer"):
        enrich_parameters(parameters, table, pst_index_offset=-1)
