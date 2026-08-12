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
