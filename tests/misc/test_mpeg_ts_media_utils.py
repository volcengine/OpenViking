from openviking.parse.parsers.media.utils import (
    MPEG_TS_PACKET_SIZE,
    MPEG_TS_PROBE_BYTES,
    get_media_type,
    is_mpeg_ts,
    read_mpeg_ts_probe,
)


def mpeg_ts_probe() -> bytes:
    content = bytearray(MPEG_TS_PROBE_BYTES)
    for offset in range(0, MPEG_TS_PROBE_BYTES, MPEG_TS_PACKET_SIZE):
        content[offset] = 0x47
    return bytes(content)


def test_is_mpeg_ts_requires_sync_bytes_at_packet_boundaries():
    assert is_mpeg_ts(mpeg_ts_probe())

    invalid = bytearray(mpeg_ts_probe())
    invalid[MPEG_TS_PACKET_SIZE] = 0
    assert not is_mpeg_ts(bytes(invalid))


def test_read_mpeg_ts_probe_reads_only_probe_bytes(tmp_path):
    path = tmp_path / "video.ts"
    path.write_bytes(mpeg_ts_probe() + b"x" * 1024)

    assert read_mpeg_ts_probe(path) == mpeg_ts_probe()


def test_get_media_type_detects_local_mpeg_ts_by_magic_bytes(tmp_path):
    path = tmp_path / "video.ts"
    path.write_bytes(mpeg_ts_probe())

    assert get_media_type(str(path), None) == "video"


def test_get_media_type_does_not_classify_typescript_as_video(tmp_path):
    path = tmp_path / "source.ts"
    path.write_text("export const answer: number = 42;\n")

    assert get_media_type(str(path), None) is None


def test_get_media_type_does_not_classify_viking_ts_uri_by_path_prefix():
    assert get_media_type("viking://resources/video/2026/07/27/test.ts", None) is None


def test_get_media_type_treats_video_source_format_as_video():
    assert get_media_type(None, "video") == "video"
