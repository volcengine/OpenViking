import pytest

from openviking.parse.parsers.media.audio import AudioParser
from openviking.parse.registry import ParserRegistry


class FakeVikingFS:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def create_temp_uri(self) -> str:
        return "viking://temp/ac3-test"

    async def mkdir(self, _uri: str, exist_ok: bool = False) -> None:
        assert exist_ok is True

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content


@pytest.fixture
def fake_viking_fs(monkeypatch):
    fake = FakeVikingFS()
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: fake,
    )
    return fake


def test_parser_registry_routes_ac3_to_audio_parser():
    parser = ParserRegistry().get_parser_for_file("sample.ac3")

    assert isinstance(parser, AudioParser)


@pytest.mark.asyncio
async def test_audio_parser_accepts_ac3_syncword(tmp_path, fake_viking_fs):
    source = tmp_path / "sample.ac3"
    content = b"\x0b\x77ac3-data"
    source.write_bytes(content)

    result = await AudioParser().parse(source)

    assert result.parser_name == "AudioParser"
    assert result.source_format == "audio"
    assert result.meta == {"content_type": "audio", "format": "ac3"}
    assert any(uri.endswith("/sample.ac3") for uri in fake_viking_fs.files)
    assert content in fake_viking_fs.files.values()


@pytest.mark.asyncio
async def test_audio_parser_rejects_invalid_ac3_signature(tmp_path, fake_viking_fs):
    source = tmp_path / "invalid.ac3"
    source.write_bytes(b"not-ac3")

    with pytest.raises(
        ValueError,
        match=r"File signature does not match expected format \.ac3",
    ):
        await AudioParser().parse(source)
