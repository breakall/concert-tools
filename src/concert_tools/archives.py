"""Helpers for the NSKeyedArchives stored inside concert bundles."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from .errors import ConcertFormatError

SWIFT_TEXT_REWRITER = r"""
import Foundation
import AppKit

let oldData = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let oldText = try NSKeyedUnarchiver.unarchiveTopLevelObjectWithData(oldData) as! NSAttributedString
let revisedText = NSMutableAttributedString(attributedString: oldText)
revisedText.mutableString.setString(CommandLine.arguments[3])
let newData = try NSKeyedArchiver.archivedData(withRootObject: revisedText, requiringSecureCoding: false)
try newData.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
"""


def read_attributed_plain_text(blob: bytes) -> str:
    """Return the plain string from a recognized archived NSAttributedString."""
    try:
        value = plistlib.loads(blob)["$objects"][2]
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OverflowError,
        plistlib.InvalidFileException,
    ) as error:
        raise ConcertFormatError("Could not read archived attributed text.") from error
    if isinstance(value, dict):
        value = value.get("NS.string")
    if not isinstance(value, str):
        raise ConcertFormatError("Attributed-text archive has an unrecognized string object.")
    return value


def rewrite_attributed_plain_text(blob: bytes, replacement: str, work_dir: Path) -> bytes:
    """Change text while preserving MainStage's font and paragraph attributes.

    This operation requires the macOS Swift toolchain and AppKit.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    old_path = work_dir / "old.archive"
    new_path = work_dir / "new.archive"
    old_path.write_bytes(blob)
    environment = os.environ.copy()
    environment.setdefault("CLANG_MODULE_CACHE_PATH", str(work_dir / "swift-module-cache"))
    try:
        subprocess.run(
            ["swift", "-", str(old_path), str(new_path), replacement],
            input=SWIFT_TEXT_REWRITER,
            text=True,
            check=True,
            capture_output=True,
            env=environment,
            timeout=60,
        )
    except FileNotFoundError as error:
        raise ConcertFormatError("The macOS 'swift' command is required to preserve rich text.") from error
    except subprocess.CalledProcessError as error:
        raise ConcertFormatError(
            f"Swift could not rewrite attributed text: {error.stderr.strip()}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ConcertFormatError("Swift timed out while rewriting attributed text.") from error
    return new_path.read_bytes()


def instance_id_values(value: Any) -> list[int]:
    """Collect every `kGInstIDKey` target from a decoded archive."""
    values: list[int] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "kGInstIDKey" and isinstance(child, int) and not isinstance(child, bool):
                values.append(child)
            else:
                values.extend(instance_id_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(instance_id_values(child))
    return values


def rewrite_mapping_store(store: dict[str, Any], replacements: dict[int, int]) -> int:
    """Rewrite mapping integers in place, preserving every other archive byte."""
    # Imported lazily to avoid the module-level cycle: ocua also uses the archive
    # traversal helper above for its embedded mapping archive.
    from .ocua import rewrite_bplist_integer_objects

    changed = 0
    for control, blob in store.items():
        if not isinstance(blob, bytes) or not blob.startswith(b"bplist00"):
            continue
        try:
            archive = plistlib.loads(blob)
        except (plistlib.InvalidFileException, ValueError, OverflowError) as error:
            raise ConcertFormatError(f"Could not decode mappings for {control!r}.") from error
        targets = instance_id_values(archive)
        applicable = {old: new for old, new in replacements.items() if old in targets}
        if applicable:
            store[control] = rewrite_bplist_integer_objects(blob, applicable)
            changed += sum(target in applicable for target in targets)
    return changed


def mapping_store_targets(store: dict[str, Any]) -> list[int]:
    """Decode every mapping archive in a parameterMappingMap store."""
    values: list[int] = []
    for control, blob in store.items():
        if not isinstance(blob, bytes) or not blob.startswith(b"bplist00"):
            continue
        try:
            values.extend(instance_id_values(plistlib.loads(blob)))
        except (plistlib.InvalidFileException, ValueError, OverflowError) as error:
            raise ConcertFormatError(f"Could not decode mappings for {control!r}.") from error
    return values
