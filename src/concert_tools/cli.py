"""Command-line interface for concert-tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import scan_patch_bundles
from .composition import compose_patch
from .concert import inspect_concert
from .enumeration import (
    enumerate_channel_strips,
    enumerate_parameters,
    enumerate_patches,
    enumerate_plugins,
)
from .errors import ConcertFormatError
from .manifest import inspect_patch_manifest
from .parameter_metadata import discover_parameter_metadata
from .validation import validate_concert


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="concert-tools", description="Inspect and transform concert bundles."
    )
    commands = result.add_subparsers(dest="command", required=True)

    inspect = commands.add_parser("inspect", help="List recognized patches and channels")
    inspect.add_argument("concert", type=Path)
    inspect.add_argument("--json", action="store_true", dest="as_json")

    validate = commands.add_parser("validate", help="Check UUIDs, instance IDs, mappings, and display text")
    validate.add_argument("concert", type=Path)

    catalog = commands.add_parser("catalog", help="Discover patch bundles without vendor assumptions")
    catalog.add_argument("root", type=Path)
    catalog.add_argument("--inspect", action="store_true", help="Read and validate every patch manifest")
    catalog.add_argument("--json", action="store_true", dest="as_json")

    compose = commands.add_parser("compose", help="Combine compatible pre-positioned patches")
    compose.add_argument("sources", nargs="+", type=Path)
    compose.add_argument("--output", required=True, type=Path)
    compose.add_argument("--name", required=True)
    compose.add_argument("--instance-id", action="append", type=int, dest="instance_ids")

    patches = commands.add_parser("patches", help="Enumerate patches directly contained by a concert")
    patches.add_argument("concert", type=Path)
    patches.add_argument("--json", action="store_true", dest="as_json")

    channel_strips = commands.add_parser(
        "channel-strips",
        help="Enumerate channel strips directly contained by a patch",
    )
    channel_strips.add_argument("patch", type=Path)
    channel_strips.add_argument("--json", action="store_true", dest="as_json")

    plugins = commands.add_parser("plugins", help="Enumerate plugins in a channel-strip setting")
    plugins.add_argument("channel_strip", type=Path)
    plugins.add_argument("--json", action="store_true", dest="as_json")

    parameters = commands.add_parser("parameters", help="Enumerate parameters for one plugin")
    parameters.add_argument("channel_strip", type=Path)
    parameters.add_argument("--plugin", required=True, type=int, dest="plugin_index")
    parameters.add_argument("--json", action="store_true", dest="as_json")
    parameter_roots = parameters.add_mutually_exclusive_group()
    parameter_roots.add_argument(
        "--metadata-root",
        action="append",
        type=Path,
        dest="metadata_roots",
        help="Search only this Logic Plug-In Settings root; may be repeated",
    )
    parameter_roots.add_argument(
        "--no-metadata",
        action="store_true",
        help="Return raw parameters without installed semantic metadata",
    )

    parameter_metadata = commands.add_parser(
        "parameter-metadata",
        help="Enumerate installed semantic metadata for one plugin",
    )
    parameter_metadata.add_argument("plugin_name")
    parameter_metadata.add_argument(
        "--metadata-root",
        action="append",
        type=Path,
        dest="metadata_roots",
        help="Search only this Logic Plug-In Settings root; may be repeated",
    )
    parameter_metadata.add_argument("--json", action="store_true", dest="as_json")

    return result


def _inspect(path: Path, as_json: bool) -> int:
    info = inspect_concert(path)
    payload = [
        {
            "name": patch.name,
            "error": patch.error,
            "channels": [
                {
                    "name": channel.get("Channel_name"),
                    "instance_id": channel.get("Channel_instID"),
                    "uuid": channel.get("UUID"),
                    "file": channel.get("Filename"),
                }
                for channel in patch.channels
            ],
        }
        for patch in info.patches
    ]
    if as_json:
        print(
            json.dumps(
                {
                    "concert": str(path),
                    "patches": payload,
                    "issues": [issue.__dict__ for issue in info.issues],
                },
                indent=2,
            )
        )
    else:
        for index, patch in enumerate(payload, start=1):
            print(f"{index}. {patch['name']} ({len(patch['channels'])} channels)")
            if patch["error"]:
                print(f"   ERROR: {patch['error']}")
    return 1 if info.issues else 0


def _validate(path: Path) -> int:
    issues = validate_concert(path)
    if not issues:
        print(f"OK: {path}")
        return 0
    for issue in issues:
        print(f"{issue.severity.upper()}: {issue.location}: {issue.message}")
    return 1 if any(issue.severity == "error" for issue in issues) else 0


def _catalog(args: argparse.Namespace) -> int:
    paths = scan_patch_bundles(args.root)
    manifests = [inspect_patch_manifest(path) for path in paths] if args.inspect else []
    if args.as_json:
        payload: dict[str, object] = {"root": str(args.root), "patches": [str(path) for path in paths]}
        if args.inspect:
            payload["manifests"] = [manifest.as_dict() for manifest in manifests]
        print(json.dumps(payload, indent=2))
    else:
        print(f"Patch bundles: {len(paths)}")
        if args.inspect:
            print(f"Valid manifests: {len(manifests)}")
    return 0


def _compose(args: argparse.Namespace) -> int:
    manifest = compose_patch(
        args.sources,
        args.output,
        name=args.name,
        instance_ids=args.instance_ids,
    )
    print(f"Created {manifest.path} with {len(manifest.channels)} channels.")
    return 0


def _patches(args: argparse.Namespace) -> int:
    entries = enumerate_patches(args.concert)
    if args.as_json:
        print(json.dumps([entry.as_dict() for entry in entries], indent=2))
    else:
        for entry in entries:
            suffix = f" — ERROR: {entry.error}" if entry.error else ""
            print(f"{entry.index}. {entry.name} — {entry.path}{suffix}")
    return 1 if any(entry.error for entry in entries) else 0


def _channel_strips(args: argparse.Namespace) -> int:
    entries = enumerate_channel_strips(args.patch)
    if args.as_json:
        print(json.dumps([entry.as_dict() for entry in entries], indent=2))
    else:
        for entry in entries:
            print(f"{entry.index}. {entry.name} — {entry.path}")
    return 0


def _plugins(args: argparse.Namespace) -> int:
    entries = enumerate_plugins(args.channel_strip)
    if args.as_json:
        print(json.dumps([entry.as_dict() for entry in entries], indent=2))
    else:
        for entry in entries:
            name = entry.name or "Unknown plugin"
            preset = f" — {entry.preset_name}" if entry.preset_name else ""
            print(f"{entry.index}. [{entry.role}] {name}{preset}")
    return 0


def _parameters(args: argparse.Namespace) -> int:
    metadata_roots = () if args.no_metadata else args.metadata_roots
    entries = enumerate_parameters(
        args.channel_strip,
        args.plugin_index,
        metadata_roots=metadata_roots,
    )
    if args.as_json:
        print(json.dumps([entry.as_dict() for entry in entries], indent=2))
    else:
        for entry in entries:
            label = entry.name or entry.identifier or f"byte offset {entry.byte_offset}"
            display = entry.display_value
            if display is None:
                display = str(entry.value) if entry.value is not None else entry.raw_bits or "unavailable"
            print(f"{entry.address}. {label} = {display}")
    return 0


def _parameter_metadata(args: argparse.Namespace) -> int:
    table = discover_parameter_metadata(args.plugin_name, args.metadata_roots)
    if table is None:
        if args.as_json:
            print("null")
        else:
            print(f"No installed parameter metadata found for {args.plugin_name!r}.")
        return 1
    if args.as_json:
        print(json.dumps(table.as_dict(), indent=2))
    else:
        print(f"{table.plugin_name} — {table.path}")
        for entry in table.entries:
            print(f"{entry.parameter_id}. {entry.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect":
            return _inspect(args.concert, args.as_json)
        if args.command == "validate":
            return _validate(args.concert)
        if args.command == "catalog":
            return _catalog(args)
        if args.command == "compose":
            return _compose(args)
        if args.command == "patches":
            return _patches(args)
        if args.command == "channel-strips":
            return _channel_strips(args)
        if args.command == "plugins":
            return _plugins(args)
        if args.command == "parameters":
            return _parameters(args)
        if args.command == "parameter-metadata":
            return _parameter_metadata(args)
    except (ConcertFormatError, OSError, TypeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
