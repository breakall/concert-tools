from __future__ import annotations

import ast
from pathlib import Path


def test_package_has_no_reverse_dependencies() -> None:
    source_root = Path(__file__).parents[1] / "src" / "concert_tools"
    package_modules = {path.stem for path in source_root.glob("*.py")}

    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".", 1)[0] not in package_modules


def test_public_api_does_not_export_headless_orchestration() -> None:
    import concert_tools

    assert "build_concert" not in concert_tools.__all__
    assert "Song" not in concert_tools.__all__
