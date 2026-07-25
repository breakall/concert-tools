# Concert format notes

This document records observed behavior, not an official Apple specification. The implementation rejects ambiguous structures instead of guessing.

## Bundle structure

A `.concert` is a directory package containing `Concert.patch`. Its `data.plist` has an ordered `nodes` array naming child `.patch` bundles. A song patch has its own `data.plist` and one `.cst` channel-strip setting per channel.

## Channel identity

Each channel has two relevant identifiers:

- `UUID`, also embedded as 16 raw bytes in its `.cst` file.
- `Channel_instID`, targeted by screen-control mappings through `kGInstIDKey`.

Cloning a patch requires fresh UUIDs and concert-unique instance IDs. Changing only `data.plist` produces apparently valid patches whose controls show broken-mapping `!` indicators.

## Mapping copies

Instance-ID mapping targets occur in two places:

1. Per-control NSKeyedArchives in `engineNode.parameterMappingMap.storeDict`.
2. A large NSKeyedArchive embedded in one channel's OCuA/UCuA data.

Both copies must be remapped. In the large archive, this project edits existing binary-plist integer objects in place. Re-serializing that archive can substantially rewrite/deduplicate its binary object table.

Binary plists may also deduplicate equal scalar objects. Before changing an integer object in place, the implementation walks the serialized object-reference graph and verifies that every reference to that integer is a dictionary value whose key is `kGInstIDKey`. If an unrelated setting shares the integer, the edit is rejected.

## Layout text copies

In the template that motivated the text-edit primitive, two large text controls were duplicated:

1. Archived attributed strings in `uiPluginDataDict`.
2. An embedded workspace archive inside a `.cst` file.

The observed layout identifies these controls by 96-point and 48-point font records. The generic primitive instead
requires callers to select the control identifier and, when applicable, the embedded font size. Concert Tools does
not assign template-specific meaning to either value.

## OCuA/UCuA plist chunks

Concert Tools walks OCuA and UCuA containers using their declared block lengths. It does not treat an arbitrary
`bplist00` byte sequence inside plug-in state as a MainStage system archive. Observed editable system archives
begin 56 bytes into a UCuA block and end 16 bytes before the block boundary. Immediately before the payload are
little-endian lengths:

- At payload offset `-4`: payload length.
- At payload offset `-28`: payload length plus 36 bytes.

Older exported library patches have also been observed without the 16-byte post-plist identity. In that form,
offset `-4` is still the payload length, offset `-28` is payload length plus 20, and the plist ends at the UCuA
block boundary. Transformations preserve whichever convention the source block uses.

In current saved concerts, a 16-byte identity follows the payload before the next `UCuA` marker. When a
transformed plist changes size, both preceding length fields must be updated.

Per-control mapping archives in `data.plist` are not reserialized. Their existing binary-plist integer objects are
edited in place only after the object-reference graph proves that every reference is owned by a `kGInstIDKey`
dictionary value. This preserves all unknown archive bytes and refuses edits when plist scalar deduplication makes
an integer shared with an unrelated property.

## Channel-strip plugin enumeration

Channel-strip `.cst` files are OCuA containers with length-delimited UCuA blocks. Each named plugin block carries a
slot number at bytes 18–19, a preset filename at bytes 50–109, a short display name at bytes 156–167, and a role
byte at 187. Role bit `0x08` identifies an instrument and bit `0x02` identifies a MIDI effect; neither bit denotes
an audio effect.

MainStage may place user-visible audio and MIDI plugins in slots above 11, so a Logic-oriented high-slot cutoff is
not valid for concerts. Concert Tools instead requires a recognized PST/AU payload or a recognized preset filename
extension and ignores anonymous keyed-archive metadata blocks. PST payloads have a 24-byte envelope containing the
`GAMETSPP` or `PPSTEMAG` marker. Audio Unit presets are XML or binary property lists carrying integer
`manufacturer`, `type`, and `subtype` component codes.

Native PST headers declare a bounded number of little-endian float slots. Concert Tools validates that the declared
count fits within the PST payload, enumerates only those slots, omits the observed unused sentinel bytes
`ca f2 49 71`, and leaves any remaining plugin-specific tail uninterpreted. Results expose stable slot indices, byte
offsets, raw bits, and values. They intentionally leave semantic names, ranges, and units null because those are not
encoded in the observed native states and vary by plugin.

Logic installations may provide `CSParameterOrder.plist` beside a plugin's saved settings. Its
`ControlSurfaceParameterOrder` array contains strings whose leading integer is a Logic parameter ID and whose
remainder is the host display name. Concert Tools validates the plist, ignores `-1` page placeholders, preserves
ordered repeated IDs, rejects malformed entries, traversal names, and symlinks, and ignores unrelated future keys.
When an ID appears more than once, the final (usually complete-list) name is used for enrichment. User metadata
roots take precedence over system roots.

Across the installed Channel EQ, SilverVerb, Gain, Chorus, Tremolo, and AutoFilter tables, comparison with saved
MainStage PST state consistently maps `PST index = Logic parameter ID + 1`. Enrichment applies that explicit offset
only to matching native PST slots. Entries absent from the table remain unchanged; ranges, units, display-value
conversion, and musical interpretation are not inferred.

For Audio Unit property-list states, a short-lived macOS helper instantiates the component, assigns its saved state,
and reads `AUParameterTree.allParameters`, which supplies named parameters and metadata.

## Relationship to LogicFiles

[LogicFiles](https://github.com/CraigStuntz/LogicFiles) documents and implements related Logic Pro `.cst`, `.patch`,
`.pst`, and `.aupreset` formats. Its lossless handling of unknown blocks and length-delimited OCuA/UCuA parsing are
useful independent safety references for this project.

MainStage song patches are not interchangeable with LogicFiles' current Logic patch model: observed MainStage
patches use arbitrarily named channel-strip files and describe them in `data.plist`, while that model requires a
Logic `#Root.cst`. MainStage CST files also contain system and layout blocks that can cause Logic-oriented plug-in
role heuristics to report incomplete instrument information. Concert Tools therefore does not currently depend on
LogicFiles and keeps its MainStage-specific graph transformations narrowly scoped.

## Filesystem safety

Names read from bundle plists are treated as untrusted metadata. Patch nodes and channel-strip settings must be single-component relative names with the expected suffix. Absolute paths, traversal components, and symlinks are rejected before copying, reading, writing, or deleting files.
