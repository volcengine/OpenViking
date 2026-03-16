# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

try:
    from openviking.session.memory_extractor import MemoryExtractor
except Exception:  # pragma: no cover - fallback for minimal local test env
    logger_stub = SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None
    )

    modules = {
        "openviking": ModuleType("openviking"),
        "openviking.core": ModuleType("openviking.core"),
        "openviking.core.context": ModuleType("openviking.core.context"),
        "openviking.prompts": ModuleType("openviking.prompts"),
        "openviking.session": ModuleType("openviking.session"),
        "openviking.session.user_id": ModuleType("openviking.session.user_id"),
        "openviking.storage": ModuleType("openviking.storage"),
        "openviking.storage.viking_fs": ModuleType("openviking.storage.viking_fs"),
        "openviking.utils": ModuleType("openviking.utils"),
        "openviking.utils.config": ModuleType("openviking.utils.config"),
    }

    modules["openviking.core.context"].Context = object
    modules["openviking.core.context"].ContextType = SimpleNamespace(
        MEMORY=SimpleNamespace(value="memory")
    )
    modules["openviking.core.context"].Vectorize = object
    modules["openviking.prompts"].render_prompt = lambda *a, **k: ""
    modules["openviking.session.user_id"].UserIdentifier = object
    modules["openviking.storage.viking_fs"].get_viking_fs = lambda: None
    modules["openviking.utils"].get_logger = lambda _name: logger_stub
    modules["openviking.utils.config"].get_openviking_config = lambda: SimpleNamespace(
        language_fallback="en", vlm=None
    )

    for name, module in modules.items():
        sys.modules.setdefault(name, module)

    module_path = (
        Path(__file__).resolve().parents[2] / "openviking" / "session" / "memory_extractor.py"
    )
    spec = importlib.util.spec_from_file_location(
        "openviking.session.memory_extractor", module_path
    )
    memory_extractor = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(memory_extractor)
    MemoryExtractor = memory_extractor.MemoryExtractor


def _msg(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def test_detect_output_language_japanese_kana_and_kanji():
    messages = [
        _msg(
            "user",
            "\u4eca\u65e5\u306f\u65b0\u6a5f\u80fd\u306e\u8a2d\u8a08\u3092\u9032\u3081\u307e\u3059",
        )
    ]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "ja"


def test_detect_output_language_chinese_han_only():
    messages = [
        _msg("user", "\u4eca\u5929\u7ee7\u7eed\u4f18\u5316\u8bb0\u5fc6\u62bd\u53d6\u6a21\u5757")
    ]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "zh-CN"


def test_detect_output_language_japanese_with_more_han_than_kana():
    messages = [
        _msg(
            "user",
            "\u65b0\u6a5f\u80fd\u8a2d\u8a08\u306e\u65b9\u91dd\u3092\u78ba\u8a8d\u3057\u307e\u3059",
        )
    ]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "ja"


def test_detect_output_language_chinese_with_single_cyrillic():
    """Mixed Chinese with single Cyrillic char should be detected as Chinese, not Russian."""
    messages = [_msg("user", "\u8fd9\u662f\u4e2d\u6587 \u0414 \u518d\u7ee7\u7eed")]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "zh-CN"


def test_detect_output_language_japanese_with_single_cyrillic():
    """Mixed Japanese with single Cyrillic char should be detected as Japanese, not Russian."""
    messages = [_msg("user", "\u3053\u308c\u306f\u65e5\u672c\u8a9e \u042f ")]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "ja"


def test_detect_output_language_russian_with_threshold():
    """Russian text with sufficient Cyrillic chars should be detected as Russian."""
    messages = [_msg("user", "\u042d\u0442\u043e \u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u0442\u0435\u043a\u0441\u0442")]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "ru"


def test_detect_output_language_insufficient_cyrillic_fallback():
    """Text with only 1 Cyrillic char among Latin should fallback, not Russian."""
    messages = [_msg("user", "Hello \u0424 world")]
    language = MemoryExtractor._detect_output_language(messages, fallback_language="en")
    assert language == "en"
