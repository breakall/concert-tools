import plistlib
from pathlib import Path

import pytest
from fixture_factory import TEST_LARGE_TEXT_CONTROL, make_concert

from concert_tools import clone_patch, rewrite_patch_text, validate_concert
from concert_tools.archives import rewrite_mapping_store
from concert_tools.errors import ConcertFormatError
from concert_tools.ocua import rewrite_bplist_integer_objects


def write_plist(path: Path, value: dict) -> None:
    with path.open("wb") as handle:
        plistlib.dump(value, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def test_text_rewrite_failure_does_not_partially_change_embedded_copy(tmp_path: Path, monkeypatch) -> None:
    source = make_concert(tmp_path / "source.concert")
    patch = source / "Concert.patch" / "Template.patch"
    setting_path = patch / "Layer 1.cst"
    original = setting_path.read_bytes()

    def fail_rewrite(*_args, **_kwargs):
        raise ConcertFormatError("synthetic rich-text failure")

    monkeypatch.setattr("concert_tools.patch.rewrite_attributed_plain_text", fail_rewrite)
    with pytest.raises(ConcertFormatError, match="synthetic rich-text failure"):
        rewrite_patch_text(patch, TEST_LARGE_TEXT_CONTROL, "F", embedded_font_size=96.0)

    assert setting_path.read_bytes() == original


def test_channel_traversal_cannot_write_outside_patch(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    template_data_path = source / "Concert.patch" / "Template.patch" / "data.plist"
    data = plistlib.loads(template_data_path.read_bytes())
    data["channels"][0]["Filename"] = "../outside.cst"
    write_plist(template_data_path, data)
    outside = source / "Concert.patch" / "outside.cst"
    outside.write_bytes(b"unchanged")
    with pytest.raises(ConcertFormatError, match="Unsafe channel-strip setting path"):
        clone_patch(
            source / "Concert.patch" / "Template.patch",
            tmp_path / "output.patch",
            name="Output",
            instance_ids=[50, 60],
        )

    assert outside.read_bytes() == b"unchanged"
    assert not (tmp_path / "output.concert").exists()


def test_symlinked_channel_setting_is_rejected(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    setting = source / "Concert.patch" / "Template.patch" / "Layer 2.cst"
    external = tmp_path / "external.cst"
    external.write_bytes(setting.read_bytes())
    setting.unlink()
    setting.symlink_to(external)
    with pytest.raises(ConcertFormatError, match="Symlink"):
        clone_patch(
            source / "Concert.patch" / "Template.patch",
            tmp_path / "output.patch",
            name="Output",
            instance_ids=[50, 60],
        )

    assert external.exists()
    assert not (tmp_path / "output.concert").exists()


def test_shared_integer_object_is_not_rewritten() -> None:
    archive = plistlib.dumps(
        {"$objects": ["$null", {"kGInstIDKey": 136}, {"unrelatedValue": 136}]},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )

    with pytest.raises(ConcertFormatError, match="shared with a non-mapping object"):
        rewrite_bplist_integer_objects(archive, {136: 156})


def test_outer_mapping_shared_integer_object_is_not_rewritten() -> None:
    blob = plistlib.dumps(
        {"$objects": ["$null", {"kGInstIDKey": 136}, {"unrelatedValue": 136}]},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )
    store = {"control": blob}

    with pytest.raises(ConcertFormatError, match="shared with a non-mapping object"):
        rewrite_mapping_store(store, {136: 156})

    assert store["control"] == blob


def test_boolean_is_not_accepted_as_an_instance_id_replacement() -> None:
    blob = plistlib.dumps(
        {"$objects": ["$null", {"kGInstIDKey": 136}]},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )

    with pytest.raises(ConcertFormatError, match="must contain integers"):
        rewrite_bplist_integer_objects(blob, {136: True})


def test_validator_accepts_patch_with_no_outer_or_embedded_mappings(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    patch = source / "Concert.patch" / "Template.patch"
    data_path = patch / "data.plist"
    data = plistlib.loads(data_path.read_bytes())
    data["patch"]["engineNode"]["parameterMappingMap"]["storeDict"] = {}
    write_plist(data_path, data)
    layout_path = patch / "Layer 1.cst"
    layout_path.write_bytes(layout_path.read_bytes().replace(b"bplist00", b"xplist00", 1))

    assert validate_concert(source) == []


def test_validator_reports_malformed_channel_without_crashing(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    data_path = source / "Concert.patch" / "Template.patch" / "data.plist"
    data = plistlib.loads(data_path.read_bytes())
    data["channels"].append("not-a-channel")
    write_plist(data_path, data)

    messages = [issue.message for issue in validate_concert(source)]

    assert "Channel 3 is not a dictionary object." in messages


def test_disabled_patch_still_requires_consistent_mapping_graph(tmp_path: Path) -> None:
    source = make_concert(tmp_path / "source.concert")
    patch = source / "Concert.patch" / "Second.patch"
    data_path = patch / "data.plist"
    data = plistlib.loads(data_path.read_bytes())
    data["patch"]["engineNode"]["disabled"] = True
    write_plist(data_path, data)
    channel_uuid = data["channels"][0]["UUID"]
    from fixture_factory import channel_uuid_archive, ocua_container

    (patch / "Layer 1.cst").write_bytes(ocua_container(channel_uuid_archive(channel_uuid)))

    messages = [issue.message for issue in validate_concert(source)]

    assert "Expected one embedded mapping host; found 0." in messages
