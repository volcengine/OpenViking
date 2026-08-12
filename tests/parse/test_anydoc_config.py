from openviking.parse import registry as registry_module
from openviking_cli.utils import config as config_module
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.parser_config import load_parser_configs_from_dict


def test_anydoc_config_defaults():
    configs = load_parser_configs_from_dict({})
    cfg = configs["anydoc"]
    assert cfg.enable is True
    assert cfg.fallback_to_legacy is False


def test_anydoc_config_from_dict():
    configs = load_parser_configs_from_dict(
        {"anydoc": {"enable": False, "fallback_to_legacy": True}}
    )
    assert configs["anydoc"].enable is False
    assert configs["anydoc"].fallback_to_legacy is True


def test_application_anydoc_config_reaches_default_registry(monkeypatch):
    config = OpenVikingConfig.from_dict(
        {"parsers": {"anydoc": {"enable": False, "fallback_to_legacy": True}}}
    )
    monkeypatch.setattr(config_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(registry_module, "_default_registry", None)

    registry = registry_module.get_registry()

    assert registry._parsers["word"].anydoc_config is config.anydoc
    assert registry._parsers["legacy_doc"].anydoc_config is config.anydoc
    assert registry._parsers["word"].anydoc_config.enable is False
    assert registry._parsers["legacy_doc"].anydoc_config.fallback_to_legacy is True
