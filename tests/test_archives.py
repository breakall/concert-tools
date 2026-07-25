import plistlib

from concert_tools.archives import instance_id_values, rewrite_mapping_store


def archive(values: list[int]) -> bytes:
    payload = {"$objects": [{"kGInstIDKey": value} for value in values]}
    return plistlib.dumps(payload, fmt=plistlib.FMT_BINARY, sort_keys=False)


def test_rewrite_mapping_store() -> None:
    store = {"control": archive([136, 140, 136]), "unrelated": b"raw"}
    original = store["control"]
    assert rewrite_mapping_store(store, {136: 156, 140: 160}) == 3
    assert instance_id_values(plistlib.loads(store["control"])) == [156, 160, 156]
    assert len(store["control"]) == len(original)
    changed = [
        index for index, pair in enumerate(zip(original, store["control"], strict=True)) if pair[0] != pair[1]
    ]
    assert changed
    assert len(changed) <= 3
    assert store["unrelated"] == b"raw"
