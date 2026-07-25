# concert-tools

Experimental Python utilities for inspecting, validating, and safely transforming Apple MainStage concert bundles.

The project exposes deterministic file-format primitives without owning setlist, scheduling, vendor-library,
user-interface, or personal-template policy. Interactive harnesses and headless workflow applications can compose
the same operations.

> [!CAUTION]
> The concert and OCuA/UCuA formats are undocumented and may change. Back up performance files and validate generated concerts in MainStage before using them live.

## Capabilities

- Inspect patch names, layers, UUIDs, and instance IDs.
- Validate channel UUIDs against embedded `.cst` identities.
- Validate outer and embedded mapping targets.
- Reject path traversal, symlinks, and binary-plist objects shared with unrelated settings.
- Clone a multi-layer patch with fresh UUIDs and concert-unique instance IDs.
- Remap every known `kGInstIDKey` reference.
- Read or rewrite one caller-selected text control, with an optional caller-selected embedded layout copy.
- Discover arbitrary `.patch` bundles and inspect their channel manifests.
- Combine compatible pre-positioned component patches into a standalone patch.
- Independently enumerate a concert's patches, a patch's channel strips, or a channel strip's plugins.
- Enumerate declared native PST float slots or query named Audio Unit parameters for one selected plugin.
- Discover installed Logic parameter-name tables and enrich native PST values without bundling Apple metadata.

## Requirements

- macOS with Python 3.11 or newer.
- The `swift` command and macOS frameworks when editing attributed text or querying Audio Unit parameters.
- Compatible MainStage concert, patch, and channel-strip bundles for the requested operations.

## Install for development

```zsh
python3 -m venv .venv
.venv/bin/pip install '.[dev]'
```

## CLI

```zsh
concert-tools inspect "Performance.concert"
concert-tools inspect "Performance.concert" --json
concert-tools validate "Performance.concert"
concert-tools catalog "/path/to/patch/library"
concert-tools catalog "/path/to/patch/library" --inspect --json
concert-tools patches "Performance.concert" --json
concert-tools channel-strips "Patch.patch" --json
concert-tools plugins "Piano Layer.cst" --json
concert-tools parameters "Piano Layer.cst" --plugin 2 --json
concert-tools parameter-metadata "Channel EQ" --json
concert-tools compose \
  "Piano Position 1.patch" \
  "Pad Position 2.patch" \
  "Organ Position 3.patch" \
  --name "Piano + Pad + Organ" \
  --output "Piano + Pad + Organ.patch"
```

`catalog` is vendor-neutral: it discovers patch bundles, and `--inspect` validates their names, channels,
setting files, UUIDs, and instance IDs. Vendor and workflow conventions belong in downstream applications.

`compose` accepts one or more component patches that were built against the same screen-control layout. It
combines every channel strip, allocates fresh instance IDs, assigns fresh channel UUIDs, merges shared mappings,
and emits one standalone `.patch` bundle. Source order is preserved. The command refuses incompatible mapping
graphs, ambiguous host archives, symlinks, duplicate identities, and existing output paths.

The four enumeration commands are intentionally independent and return only one immediate level:

- `patches` accepts a `.concert` bundle.
- `channel-strips` accepts one `.patch` bundle.
- `plugins` accepts one `.cst` channel-strip setting.
- `parameters` accepts one `.cst` and the one-based plugin index returned by `plugins`.

Plugin enumeration reports file order, MainStage slot, role, preset format, displayed name, preset filename, and
Audio Unit component codes where available. For native PST plugins, parameter enumeration reads only the float-slot
count declared by the validated PST header and reports each used slot's stable index, byte offset, raw bits, and
current value. It then looks for the plugin's installed `CSParameterOrder.plist`, first in the user's Logic settings
and then in the system Logic settings. Validated metadata supplies `logic:<id>` identifiers and names using the
observed native mapping `PST index = Logic parameter ID + 1`. Unknown slots remain raw, the known unused sentinel is
omitted, and plugin-specific tail data is not interpreted as parameters. Use `--no-metadata` for raw output or one
or more `--metadata-root` arguments to use explicit roots.

`parameter-metadata` is an independent call that accepts a plugin display name and returns the selected installed
table without reading a concert. User tables take precedence over system tables. Concert Tools does not copy or
ship Apple parameter tables; it consumes the metadata installed on the current Mac and safely falls back when no
table exists.

For Audio Unit property-list states, parameter enumeration instantiates only the selected component in a short-lived
Swift helper process, applies its state, and reports the parameter tree's stable address, identifier, key path, name,
current value, range, unit, flags, and display string. Unsupported or malformed states fail explicitly; the command
never assigns guessed semantic names to raw byte offsets.

## Library

```python
from pathlib import Path
from concert_tools import clone_patch, rewrite_patch_text, validate_concert

clone_patch(
    Path("Source.patch"),
    Path("Clone.patch"),
    name="Clone",
    instance_ids=[100, 101],
)
rewrite_patch_text(
    Path("Clone.patch"),
    "\x01IDENTITY:Caller Selected Control",
    "Replacement text",
    embedded_font_size=72.0,
)

issues = validate_concert(Path("Performance.concert"))
```

See [docs/format-notes.md](docs/format-notes.md) for the reverse-engineered structures and safety invariants, and
[docs/library-catalogs.md](docs/library-catalogs.md) for the extension boundary used by patch-library applications.
The CST plugin scanner was informed by the MIT-licensed LogicFiles format research; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Repository hygiene

Concert, patch, and channel-strip files are ignored by Git. Do not commit commercial templates, plugin content, performance files, or API credentials. Tests build a complete synthetic concert fixture in a temporary directory and include adversarial traversal, symlink, archive-sharing, and missing-mapping cases.

## Status

Alpha. The transformations have been exercised on real concert bundles and validated in MainStage, but the format is private and broader compatibility is not yet known.

MainStage is a trademark of Apple Inc. This project is unofficial and is not affiliated with or endorsed by Apple.

## License

MIT
