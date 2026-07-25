"""Independent read-only enumerators for concert bundle contents."""

from __future__ import annotations

import json
import math
import os
import plistlib
import struct
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .bundle import confined_child
from .concert import load_concert_data
from .errors import ConcertFormatError
from .manifest import inspect_patch_manifest
from .parameter_metadata import ParameterMetadataTable, discover_parameter_metadata


@dataclass(frozen=True)
class PatchEntry:
    """One patch directly contained by a concert."""

    index: int
    name: str
    path: Path
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


@dataclass(frozen=True)
class ChannelStripEntry:
    """One channel strip directly contained by a patch."""

    index: int
    name: str
    path: Path
    filename: str
    uuid: str
    instance_id: int

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


@dataclass(frozen=True)
class PluginEntry:
    """One user-visible plugin embedded in a channel-strip setting."""

    index: int
    slot: int
    role: str
    format: str
    name: str | None
    preset_name: str | None
    manufacturer: str | None = None
    type: str | None = None
    subtype: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PluginParameter:
    """One current plugin parameter or native PST float slot."""

    address: str
    identifier: str | None
    key_path: str | None
    name: str | None
    value: float | None
    minimum: float | None
    maximum: float | None
    unit: int | None
    unit_name: str | None
    display_value: str | None
    flags: int | None
    value_strings: tuple[str, ...] = ()
    source: str = "audio_unit"
    byte_offset: int | None = None
    raw_bits: str | None = None
    metadata_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["value_strings"] = list(self.value_strings)
        return payload


@dataclass(frozen=True)
class _PstPayload:
    payload: bytes
    declared_count: int
    magic: bytes


@dataclass(frozen=True)
class _ParsedPlugin:
    entry: PluginEntry
    component: tuple[int, int, int] | None
    state: dict[str, Any] | None
    pst: _PstPayload | None


def enumerate_patches(concert_path: Path) -> tuple[PatchEntry, ...]:
    """Return only the patches directly listed by ``concert_path``."""
    root, data = load_concert_data(Path(concert_path))
    entries: list[PatchEntry] = []
    for index, node in enumerate(data["nodes"], start=1):
        path = confined_child(root, node, suffix=".patch", kind="patch node")
        name = path.stem
        error: str | None = None
        data_path = path / "data.plist"
        if path.is_symlink() or data_path.is_symlink() or not path.is_dir():
            error = f"Not a regular patch bundle: {path}"
        else:
            try:
                with data_path.open("rb") as handle:
                    patch_data = plistlib.load(handle)
                candidate = patch_data["patch"]["engineNode"].get("name")
                if isinstance(candidate, str) and candidate:
                    name = candidate
            except (
                EOFError,
                KeyError,
                OSError,
                TypeError,
                ValueError,
                OverflowError,
                plistlib.InvalidFileException,
            ) as caught:
                error = f"Could not read patch identity: {caught}"
        entries.append(PatchEntry(index, name, path, error))
    return tuple(entries)


def enumerate_channel_strips(patch_path: Path) -> tuple[ChannelStripEntry, ...]:
    """Return only the channel strips directly listed by ``patch_path``."""
    manifest = inspect_patch_manifest(Path(patch_path))
    return tuple(
        ChannelStripEntry(
            index,
            channel.name,
            confined_child(
                manifest.path,
                channel.filename,
                suffix=".cst",
                kind="channel strip",
            ),
            channel.filename,
            channel.uuid,
            channel.instance_id,
        )
        for index, channel in enumerate(manifest.channels, start=1)
    )


def _uint32_le(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ConcertFormatError("Channel-strip integer field is out of bounds.")
    return struct.unpack_from("<I", data, offset)[0]


def _c_string(data: bytes, start: int, end: int) -> str | None:
    if start < 0 or end > len(data) or start >= end:
        return None
    raw = data[start:end].split(b"\0", 1)[0]
    if not raw:
        return None
    for encoding in ("utf-8", "mac_roman"):
        try:
            value = raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
        if value and all(character.isprintable() for character in value):
            return value
    return None


def _fourcc(value: int) -> str:
    raw = (value & 0xFFFFFFFF).to_bytes(4, "big")
    try:
        decoded = raw.decode("mac_roman")
    except UnicodeDecodeError:
        return f"0x{value & 0xFFFFFFFF:08x}"
    if all(0x20 <= ord(character) < 0x7F for character in decoded):
        return decoded
    return f"0x{value & 0xFFFFFFFF:08x}"


def _plist_component(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, dict) or value.get("$archiver") == "NSKeyedArchiver":
        return None
    manufacturer = value.get("manufacturer")
    component_type = value.get("type")
    subtype = value.get("subtype")
    values = (manufacturer, component_type, subtype)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in values):
        return None
    return manufacturer, component_type, subtype


def _xml_plist(block: bytes, start: int) -> tuple[dict[str, Any], int] | None:
    closing = block.find(b"</plist>", start)
    if closing < 0:
        return None
    end = closing + len(b"</plist>")
    try:
        value = plistlib.loads(block[start:end])
    except (ValueError, OverflowError, plistlib.InvalidFileException):
        return None
    return (value, end) if isinstance(value, dict) else None


def _binary_plist(block: bytes, start: int) -> tuple[dict[str, Any], int] | None:
    if start < 4:
        return None
    length = _uint32_le(block, start - 4)
    end = start + length
    if length < 40 or end > len(block):
        return None
    try:
        value = plistlib.loads(block[start:end])
    except (ValueError, OverflowError, plistlib.InvalidFileException):
        return None
    return (value, end) if isinstance(value, dict) else None


def _role(role_byte: int | None, component_type: int | None, *, first_plugin: bool) -> str:
    if role_byte is not None:
        if role_byte & 0x08:
            return "instrument"
        if role_byte & 0x02:
            return "midi_effect"
        return "audio_effect"
    component_code = _fourcc(component_type) if component_type is not None else None
    if component_code in {"aumu", "aumf"}:
        return "instrument" if component_code == "aumu" else "audio_effect"
    if component_code == "aumi":
        return "midi_effect"
    return "instrument" if first_plugin else "audio_effect"


def _plugin_payload(
    block: bytes,
) -> tuple[str, dict[str, Any] | None, tuple[int, int, int] | None, _PstPayload | None]:
    for magic in (b"GAMETSPP", b"PPSTEMAG"):
        search_from = 0
        while True:
            magic_at = block.find(magic, search_from)
            if magic_at < 0:
                break
            start = magic_at - 12
            if start >= 0 and start + 24 <= len(block):
                size = _uint32_le(block, start)
                if size >= 24 and start + size <= len(block):
                    return (
                        "pst",
                        None,
                        None,
                        _PstPayload(
                            payload=block[start + 24 : start + size],
                            declared_count=_uint32_le(block, start + 8),
                            magic=magic,
                        ),
                    )
            search_from = magic_at + 1

    xml_at = block.find(b"<?xml")
    if xml_at >= 0:
        parsed = _xml_plist(block, xml_at)
        if parsed is not None:
            state, _end = parsed
            component = _plist_component(state)
            if component is not None:
                return "aupreset", state, component, None

    search_from = 0
    while True:
        binary_at = block.find(b"bplist00", search_from)
        if binary_at < 0:
            break
        parsed = _binary_plist(block, binary_at)
        if parsed is not None:
            state, _end = parsed
            component = _plist_component(state)
            if component is not None:
                return "aupreset", state, component, None
        search_from = binary_at + 1
    return "unknown", None, None, None


def _parse_plugins(path: Path) -> tuple[_ParsedPlugin, ...]:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".cst":
        raise ConcertFormatError(f"Not a regular .cst file: {path}")
    data = path.read_bytes()
    if len(data) < 36 or data[:4] != b"OCuA":
        raise ConcertFormatError(f"Channel strip is not an OCuA container: {path}")
    header_size = _uint32_le(data, 28) + 36
    if header_size < 36 or header_size > len(data):
        raise ConcertFormatError(f"Channel strip has an invalid OCuA header: {path}")

    plugins: list[_ParsedPlugin] = []
    position = header_size
    while position + 4 <= len(data) and data[position : position + 4] == b"UCuA":
        if position + 36 > len(data):
            raise ConcertFormatError(f"Channel strip contains a truncated UCuA block: {path}")
        block_size = _uint32_le(data, position + 28) + 36
        end = position + block_size
        if block_size <= 36 or end > len(data):
            raise ConcertFormatError(f"Channel strip contains an invalid UCuA block: {path}")
        block = data[position:end]
        slot = struct.unpack_from("<H", block, 18)[0]
        preset_name = _c_string(block, 50, 110)
        display_name = _c_string(block, 156, 168)
        setting_format, state, component, pst = _plugin_payload(block)
        role_byte = block[187] if len(block) > 187 and (preset_name or display_name) else None

        # MainStage uses user-visible slots above 11, unlike the Logic convention.
        # A printable displayed plugin or preset name is the stable distinction from
        # the high-slot keyed-archive metadata blocks.
        recognized_preset = (
            Path(preset_name).suffix.casefold() in {".pst", ".aupreset", ".exs"}
            if preset_name is not None
            else False
        )
        if recognized_preset or setting_format in {"pst", "aupreset"}:
            if setting_format == "unknown" and preset_name is not None:
                setting_format = Path(preset_name).suffix.removeprefix(".").casefold()
            component_type = component[1] if component is not None else None
            entry = PluginEntry(
                index=len(plugins) + 1,
                slot=slot,
                role=_role(role_byte, component_type, first_plugin=not plugins),
                format=setting_format,
                name=display_name,
                preset_name=preset_name,
                manufacturer=_fourcc(component[0]) if component is not None else None,
                type=_fourcc(component[1]) if component is not None else None,
                subtype=_fourcc(component[2]) if component is not None else None,
            )
            plugins.append(_ParsedPlugin(entry, component, state, pst))
        position = end

    return tuple(plugins)


def enumerate_plugins(channel_strip_path: Path) -> tuple[PluginEntry, ...]:
    """Return only the user-visible plugins in ``channel_strip_path``."""
    return tuple(plugin.entry for plugin in _parse_plugins(Path(channel_strip_path)))


SWIFT_AU_PARAMETER_ENUMERATOR = r"""
import Foundation
import AudioToolbox

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

guard CommandLine.arguments.count == 5 else { fail("Invalid helper arguments.") }
let stateURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard let manufacturer = UInt32(CommandLine.arguments[2]),
      let componentType = UInt32(CommandLine.arguments[3]),
      let subtype = UInt32(CommandLine.arguments[4]) else {
    fail("Invalid Audio Unit component identifier.")
}

let stateData: Data
do { stateData = try Data(contentsOf: stateURL) }
catch { fail("Could not read saved Audio Unit state: \(error)") }

let state: [String: Any]
do {
    let value = try PropertyListSerialization.propertyList(from: stateData, options: [], format: nil)
    guard let dictionary = value as? [String: Any] else { fail("Audio Unit state is not a dictionary.") }
    state = dictionary
} catch { fail("Could not decode saved Audio Unit state: \(error)") }

let description = AudioComponentDescription(
    componentType: componentType,
    componentSubType: subtype,
    componentManufacturer: manufacturer,
    componentFlags: 0,
    componentFlagsMask: 0
)

let timeout = DispatchSource.makeTimerSource(queue: .global())
timeout.schedule(deadline: .now() + 20)
timeout.setEventHandler { fail("Audio Unit instantiation timed out.") }
timeout.resume()

AUAudioUnit.instantiate(with: description, options: [.loadInProcess]) { unit, error in
    timeout.cancel()
    guard let unit else { fail("Could not instantiate Audio Unit: \(error?.localizedDescription ?? "unknown error")") }
    unit.fullState = state
    let parameters = unit.parameterTree?.allParameters ?? []
    let payload: [[String: Any]] = parameters.map { parameter in
        let value = parameter.value
        var item: [String: Any] = [
            "address": String(parameter.address),
            "identifier": parameter.identifier,
            "key_path": parameter.keyPath,
            "name": parameter.displayName,
            "value": Double(value),
            "minimum": Double(parameter.minValue),
            "maximum": Double(parameter.maxValue),
            "unit": Int(parameter.unit.rawValue),
            "flags": Int(parameter.flags.rawValue),
        ]
        if let unitName = parameter.unitName { item["unit_name"] = unitName }
        let display = parameter.string(fromValue: nil)
        if !display.isEmpty { item["display_value"] = display }
        if let strings = parameter.valueStrings { item["value_strings"] = strings }
        return item
    }
    do {
        let output = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        FileHandle.standardOutput.write(output)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(0)
    } catch { fail("Could not encode Audio Unit parameters: \(error)") }
}

dispatchMain()
"""


def _query_au_parameters(
    state: dict[str, Any],
    component: tuple[int, int, int],
) -> list[dict[str, Any]]:
    if sys.platform != "darwin":
        raise ConcertFormatError("Named Audio Unit parameters can only be queried on macOS.")
    with tempfile.TemporaryDirectory(prefix="concert-tools-au-") as raw_work:
        work = Path(raw_work)
        state_path = work / "state.plist"
        state_path.write_bytes(plistlib.dumps(state, fmt=plistlib.FMT_BINARY, sort_keys=False))
        environment = os.environ.copy()
        environment.setdefault("CLANG_MODULE_CACHE_PATH", str(work / "swift-module-cache"))
        try:
            result = subprocess.run(
                [
                    "swift",
                    "-",
                    str(state_path),
                    str(component[0]),
                    str(component[1]),
                    str(component[2]),
                ],
                input=SWIFT_AU_PARAMETER_ENUMERATOR,
                text=True,
                check=True,
                capture_output=True,
                env=environment,
                timeout=30,
            )
        except FileNotFoundError as error:
            raise ConcertFormatError("The macOS 'swift' command is required to query Audio Units.") from error
        except subprocess.TimeoutExpired as error:
            raise ConcertFormatError("Audio Unit parameter enumeration timed out.") from error
        except subprocess.CalledProcessError as error:
            message = error.stderr.strip() or "Audio Unit parameter helper failed."
            raise ConcertFormatError(message) from error
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ConcertFormatError("Audio Unit parameter helper returned invalid JSON.") from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ConcertFormatError("Audio Unit parameter helper returned an invalid structure.")
    return payload


def _required_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConcertFormatError(f"Audio Unit parameter {field} is not numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise ConcertFormatError(f"Audio Unit parameter {field} is not finite.")
    return result


_PST_SENTINEL = b"\xca\xf2\x49\x71"


def _enumerate_pst_parameters(pst: _PstPayload) -> tuple[PluginParameter, ...]:
    if pst.magic != b"GAMETSPP":
        raise ConcertFormatError(
            f"Native PST byte order {pst.magic.decode('ascii', errors='replace')!r} is unsupported."
        )
    available_count = len(pst.payload) // 4
    if pst.declared_count > available_count:
        raise ConcertFormatError(
            f"Native PST declares {pst.declared_count} float slots but contains only {available_count}."
        )

    parameters: list[PluginParameter] = []
    for index in range(pst.declared_count):
        byte_offset = index * 4
        raw = pst.payload[byte_offset : byte_offset + 4]
        if raw == _PST_SENTINEL:
            continue
        value = struct.unpack("<f", raw)[0]
        parameters.append(
            PluginParameter(
                address=f"pst:{index}",
                identifier=None,
                key_path=None,
                name=None,
                value=value if math.isfinite(value) else None,
                minimum=None,
                maximum=None,
                unit=None,
                unit_name=None,
                display_value=None,
                flags=None,
                source="native_pst",
                byte_offset=byte_offset,
                raw_bits=f"0x{int.from_bytes(raw, 'little'):08x}",
            )
        )
    return tuple(parameters)


def enrich_parameters(
    parameters: Iterable[PluginParameter],
    metadata: ParameterMetadataTable,
    *,
    pst_index_offset: int = 1,
) -> tuple[PluginParameter, ...]:
    """Apply host names to native PST slots while preserving every raw value."""
    if isinstance(pst_index_offset, bool) or not isinstance(pst_index_offset, int) or pst_index_offset < 0:
        raise ValueError("PST index offset must be a non-negative integer.")
    names = {entry.parameter_id: entry.name for entry in metadata.entries}
    enriched: list[PluginParameter] = []
    for parameter in parameters:
        if (
            parameter.source != "native_pst"
            or parameter.byte_offset is None
            or parameter.byte_offset < 0
            or parameter.byte_offset % 4
        ):
            enriched.append(parameter)
            continue
        pst_index = parameter.byte_offset // 4
        parameter_id = pst_index - pst_index_offset
        name = names.get(parameter_id)
        if name is None:
            enriched.append(parameter)
            continue
        enriched.append(
            replace(
                parameter,
                identifier=f"logic:{parameter_id}",
                name=name,
                metadata_source=str(metadata.path),
            )
        )
    return tuple(enriched)


def enumerate_parameters(
    channel_strip_path: Path,
    plugin_index: int,
    *,
    metadata_roots: Iterable[Path] | None = None,
) -> tuple[PluginParameter, ...]:
    """Return current parameters for one selected plugin.

    ``plugin_index`` is the one-based index returned by :func:`enumerate_plugins`.
    The operation is deliberately independent: it reparses only the supplied CST and
    reads or instantiates only the selected plugin. Native PST results preserve stable
    float indices and byte offsets. Installed host metadata supplies names when present;
    absent names, ranges, and units remain null rather than being guessed.
    """
    if isinstance(plugin_index, bool) or not isinstance(plugin_index, int) or plugin_index < 1:
        raise ValueError("Plugin index must be a positive integer.")
    plugins = _parse_plugins(Path(channel_strip_path))
    if plugin_index > len(plugins):
        raise ValueError(f"Plugin index {plugin_index} is out of range; found {len(plugins)} plugins.")
    selected = plugins[plugin_index - 1]
    if selected.pst is not None:
        parameters = _enumerate_pst_parameters(selected.pst)
        if selected.entry.name is None:
            return parameters
        metadata = discover_parameter_metadata(selected.entry.name, metadata_roots)
        return enrich_parameters(parameters, metadata) if metadata is not None else parameters
    if selected.component is None or selected.state is None:
        label = selected.entry.name or selected.entry.preset_name or f"plugin {plugin_index}"
        raise ConcertFormatError(
            f"{label!r} does not contain a supported native PST or Audio Unit property-list state; "
            "current parameters are unavailable."
        )

    raw_parameters = _query_au_parameters(selected.state, selected.component)
    parameters: list[PluginParameter] = []
    for item in raw_parameters:
        strings = item.get("value_strings", [])
        if not isinstance(strings, list) or any(not isinstance(value, str) for value in strings):
            raise ConcertFormatError("Audio Unit parameter value_strings is not a string array.")
        required_strings = {key: item.get(key) for key in ("address", "identifier", "key_path", "name")}
        if any(not isinstance(value, str) for value in required_strings.values()):
            raise ConcertFormatError("Audio Unit parameter identity fields must be strings.")
        unit = item.get("unit")
        flags = item.get("flags")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (unit, flags)):
            raise ConcertFormatError("Audio Unit parameter unit and flags must be integers.")
        unit_name = item.get("unit_name")
        display_value = item.get("display_value")
        if unit_name is not None and not isinstance(unit_name, str):
            raise ConcertFormatError("Audio Unit parameter unit_name must be a string or null.")
        if display_value is not None and not isinstance(display_value, str):
            raise ConcertFormatError("Audio Unit parameter display_value must be a string or null.")
        parameters.append(
            PluginParameter(
                source="audio_unit",
                address=required_strings["address"],
                value=_required_number(item.get("value"), "value"),
                identifier=required_strings["identifier"],
                key_path=required_strings["key_path"],
                name=required_strings["name"],
                minimum=_required_number(item.get("minimum"), "minimum"),
                maximum=_required_number(item.get("maximum"), "maximum"),
                unit=unit,
                unit_name=unit_name,
                display_value=display_value,
                flags=flags,
                value_strings=tuple(strings),
            )
        )
    return tuple(parameters)
