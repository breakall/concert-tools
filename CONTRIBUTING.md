# Contributing

Contributions that document additional format variants, add synthetic fixtures, or strengthen validation are welcome.

Please do not submit copyrighted concert templates, commercial sound-library data, plugin presets, credentials, or other private performance files. Reduce discoveries to synthetic fixtures whenever possible.

Keep APIs as independent, deterministic MainStage primitives. Vendor conventions, control identifiers from a
particular template, setlist normalization, scheduling, delivery, recents, and user-interface policy belong in a
downstream harness or workflow repository.

Run before submitting a change:

```zsh
pytest
ruff check .
```

Format changes should be documented in `docs/format-notes.md`, including the observed application version and the invariant used to make the edit safe.
