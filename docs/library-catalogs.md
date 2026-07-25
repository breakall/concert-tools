# Patch library catalogs

Concert Tools provides portable bundle operations without owning library conventions.

## Generic layer

`concert_tools.catalog.scan_patch_bundles` discovers `.patch` packages without following symlinks or descending
into packages. It treats packages as opaque, so it is suitable for any vendor's library and remains fast over
large directory trees.

`concert_tools.manifest.inspect_patch_manifest` reads a single package and returns only reusable identity data:
the patch name and each channel strip's name, setting filename, UUID, and instance ID. It rejects malformed UUIDs,
non-integer IDs, unsafe filenames, symlinks, and missing `.cst` files.

## Downstream convention adapters

Vendor and workflow adapters belong in downstream projects. They may consume `scan_patch_bundles`,
`inspect_patch_manifest`, and `compose_patch`, but `concert-tools` never imports those adapters. This keeps product
naming rules, library layouts, recent-item policy, user interfaces, scheduling integrations, and personal template
assumptions out of the primitive layer.

## Composition

`concert_tools.composition.compose_patch` combines compatible, pre-positioned components without knowing their
vendor or selection workflow. It unions channels and controls, merges shared mapping arrays, picks the richest stored UI value,
and removes a redundant override only when every component carries it. The first output channel hosts the merged
mapping and metadata archives; later component hosts are reduced to UUID-only metadata, matching a native combine.

Composition assigns fresh channel UUIDs and either caller-provided or newly allocated instance IDs. It validates
that outer and embedded control sets agree, exactly one channel hosts the final embedded mappings, every output ID
is targeted, and every channel setting embeds its declared UUID exactly once.
