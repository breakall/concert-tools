from __future__ import annotations

from pathlib import Path

import pytest

from concert_tools.catalog import scan_patch_bundles
from concert_tools.errors import ConcertFormatError


def test_scan_patch_bundles_finds_packages_without_descending_into_them(tmp_path: Path) -> None:
    first = tmp_path / "Category" / "First.patch"
    nested_inside_patch = first / "Not A Library Entry.patch"
    second = tmp_path / "Second.PATCH"
    nested_inside_patch.mkdir(parents=True)
    second.mkdir()

    assert scan_patch_bundles(tmp_path) == [first, second]


def test_scan_patch_bundles_skips_symlinked_subtrees(tmp_path: Path) -> None:
    external = tmp_path / "external"
    (external / "Hidden.patch").mkdir(parents=True)
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "alias").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable")

    assert scan_patch_bundles(root) == []


def test_scan_patch_bundles_rejects_symlink_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable")

    with pytest.raises(ConcertFormatError, match="Symlinked catalog roots"):
        scan_patch_bundles(alias)
