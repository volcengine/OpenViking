import pytest

from openviking_cli.utils.config.jev_config import JevConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


def test_rule_cleanup_defaults_to_thirty_day_interval():
    config = OpenVikingConfig.from_dict({})

    assert config.rules.archived_retention_days == 30
    assert config.rules.archived_cleanup_interval_seconds == 30 * 24 * 60 * 60


def test_openviking_config_accepts_dedicated_rule_models():
    config = OpenVikingConfig.from_dict(
        {
            "rules": {
                "compiler_context_window": 32768,
                "archived_retention_days": 45,
                "archived_cleanup_interval_seconds": 3600,
                "archived_cleanup_enabled": False,
                "scenario_irrelevance_threshold": 0.95,
                "jev_rule_matcher": {
                    "provider": "typesafe",
                    "api_key": "jev-key",
                },
                "compiler": {
                    "provider": "litellm",
                    "model": "ollama/rule-compiler",
                },
            }
        }
    )

    assert config.rules.jev_rule_matcher is not None
    assert config.rules.jev_rule_matcher.resolved_model() == "jev-latest"
    assert (
        config.rules.jev_rule_matcher.resolved_api_base() == "https://api.typesafe.ai/v1/systemone"
    )
    assert config.rules.compiler_context_window == 32768
    assert config.rules.archived_retention_days == 45
    assert config.rules.archived_cleanup_interval_seconds == 3600
    assert config.rules.archived_cleanup_enabled is False
    assert config.rules.scenario_irrelevance_threshold == 0.95
    compiler = config.rules.get_compiler()
    assert compiler is not None
    assert compiler.model == "ollama/rule-compiler"


def test_empty_rule_compiler_block_falls_back_to_default_vlm():
    config = OpenVikingConfig.from_dict(
        {
            "rules": {
                "compiler": {},
            }
        }
    )

    assert config.rules.get_compiler() is None


@pytest.mark.parametrize(
    "jev_config,expected",
    [
        (
            {"provider": "cohere", "api_key": "key"},
            "Jev provider must be one of",
        ),
        (
            {"provider": "jev", "api_key": "key"},
            "Jev provider must be one of",
        ),
        (
            {"provider": "volcengine", "api_key": "key", "model": "unsupported"},
            "Jev provider must be one of",
        ),
    ],
)
def test_jev_rule_matcher_rejects_unsupported_config(jev_config, expected):
    with pytest.raises(ValueError, match=expected):
        OpenVikingConfig.from_dict({"rules": {"jev_rule_matcher": jev_config}})


@pytest.mark.parametrize(
    "provider,api_base,model",
    [
        ("typesafe", "https://api.typesafe.ai/v1/systemone", "jev-latest"),
        (
            "vercel",
            "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
            "typesafe-ai/jev",
        ),
        ("openrouter", "https://openrouter.ai/api/v1/systemone", "jev-latest"),
    ],
)
def test_jev_provider_defaults(provider, api_base, model):
    config = JevConfig(provider=provider, api_key="key")

    assert config.resolved_api_base() == api_base
    assert config.resolved_model() == model
    assert config.resolved_extra_headers() == {}


def test_jev_explicit_overrides_are_used_unchanged():
    config = JevConfig(
        provider="vercel",
        api_key="key",
        api_base="https://gateway.example.com/decision/",
        model="private-jev",
    )

    assert config.resolved_api_base() == "https://gateway.example.com/decision/"
    assert config.resolved_model() == "private-jev"


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"provider": "custom", "api_key": "key", "model": "jev"}, "requires 'api_base'"),
        (
            {
                "provider": "custom",
                "api_key": "key",
                "api_base": "https://example.com/jev",
            },
            "requires 'model'",
        ),
        ({"provider": "typesafe"}, "requires 'api_key'"),
    ],
)
def test_jev_provider_validates_required_fields(config, expected):
    with pytest.raises(ValueError, match=expected):
        JevConfig(**config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "noul"),
        ("threshold", 0.5),
        ("max_input_tokens", 2048),
    ],
)
def test_jev_rule_matcher_rejects_rerank_fields(field, value):
    with pytest.raises(ValueError, match=field):
        JevConfig.model_validate({"provider": "typesafe", "api_key": "key", field: value})


def test_rule_compiler_context_window_rejects_too_small_value():
    with pytest.raises(ValueError, match="must be 0 or at least 2048"):
        OpenVikingConfig.from_dict({"rules": {"compiler_context_window": 1024}})


@pytest.mark.parametrize(
    "field,value",
    [
        ("archived_retention_days", 0),
        ("archived_cleanup_interval_seconds", 59),
    ],
)
def test_rule_cleanup_config_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        OpenVikingConfig.from_dict({"rules": {field: value}})
