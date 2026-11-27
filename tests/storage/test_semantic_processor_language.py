# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.utils.language import (
    _detect_language_from_text,
    _language_from_locale_value,
    _language_from_timezone_value,
    _resolve_system_fallback_language,
    resolve_output_language,
    resolve_output_language_from_conversation,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.parser_config import SemanticConfig


class _TestVLMResolver:
    def __init__(self, vlm):
        self._vlm = vlm

    async def get_vlm(self, account_id):
        del account_id
        return self._vlm


class TestLanguageDetection:
    """语言检测功能测试。"""

    def test_detect_language_chinese(self):
        text = "这是一个中文文档，用于测试语言检测功能"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"

    def test_detect_language_english_fallback(self):
        text = "This is an English document for testing language detection"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "en"

    def test_detect_language_japanese(self):
        text = "これは日本語のドキュメントです"
        language = _detect_language_from_text(text, fallback_language="ja")
        assert language == "ja"

    @pytest.mark.parametrize("fallback", ["ja", "zh-CN"])
    def test_kanji_do_not_count_toward_kana_ratio(self, fallback):
        text = "明日は会議です"
        assert _detect_language_from_text(text, fallback_language=fallback) == "zh-CN"

    def test_strong_japanese_text_can_override_system_fallback(self):
        text = (
            "今日は新しい機能の設計を進めます。明日の会議で方針を確認します。"
            "そのあとで実装とテストをまとめます。"
        )
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "ja"

    def test_japanese_title_in_chinese_text_selects_chinese(self):
        text = "请记住我最近在读《ノルウェイの森》，后面继续用中文讨论这个内容"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "zh-CN"

    def test_single_kana_in_han_text_selects_chinese(self):
        text = "这是中文の测试"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"

    def test_detect_language_korean(self):
        text = "이것은 한국어 문서입니다"
        language = _detect_language_from_text(text, fallback_language="ko")
        assert language == "ko"

    def test_strong_korean_text_can_override_system_fallback(self):
        text = "이것은 한국어로 작성된 긴 문서입니다 사용자의 선호와 프로젝트 내용을 기록합니다"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "ko"

    def test_detect_language_empty_text(self):
        text = ""
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "en"

    @pytest.mark.parametrize(
        "text,fallback,expected",
        [
            ("・", "en", "en"),
            ("١٢٣٤٥، １２３！？", "zh-CN", "zh-CN"),
            ("×÷", "ko", "ko"),
            ("テスト日本・١٢٣", "en", "ja"),
            ("中" + "a" * 19 + "،١", "en", "zh-CN"),
            ("id42 field_name e٤١", "ko", "ko"),
        ],
    )
    def test_nonletters_do_not_contribute_to_language_ratios(self, text, fallback, expected):
        assert _detect_language_from_text(text, fallback_language=fallback) == expected

    def test_detect_language_mixed_chinese_english(self):
        text = "这是一个 mixed 文档"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"

    def test_detect_language_chinese_with_single_korean_char(self):
        text = "这是中文需求，继续优化记忆。한"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"

    def test_detect_language_chinese_with_single_cyrillic_char(self):
        text = "这是中文需求，继续优化记忆。Д"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"

    def test_detect_language_english_with_single_korean_char(self):
        text = "Please optimize memory extraction 한"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "en"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("中文" + "a" * 38, "zh-CN"),
            ("中文" + "a" * 39, "en"),
            ("中文" + "я" * 39, "en"),
            ("中" + "a" * 19, "zh-CN"),
            ("中" + "a" * 20, "en"),
        ],
    )
    def test_chinese_requires_five_percent(self, text, expected):
        assert _detect_language_from_text(text, fallback_language="en") == expected

    @pytest.mark.parametrize(
        "text,fallback,expected",
        [("go の", "ja", "en"), ("go", "ja", "en"), ("test 한", "ko", "en")],
    )
    def test_short_text_with_locale_hint(self, text, fallback, expected):
        assert _detect_language_from_text(text, fallback_language=fallback) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("テスト日本", "ja"),
            ("テスト日本語", "zh-CN"),
            ("한국어 go", "ko"),
            ("한국어 log", "en"),
        ],
    )
    def test_japanese_and_korean_dominance_at_sixty_percent(self, text, expected):
        assert _detect_language_from_text(text, fallback_language="en") == expected

    @pytest.mark.parametrize(
        "text,locale_hint",
        [
            ("Questo documento descrive le preferenze del progetto.", "it"),
            ("Ce document décrit les préférences pour le projet.", "fr"),
            ("Este documento describe las preferencias del usuario.", "es"),
            ("Dieses Dokument beschreibt die Präferenzen der Benutzer.", "de"),
            ("Este documento descreve as preferências do usuário.", "pt"),
            ("Это русский документ", "ru"),
            ("هذا مستند باللغة العربية", "ar"),
        ],
    )
    def test_unsupported_languages_use_english(self, text, locale_hint):
        assert _detect_language_from_text(text, fallback_language=locale_hint) == "en"
        assert _language_from_locale_value(locale_hint) == ""


class TestOverviewGenerationFlow:
    """目录概述生成流程测试。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "description,override,expected_language",
        [
            ("", "", "en"),
            ("这是用于查询任务状态和统计运行时间的客户端代码。", "", "zh-CN"),
            (
                "Este documento descreve as preferências do usuário e o projeto para completar. "
                "Ele reúne informações importantes sobre funcionalidades e configuração.",
                "",
                "en",
            ),
            ("", "zh-CN", "zh-CN"),
            ("", "pt", "pt"),
        ],
    )
    async def test_import_paths_do_not_set_overview_language(
        self, description, override, expected_language
    ):
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        imports = [f'"github.com/example/module{index}"' for index in range(6)]
        skeleton = "package client\nimport (\n" + "\n".join(imports) + "\n)\nfunc NewClient()"
        config = MagicMock()
        config.output_language_override = override
        config.semantic = SemanticConfig()
        config.vlm.get_completion_async = AsyncMock(return_value="# client\nClient overview.")

        with (
            patch(
                "openviking.storage.queuefs.semantic_processor.get_openviking_config",
                return_value=config,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=MagicMock(read_file=AsyncMock(side_effect=FileNotFoundError)),
            ),
        ):
            await SemanticProcessor(
                vlm_resolver=_TestVLMResolver(config.vlm)
            )._generate_directory_semantics(
                "viking://resources/example/client",
                [{"name": "client.go", "summary": description + "\n" + skeleton}],
                [],
                total_entries=1,
                ctx=RequestContext(user=UserIdentifier("test", "user"), role=Role.USER),
            )

        prompt = config.vlm.get_completion_async.call_args.args[0]
        assert f"Output Language: {expected_language}" in prompt
        assert all(path.strip('"') in prompt for path in imports)


class LanguageAwareMockVLM:
    """语言感知的 MockVLM，根据 prompt 中的 Output Language 返回对应语言的响应。"""

    def __init__(self):
        self.is_available = MagicMock(return_value=True)
        self.prompts_received = []
        self.language_responses = {
            "zh-CN": "中文摘要：这是一个测试函数",
            "en": "English summary: This is a test function",
            "ja": "日本語要約：これはテスト関数です",
            "ko": "한국어 요약: 이것은 테스트 함수입니다",
        }

    async def get_completion_async(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        for lang, response in self.language_responses.items():
            if f"Output Language: {lang}" in prompt:
                return response
        return self.language_responses["en"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_name", "content", "locale", "expected_language"),
    [
        pytest.param(
            "readme.md",
            "这是需要总结的中文技术说明。",
            "en_US.UTF-8",
            "zh-CN",
            id="chinese-document",
        ),
        pytest.param(
            "readme.md",
            "This is a technical document describing how the system works.",
            "zh_CN.UTF-8",
            "en",
            id="english-document",
        ),
        pytest.param(
            "broken.py",
            "Это русский документ для проверки памяти пользователя и настроек проекта",
            "en_US.UTF-8",
            "en",
            id="russian-code-summary-fallback",
        ),
        pytest.param(
            "broken.py",
            "هذا مستند عربي طويل لتسجيل تفضيلات المستخدم ومعلومات المشروع",
            "en_US.UTF-8",
            "en",
            id="arabic-code-summary-fallback",
        ),
    ],
)
async def test_text_summary_passes_detected_content_language_to_model(
    monkeypatch, file_name, content, locale, expected_language
):
    from openviking.storage.queuefs import semantic_processor

    monkeypatch.setenv("LC_ALL", locale)
    monkeypatch.setattr(semantic_processor, "get_viking_fs", lambda: None)
    config = MagicMock(output_language_override="")
    config.semantic.max_file_content_chars = 10000
    monkeypatch.setattr(semantic_processor, "get_openviking_config", lambda: config)
    vlm = MagicMock()
    vlm.is_available.return_value = True
    vlm.get_completion_async = AsyncMock(return_value="  generated summary  ")
    processor = semantic_processor.SemanticProcessor(vlm_resolver=_TestVLMResolver(vlm))

    result = await processor._generate_text_summary(
        "viking://resources/" + file_name,
        file_name,
        asyncio.Semaphore(1),
        file_content=content.encode(),
    )

    assert result == {"name": file_name, "summary": "generated summary"}
    assert vlm.get_completion_async.await_count == 1
    prompt = vlm.get_completion_async.await_args.args[0]
    assert content in prompt
    assert f"Output Language: {expected_language}" in prompt


class TestOutputLanguageOverride:
    """Config-level `output_language_override` bypasses content-based detection."""

    def _make_config(self, override: str = "", fallback: str = "en"):
        config = MagicMock()
        config.output_language_override = override
        config.language_fallback = fallback
        return config

    def test_override_unset_detects_from_content(self):
        config = self._make_config(override="")
        with patch.dict(os.environ, {"LC_ALL": "ja_JP.UTF-8"}):
            result = resolve_output_language("これは日本語のテキストです", config=config)
        assert result == "ja"

    def test_override_unset_uses_english_for_latin_text(self):
        config = self._make_config(override="", fallback="en")
        result = resolve_output_language(
            "Plain English text with no special scripts", config=config
        )
        assert result == "en"

    async def test_override_set_bypasses_detection(self, monkeypatch):
        from openviking.config.binding import manager_over_source
        from openviking.config.source import MemoryConfigSource
        from openviking.server.identity import RequestContext, Role
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor
        from openviking_cli.session.user_id import UserIdentifier
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

        cluster = OpenVikingConfig.from_dict({"output_language_override": "en"})
        monkeypatch.setattr("openviking.config.binding.get_openviking_config", lambda: cluster)
        monkeypatch.setattr(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config", lambda: cluster
        )
        monkeypatch.setattr(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            lambda: MagicMock(read_file=AsyncMock(side_effect=FileNotFoundError)),
        )
        manager = manager_over_source(MemoryConfigSource(), base_config=cluster)
        await manager.patch_account("a", {"output_language_override": "en"})
        await manager.patch_account("b", {"output_language_override": "zh-CN"})
        vlm = LanguageAwareMockVLM()
        processor = SemanticProcessor(
            vlm_resolver=_TestVLMResolver(vlm), runtime_config_manager=manager
        )
        contexts = [
            RequestContext(user=UserIdentifier(account, "alice"), role=Role.USER)
            for account in ("a", "b")
        ]
        summaries = await asyncio.gather(
            *[
                processor._generate_text_summary(
                    "viking://resources/readme.md",
                    "readme.md",
                    asyncio.Semaphore(1),
                    ctx=ctx,
                    file_content="这是需要保留的中文文档。".encode(),
                )
                for ctx in contexts
            ]
        )
        assert [item["summary"] for item in summaries] == [
            vlm.language_responses["en"],
            vlm.language_responses["zh-CN"],
        ]
        await manager.patch_account("a", {"output_language_override": ""})
        await processor._generate_directory_semantics(
            "viking://resources/project", [summaries[1]], [], ctx=contexts[0], total_entries=1
        )
        assert "Output Language: zh-CN" in vlm.prompts_received[-1]
        assert cluster.output_language_override == "en"

    def test_override_set_wins_over_fallback(self):
        config = self._make_config(override="zh-CN", fallback="en")
        result = resolve_output_language("Plain English text", config=config)
        assert result == "zh-CN"

    def test_override_whitespace_treated_as_unset(self):
        config = self._make_config(override="   ")
        with patch.dict(os.environ, {"LC_ALL": "ja_JP.UTF-8"}):
            result = resolve_output_language("これは日本語のテキストです", config=config)
        assert result == "ja"

    def test_locale_hint_used_when_content_has_no_language_signal(self):
        config = self._make_config(override="")
        with patch.dict(os.environ, {"LC_ALL": "zh_CN.UTF-8"}, clear=True):
            result = resolve_output_language("12345 ---", config=config)
        assert result == "zh-CN"

    @pytest.mark.parametrize(
        "locale_value,expected",
        [
            ("Chinese_China.936", "zh-CN"),
            ("Chinese (Simplified)_China.936", "zh-CN"),
            ("English_United States.1252", "en"),
        ],
    )
    def test_windows_locale_hint_values(self, locale_value, expected):
        assert _language_from_locale_value(locale_value) == expected

    def test_timezone_hint_used_when_locale_hint_absent(self):
        config = self._make_config(override="")
        with (
            patch.dict(os.environ, {"TZ": "Asia/Tokyo"}, clear=True),
            patch(
                "openviking.session.memory.utils.language.locale.getlocale",
                return_value=("C", "UTF-8"),
            ),
        ):
            result = resolve_output_language("12345 ---", config=config)
        assert result == "ja"

    @pytest.mark.parametrize(
        "timezone_value,expected",
        [
            ("China Standard Time", "zh-CN"),
            ("Tokyo Standard Time", "ja"),
            ("Eastern Standard Time", "en"),
        ],
    )
    def test_windows_timezone_hint_values(self, timezone_value, expected):
        assert _language_from_timezone_value(timezone_value) == expected

    @pytest.mark.parametrize("timezone_value", ["Europe/Moscow", "Asia/Riyadh", "Europe/Lisbon"])
    def test_unsupported_timezone_has_no_language_hint(self, timezone_value):
        assert _language_from_timezone_value(timezone_value) == ""

    def test_timezone_hint_overrides_english_locale_for_weak_fallback(self):
        with patch.dict(
            os.environ,
            {"LC_ALL": "en_US.UTF-8", "TZ": "Asia/Shanghai"},
            clear=True,
        ):
            assert _resolve_system_fallback_language("en") == "zh-CN"

    def test_non_english_locale_hint_wins_over_timezone(self):
        with patch.dict(
            os.environ,
            {"LC_ALL": "ja_JP.UTF-8", "TZ": "Asia/Shanghai"},
            clear=True,
        ):
            assert _resolve_system_fallback_language("en") == "ja"

    def test_local_timezone_hint_used_when_tz_env_absent(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "openviking.session.memory.utils.language.locale.getlocale",
                return_value=("C", "UTF-8"),
            ),
            patch(
                "openviking.session.memory.utils.language.os.path.realpath",
                return_value="/usr/share/zoneinfo.default/Asia/Shanghai",
            ),
        ):
            assert _resolve_system_fallback_language("en") == "zh-CN"

    def test_english_timezone_hint_used_when_locale_hint_absent(self):
        config = self._make_config(override="")
        with (
            patch.dict(os.environ, {"TZ": "America/New_York"}, clear=True),
            patch(
                "openviking.session.memory.utils.language.locale.getlocale",
                return_value=("C", "UTF-8"),
            ),
        ):
            result = resolve_output_language("12345 ---", config=config)
        assert result == "en"

    def test_content_language_wins_over_locale_hint(self):
        config = self._make_config(override="")
        with patch.dict(os.environ, {"LC_ALL": "zh_CN.UTF-8"}, clear=True):
            result = resolve_output_language(
                "This is an English document for testing language detection",
                config=config,
            )
        assert result == "en"

    def test_english_content_wins_over_chinese_timezone_hint(self):
        config = self._make_config(override="")
        with patch.dict(
            os.environ,
            {"LC_ALL": "en_US.UTF-8", "TZ": "Asia/Shanghai"},
            clear=True,
        ):
            result = resolve_output_language(
                "This is an English document for testing language detection",
                config=config,
            )
        assert result == "en"

    def test_short_latin_content_wins_over_chinese_timezone_hint(self):
        config = self._make_config(override="")
        with patch.dict(
            os.environ,
            {"LC_ALL": "en_US.UTF-8", "TZ": "Asia/Shanghai"},
            clear=True,
        ):
            result = resolve_output_language("Use Vim", config=config)
        assert result == "en"

    def test_conversation_override_set_bypasses_detection(self):
        config = self._make_config(override="en")
        conversation = "[user]: これは日本語のメッセージです\n[assistant]: reply"
        result = resolve_output_language_from_conversation(conversation, config=config)
        assert result == "en"

    def test_conversation_override_unset_detects_from_user_content(self):
        config = self._make_config(override="")
        conversation = "[user]: これは日本語のメッセージです\n[assistant]: reply"
        with patch.dict(os.environ, {"LC_ALL": "ja_JP.UTF-8"}):
            result = resolve_output_language_from_conversation(conversation, config=config)
        assert result == "ja"

    def test_indexed_conversation_detects_user_content(self):
        config = self._make_config(override="")
        conversation = "[0][user][alice]: 请使用中文\n[1][assistant][bot]: 한국어 응답"
        result = resolve_output_language_from_conversation(conversation, config=config)
        assert result == "zh-CN"
