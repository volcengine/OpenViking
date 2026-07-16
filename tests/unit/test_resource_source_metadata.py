from openviking.resource.source_metadata import (
    build_source_metadata,
    decode_source_metadata,
    encode_source_metadata,
    fingerprints_match,
    normalize_source_fingerprint,
)


def _fingerprint(sha: str = "a" * 64) -> dict[str, object]:
    return {
        "source_kind": "temp_upload",
        "source_sha256": sha,
        "source_size": 42,
    }


def test_source_metadata_round_trip_and_match():
    fingerprint = normalize_source_fingerprint(_fingerprint())
    metadata = build_source_metadata(fingerprint)

    decoded = decode_source_metadata(encode_source_metadata(metadata))

    assert decoded == metadata
    assert decoded["source_revision"] == f"sha256:{'a' * 64}"
    assert fingerprints_match(decoded, fingerprint)
    assert not fingerprints_match(decoded, normalize_source_fingerprint(_fingerprint("b" * 64)))


def test_unfingerprinted_write_cannot_match_a_temp_upload():
    metadata = build_source_metadata(None)

    decoded = decode_source_metadata(encode_source_metadata(metadata))

    assert decoded == metadata
    assert not fingerprints_match(decoded, normalize_source_fingerprint(_fingerprint()))
