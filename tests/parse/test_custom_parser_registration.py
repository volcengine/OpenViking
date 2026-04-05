import json

import pytest

from openviking.parse.parsers.word import WordParser
from openviking.parse.registry import ParserRegistry
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


def _custom_parser_config(
    class_path: str = "tests.utils.custom_parser_samples.SampleDocxParser",
    *,
    extensions: list[str] | None = None,
    kwargs: dict | None = None,
) -> OpenVikingConfig:
    return OpenVikingConfig.from_dict(
        {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "api_key": "test-key",
                    "model": "text-embedding-3-small",
                }
            },
            "custom_parsers": {
                "my-docx-parser": {
                    "class": class_path,
                    "extensions": extensions or [".docx"],
                    "kwargs": kwargs or {"plugin-name": "my-docx-parser", "version": 1.0},
                }
            },
        }
    )


def test_register_configured_custom_parsers_overrides_builtin_extension():
    from openviking.parse.custom_loader import register_configured_custom_parsers

    registry = ParserRegistry(register_optional=False)
    before = registry.get_parser_for_file("report.docx")
    assert isinstance(before, WordParser)

    config = _custom_parser_config()

    register_configured_custom_parsers(registry=registry, config=config)

    parser = registry.get_parser_for_file("report.docx")
    assert type(parser).__name__ == "ConfiguredParserWrapper"
    assert type(parser.parser).__name__ == "SampleDocxParser"
    assert parser.supported_extensions == [".docx"]
    assert parser.parser.kwargs == {"plugin-name": "my-docx-parser", "version": 1.0}


def test_register_configured_custom_parsers_is_idempotent_for_same_config():
    from openviking.parse.custom_loader import register_configured_custom_parsers

    registry = ParserRegistry(register_optional=False)
    config = _custom_parser_config()

    register_configured_custom_parsers(registry=registry, config=config)
    first = registry.get_parser_for_file("report.docx")
    register_configured_custom_parsers(registry=registry, config=config)
    second = registry.get_parser_for_file("report.docx")

    assert first is second


def test_register_configured_custom_parsers_rejects_non_base_parser_class():
    from openviking.parse.custom_loader import register_configured_custom_parsers

    registry = ParserRegistry(register_optional=False)
    config = _custom_parser_config("tests.utils.custom_parser_samples.NotAParser")

    with pytest.raises(TypeError, match="BaseParser"):
        register_configured_custom_parsers(registry=registry, config=config)


def test_register_configured_custom_parsers_rejects_missing_import():
    from openviking.parse.custom_loader import register_configured_custom_parsers

    registry = ParserRegistry(register_optional=False)
    config = _custom_parser_config("tests.utils.custom_parser_samples.MissingParser")

    with pytest.raises(ImportError, match="MissingParser"):
        register_configured_custom_parsers(registry=registry, config=config)


def test_register_configured_custom_parsers_reloads_when_config_changes():
    from openviking.parse.custom_loader import register_configured_custom_parsers

    registry = ParserRegistry(register_optional=False)
    config = _custom_parser_config(kwargs={"plugin-name": "first"})
    updated = _custom_parser_config(kwargs={"plugin-name": "second"}, extensions=[".docx", ".doc"])

    register_configured_custom_parsers(registry=registry, config=config)
    first = registry.get_parser_for_file("report.docx")

    register_configured_custom_parsers(registry=registry, config=updated)
    second = registry.get_parser_for_file("report.docx")

    assert second is not first
    assert second.supported_extensions == [".docx", ".doc"]
    assert second.parser.kwargs == {"plugin-name": "second"}


def test_register_configured_custom_parsers_requires_initialized_config():
    from openviking.parse.custom_loader import build_custom_parser_registration_key

    config = _custom_parser_config()

    key = build_custom_parser_registration_key(config)

    assert json.loads(key) == {
        "my-docx-parser": {
            "class": "tests.utils.custom_parser_samples.SampleDocxParser",
            "extensions": [".docx"],
            "kwargs": {"plugin-name": "my-docx-parser", "version": 1.0},
        }
    }
