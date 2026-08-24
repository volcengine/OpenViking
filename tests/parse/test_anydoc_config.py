import pytest

from openviking.parse import registry as registry_module
from openviking.parse.parsers.anydoc import AnyDocParser
from openviking_cli.utils import config as config_module
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.parser_config import load_parser_configs_from_dict


def test_anydoc_config_defaults():
    configs = load_parser_configs_from_dict({})
    cfg = configs["anydoc"]
    assert cfg.enabled is True
    assert cfg.max_table_rows == 1000


def test_anydoc_config_from_dict():
    configs = load_parser_configs_from_dict({"anydoc": {"enabled": False, "max_table_rows": 25}})
    assert configs["anydoc"].enabled is False
    assert configs["anydoc"].max_table_rows == 25


def test_anydoc_config_rejects_enable_alias():
    with pytest.raises(ValueError, match="enable"):
        load_parser_configs_from_dict({"anydoc": {"enable": False}})


def test_anydoc_config_rejects_legacy_fallback_field():
    with pytest.raises(ValueError, match="fallback_to_legacy"):
        load_parser_configs_from_dict({"anydoc": {"fallback_to_legacy": True}})


def test_anydoc_config_is_exported_from_config_package():
    assert config_module.AnydocConfig is type(load_parser_configs_from_dict({})["anydoc"])


def test_application_anydoc_config_reaches_default_registry(monkeypatch):
    config = OpenVikingConfig.from_dict({"parsers": {"anydoc": {"enabled": False}}})
    monkeypatch.setattr(config_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(registry_module, "_default_registry", None)

    registry = registry_module.get_registry()

    assert isinstance(registry._parsers["anydoc"], AnyDocParser)
    assert registry._parsers["anydoc"].anydoc_config is config.anydoc
    assert registry._parsers["anydoc"].anydoc_config.enabled is False
