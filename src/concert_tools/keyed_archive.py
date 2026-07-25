"""Small graph toolkit for composing NSKeyedArchive dictionaries and arrays."""

from __future__ import annotations

import copy
import plistlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .errors import ConcertFormatError


@dataclass
class KeyedArchive:
    value: dict[str, Any]
    objects: list[Any]

    @classmethod
    def loads(cls, blob: bytes) -> KeyedArchive:
        try:
            value = plistlib.loads(blob)
        except (EOFError, OverflowError, ValueError, plistlib.InvalidFileException) as error:
            raise ConcertFormatError("Invalid keyed archive.") from error
        if not isinstance(value, dict) or value.get("$archiver") != "NSKeyedArchiver":
            raise ConcertFormatError("Value is not an NSKeyedArchive.")
        objects = value.get("$objects")
        if not isinstance(objects, list) or not objects or objects[0] != "$null":
            raise ConcertFormatError("Keyed archive has an invalid object table.")
        return cls(value, objects)

    def dereference(self, reference: Any) -> Any:
        if not isinstance(reference, plistlib.UID):
            return reference
        if reference.data >= len(self.objects):
            raise ConcertFormatError("Keyed archive reference is out of range.")
        return self.objects[reference.data]

    def top_reference(self, key: str) -> plistlib.UID:
        top = self.value.get("$top")
        reference = top.get(key) if isinstance(top, dict) else None
        if not isinstance(reference, plistlib.UID):
            raise ConcertFormatError(f"Keyed archive has no {key!r} top reference.")
        self.dereference(reference)
        return reference

    def dictionary_entries(self, reference: plistlib.UID) -> list[tuple[plistlib.UID, plistlib.UID]]:
        value = self.dereference(reference)
        if not isinstance(value, dict):
            raise ConcertFormatError("Keyed archive object is not a Foundation dictionary.")
        keys, values = value.get("NS.keys"), value.get("NS.objects")
        if not isinstance(keys, list) or not isinstance(values, list) or len(keys) != len(values):
            raise ConcertFormatError("Foundation dictionary has invalid entries.")
        if any(not isinstance(item, plistlib.UID) for item in [*keys, *values]):
            raise ConcertFormatError("Foundation dictionary entries are not object references.")
        for item in [*keys, *values]:
            self.dereference(item)
        return list(zip(keys, values, strict=True))

    def array_items(self, reference: plistlib.UID) -> list[plistlib.UID]:
        value = self.dereference(reference)
        items = value.get("NS.objects") if isinstance(value, dict) else None
        if not isinstance(items, list) or any(not isinstance(item, plistlib.UID) for item in items):
            raise ConcertFormatError("Keyed archive object is not a Foundation array.")
        for item in items:
            self.dereference(item)
        return items

    def identity_name(self, reference: plistlib.UID) -> str:
        value = self.dereference(reference)
        string_ref = value.get("uuidString") if isinstance(value, dict) else None
        name = self.dereference(string_ref)
        if not isinstance(name, str) or not name:
            raise ConcertFormatError("Keyed archive identity has no string name.")
        return name


class ArchiveBuilder:
    """Import reachable keyed-archive objects while rebasing every UID."""

    def __init__(self) -> None:
        self.objects: list[Any] = ["$null"]
        self._imports: dict[tuple[int, int], plistlib.UID] = {}

    def add(self, value: Any) -> plistlib.UID:
        reference = plistlib.UID(len(self.objects))
        self.objects.append(copy.deepcopy(value))
        return reference

    def import_reference(self, archive: KeyedArchive, reference: plistlib.UID) -> plistlib.UID:
        archive.dereference(reference)
        key = (id(archive), reference.data)
        if key in self._imports:
            return self._imports[key]
        imported = plistlib.UID(len(self.objects))
        self._imports[key] = imported
        self.objects.append(None)
        self.objects[imported.data] = self._import_value(archive, archive.objects[reference.data])
        return imported

    def _import_value(self, archive: KeyedArchive, value: Any) -> Any:
        if isinstance(value, plistlib.UID):
            return self.import_reference(archive, value)
        if isinstance(value, dict):
            return {key: self._import_value(archive, child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._import_value(archive, child) for child in value]
        if isinstance(value, tuple):
            return tuple(self._import_value(archive, child) for child in value)
        return copy.deepcopy(value)

    def foundation_array(
        self, items: Iterable[plistlib.UID], template: tuple[KeyedArchive, plistlib.UID]
    ) -> plistlib.UID:
        archive, reference = template
        source = archive.dereference(reference)
        if not isinstance(source, dict) or not isinstance(source.get("$class"), plistlib.UID):
            raise ConcertFormatError("Foundation array template is invalid.")
        return self.add(
            {
                "NS.objects": list(items),
                "$class": self.import_reference(archive, source["$class"]),
            }
        )

    def foundation_dictionary(
        self,
        entries: Iterable[tuple[plistlib.UID, plistlib.UID]],
        template: tuple[KeyedArchive, plistlib.UID],
    ) -> plistlib.UID:
        archive, reference = template
        source = archive.dereference(reference)
        if not isinstance(source, dict) or not isinstance(source.get("$class"), plistlib.UID):
            raise ConcertFormatError("Foundation dictionary template is invalid.")
        pairs = list(entries)
        return self.add(
            {
                "NS.keys": [key for key, _value in pairs],
                "NS.objects": [value for _key, value in pairs],
                "$class": self.import_reference(archive, source["$class"]),
            }
        )

    def dumps(self, *, top_key: str, top_reference: plistlib.UID) -> bytes:
        return plistlib.dumps(
            {
                "$version": 100000,
                "$archiver": "NSKeyedArchiver",
                "$top": {top_key: top_reference},
                "$objects": self.objects,
            },
            fmt=plistlib.FMT_BINARY,
            sort_keys=False,
        )


def rewrite_decoded_mapping_ids(value: Any, replacements: dict[int, int]) -> int:
    """Rewrite only dictionary values explicitly owned by ``kGInstIDKey``."""
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "kGInstIDKey" and isinstance(child, int) and not isinstance(child, bool):
                if child in replacements:
                    value[key] = replacements[child]
                    changed += 1
            else:
                changed += rewrite_decoded_mapping_ids(child, replacements)
    elif isinstance(value, list):
        for child in value:
            changed += rewrite_decoded_mapping_ids(child, replacements)
    return changed
