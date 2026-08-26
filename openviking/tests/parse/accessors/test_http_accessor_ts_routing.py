from openviking.parse.accessors.http_accessor import HTTPAccessor, URLType
from openviking.parse.parsers.media.utils import MPEG_TS_PACKET_SIZE, MPEG_TS_PROBE_BYTES


def mpeg_ts_probe() -> bytes:
    content = bytearray(MPEG_TS_PROBE_BYTES)
    for offset in range(0, MPEG_TS_PROBE_BYTES, MPEG_TS_PACKET_SIZE):
        content[offset] = 0x47
    return bytes(content)


def typescript_content() -> bytes:
    return b"export const answer: number = 42;\n"


def finalize_for_ts(accessor: HTTPAccessor, url: str, content: bytes) -> URLType:
    meta = accessor._finalize_download_metadata(
        url=url,
        initial_url_type=URLType.DOWNLOAD_VIDEO,
        initial_meta={"url": url, "detected_by": "extension", "extension": ".ts"},
        response_headers={},
        content=content,
    )
    assert meta["extension"] == ".ts"
    return meta["resolved_url_type"]


def test_finalize_downgrades_typescript_ts_url_to_text():
    accessor = HTTPAccessor()

    resolved = finalize_for_ts(
        accessor, "https://cdn.example.com/lib/source.ts", typescript_content()
    )

    assert resolved == URLType.DOWNLOAD_TXT


def test_finalize_keeps_mpeg_ts_video_for_real_stream():
    accessor = HTTPAccessor()

    resolved = finalize_for_ts(
        accessor, "https://cdn.example.com/lib/video.ts", mpeg_ts_probe()
    )

    assert resolved == URLType.DOWNLOAD_VIDEO


def test_finalize_downgrades_short_ts_payload_to_text():
    accessor = HTTPAccessor()

    resolved = finalize_for_ts(
        accessor, "https://cdn.example.com/lib/x.ts", b"\x47\x00\x01"
    )

    # Shorter than an MPEG-TS probe window: cannot be a valid stream.
    assert resolved == URLType.DOWNLOAD_TXT


def test_finalize_does_not_probe_non_ts_video_urls():
    accessor = HTTPAccessor()
    content = b"not really a video"

    meta = accessor._finalize_download_metadata(
        url="https://cdn.example.com/lib/movie.mp4",
        initial_url_type=URLType.DOWNLOAD_VIDEO,
        initial_meta={
            "url": "https://cdn.example.com/lib/movie.mp4",
            "detected_by": "extension",
            "extension": ".mp4",
        },
        response_headers={},
        content=content,
    )

    assert meta["resolved_url_type"] == URLType.DOWNLOAD_VIDEO
