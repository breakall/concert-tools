import plistlib
from pathlib import Path

from fixture_factory import (
    TEST_LARGE_TEXT_CONTROL,
    TEST_SMALL_TEXT_CONTROL,
    make_concert,
    rewrite_synthetic_text,
)

import concert_tools.patch as patch_module
from concert_tools import clone_patch, inspect_concert, read_patch_text, rewrite_patch_text
from concert_tools.ocua import read_mapping_targets


def test_patch_graph_clone_and_independent_text_edits(tmp_path: Path, monkeypatch) -> None:
    source = make_concert(tmp_path / "source.concert")
    output = tmp_path / "output.patch"
    monkeypatch.setattr(patch_module, "rewrite_attributed_plain_text", rewrite_synthetic_text)

    result = clone_patch(
        source / "Concert.patch" / "Template.patch",
        output,
        name="Cloned Patch",
        instance_ids=[50, 60],
    )
    rewrite_patch_text(output, TEST_LARGE_TEXT_CONTROL, "F", embedded_font_size=96.0)
    rewrite_patch_text(output, TEST_SMALL_TEXT_CONTROL, "First note", embedded_font_size=48.0)

    assert result.name == "Cloned Patch"
    assert read_patch_text(output, TEST_LARGE_TEXT_CONTROL) == "F"
    assert read_patch_text(output, TEST_SMALL_TEXT_CONTROL) == "First note"
    data = plistlib.loads((output / "data.plist").read_bytes())
    assert {channel["Channel_instID"] for channel in data["channels"]} == {50, 60}
    assert set(read_mapping_targets((output / "Layer 1.cst").read_bytes())) == {50, 60}


def test_inspection_preserves_unreadable_nodes(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    root_data_path = source / "Concert.patch" / "data.plist"
    root_data = plistlib.loads(root_data_path.read_bytes())
    root_data["nodes"].append("Missing.patch")
    with root_data_path.open("wb") as handle:
        plistlib.dump(root_data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    result = inspect_concert(source)

    assert len(result.patches) == 3
    assert result.patches[-1].name == "Missing"
    assert result.patches[-1].error
    assert len(result.issues) == 1


def test_inspection_does_not_assume_specific_text_controls(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    patch_path = source / "Concert.patch" / "Second.patch"
    data_path = patch_path / "data.plist"
    data = plistlib.loads(data_path.read_bytes())
    data["patch"]["engineNode"]["uiPluginDataDict"] = {
        "\x01IDENTITY:Arbitrary A": {"storeDict": {}},
        "\x01IDENTITY:Arbitrary B": {"storeDict": {}},
    }
    with data_path.open("wb") as handle:
        plistlib.dump(data, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

    result = inspect_concert(source)

    utility = result.patches[1]
    assert utility.error is None
    assert len(utility.channels) == 2
    assert result.issues == ()
