# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.prompts import render_prompt
from openviking.session.memory.utils.language import (
    _detect_language_from_text,
    _language_from_locale_value,
    _language_from_timezone_value,
    _resolve_system_fallback_language,
    resolve_output_language,
    resolve_output_language_from_conversation,
)


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

    def test_detect_language_kanji_heavy_japanese(self):
        text = "明日は会議です"
        language = _detect_language_from_text(text, fallback_language="ja")
        assert language == "ja"

    def test_japanese_text_uses_system_fallback_when_system_is_not_japanese(self):
        text = "明日は会議です"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "zh-CN"

    def test_strong_japanese_text_can_override_system_fallback(self):
        text = (
            "今日は新しい機能の設計を進めます。明日の会議で方針を確認します。"
            "そのあとで実装とテストをまとめます。"
        )
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "ja"

    def test_japanese_title_does_not_override_chinese_fallback(self):
        text = "请记住我最近在读《ノルウェイの森》，后面继续用中文讨论这个内容"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "zh-CN"

    def test_single_kana_does_not_override_chinese(self):
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

    def test_detect_language_russian(self):
        text = "Это русский документ"
        language = _detect_language_from_text(text, fallback_language="ru")
        assert language == "ru"

    def test_strong_russian_text_can_override_system_fallback(self):
        text = "Это русский документ для проверки памяти пользователя и настроек проекта"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "ru"

    def test_detect_language_arabic(self):
        text = "هذا مستند باللغة العربية"
        language = _detect_language_from_text(text, fallback_language="ar")
        assert language == "ar"

    def test_strong_arabic_text_can_override_system_fallback(self):
        text = "هذا مستند عربي طويل لتسجيل تفضيلات المستخدم ومعلومات المشروع"
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "ar"

    def test_detect_language_empty_text(self):
        text = ""
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "en"

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

    def test_detect_language_italian(self):
        text = "Questo documento descrive le preferenze dell utente e il progetto da completare."
        language = _detect_language_from_text(text, fallback_language="it")
        assert language == "it"

    def test_strong_italian_text_can_override_system_fallback(self):
        text = (
            "Questo documento descrive le preferenze dell utente e il progetto da completare. "
            "Il contenuto include le decisioni, le attività, la priorità e una nota finale."
        )
        language = _detect_language_from_text(text, fallback_language="zh-CN")
        assert language == "it"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("project document user data model profile", "en"),
            ("Ce document décrit les préférences de l utilisateur et le projet à terminer.", "fr"),
            (
                "Este documento describe las preferencias del usuario y el proyecto para completar.",
                "es",
            ),
            ("Dieses Dokument beschreibt die Präferenzen der Benutzer und das Projekt.", "de"),
            (
                "Este documento descreve as preferências do usuário e o projeto para completar.",
                "pt",
            ),
        ],
    )
    def test_detect_latin_language_conservatively(self, text, expected):
        language = _detect_language_from_text(text, fallback_language=expected)
        assert language == expected


class TestLanguageFlow:
    """语言检测 + 模板渲染流程测试。"""

    @pytest.mark.parametrize(
        "lang,content,file_name",
        [
            ("zh-CN", "这是一个中文Python文件，包含测试代码", "chinese_code.py"),
            ("en", "This is an English Python file for testing", "english_code.py"),
            ("ja", "これは日本語のPythonコードテストファイルです", "japanese_code.py"),
            ("ko", "이것은 한국어 Python 코드 테스트 파일입니다", "korean_code.py"),
            ("ru", "Это русский тестовый файл Python кода", "russian_code.py"),
            ("ar", "هذا ملف اختبار كود بايثون عربي", "arabic_code.py"),
        ],
    )
    def test_language_detection_to_template_flow(self, lang, content, file_name):
        """语言检测 -> output_language 注入模板 -> prompt 包含语言指令"""
        detected_lang = _detect_language_from_text(content, fallback_language=lang)
        assert detected_lang == lang, f"Expected {lang}, got {detected_lang}"

        prompt = render_prompt(
            "semantic.code_summary",
            {"file_name": file_name, "content": content, "output_language": detected_lang},
        )
        assert f"Output Language: {lang}" in prompt


class TestOverviewGenerationFlow:
    """目录概述生成流程测试。"""

    @pytest.mark.parametrize(
        "lang,file_summaries",
        [
            ("zh-CN", "[1] file1.py: 这是一个Python文件\n[2] file2.py: 这是另一个文件"),
            ("en", "[1] file1.py: This is a Python file\n[2] file2.py: Another file"),
            ("ja", "[1] file1.py: それはPythonファイルです\n[2] file2.py: これもPython"),
        ],
    )
    def test_overview_generation_language_flow(self, lang, file_summaries):
        """目录摘要 -> 语言检测 -> overview 模板"""
        detected_lang = _detect_language_from_text(file_summaries, fallback_language=lang)
        assert detected_lang == lang

        prompt = render_prompt(
            "semantic.overview_generation",
            {
                "dir_name": "test_dir",
                "file_summaries": file_summaries,
                "children_abstracts": "",
                "output_language": detected_lang,
            },
        )
        assert f"Output Language: {lang}" in prompt
        assert "Output in Markdown format" in prompt
        assert "Brief Description" in prompt
        assert "abstract_max_chars" not in prompt

    def test_overview_generation_prompt_preserves_repository_hierarchy(self):
        prompt = render_prompt(
            "semantic.overview_generation",
            {
                "dir_name": "repo-root",
                "file_summaries": "[1] pyproject.toml: Python project config",
                "children_abstracts": "- backend/: API service\n- frontend/: web UI",
                "output_language": "en",
            },
        )

        assert "Relationship rules:" in prompt
        assert (
            "- Treat child directories as parts of the same repository unless the summaries clearly show they are independent projects."
            in prompt
        )
        assert (
            "- Do not describe every child directory as an independent project by default."
            in prompt
        )
        assert (
            "- When the summaries suggest a code repository, explain how subdirectories relate to the whole repo, such as services, libraries, apps, modules, or support folders."
            in prompt
        )

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
            "ru": "Резюме на русском: это тестовая функция",
            "ar": "ملخص عربي: هذه وظيفة اختبار",
        }

    async def get_completion_async(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        for lang, response in self.language_responses.items():
            if f"Output Language: {lang}" in prompt:
                return response
        return self.language_responses["en"]


def _verify_content_language(text: str, expected_lang: str) -> bool:
    """验证文本内容语言是否符合预期。"""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    japanese_chars = sum(1 for c in text if "\u3040" <= c <= "\u309f" or "\u30a0" <= c <= "\u30ff")
    korean_chars = sum(1 for c in text if "\uac00" <= c <= "\ud7af")
    russian_chars = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06ff")

    thresholds = {
        "zh-CN": chinese_chars >= 2,
        "en": re.search(r"\b(the|is|are|test|function)\b", text, re.I) is not None,
        "ja": japanese_chars >= 2,
        "ko": korean_chars >= 2,
        "ru": russian_chars >= 2,
        "ar": arabic_chars >= 2,
    }
    return thresholds.get(expected_lang, False)


class TestGenerateTextSummaryOutputLanguage:
    """端到端测试：验证 _generate_text_summary 生成的内容语言是否符合预期。"""

    _LANGUAGE_LOCALE = {
        "zh-CN": "zh_CN.UTF-8",
        "en": "en_US.UTF-8",
        "ja": "ja_JP.UTF-8",
        "ko": "ko_KR.UTF-8",
        "ru": "ru_RU.UTF-8",
        "ar": "ar_SA.UTF-8",
    }

    @pytest.fixture
    def temp_multilang_files(self):
        """创建包含多种语言内容的临时测试文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            files = {}

            files["chinese_py"] = tmppath / "chinese_code.py"
            files["chinese_py"].write_text("# 中文Python文件\ndef 你好():\n    print('你好世界')\n")

            files["english_py"] = tmppath / "english_code.py"
            files["english_py"].write_text(
                "# English Python file\ndef hello():\n    print('Hello World')\n"
            )

            files["japanese_py"] = tmppath / "japanese_code.py"
            files["japanese_py"].write_text(
                "# 日本語Pythonファイル\ndef こんにちは():\n    print('こんにちは世界')\n"
            )

            files["korean_py"] = tmppath / "korean_code.py"
            files["korean_py"].write_text(
                "# 한국어 Python 파일\ndef 안녕하세요():\n    print('안녕하세요')\n"
            )

            files["chinese_md"] = tmppath / "chinese_doc.md"
            files["chinese_md"].write_text("# 中文文档\n\n这是一个测试文档，包含中文技术内容。\n")

            files["english_md"] = tmppath / "english_doc.md"
            files["english_md"].write_text(
                "# English Documentation\n\nThis is a test document with English content.\n"
            )

            yield files

    def _create_mock_viking_fs(self, content: str) -> MagicMock:
        mock_fs = MagicMock()
        mock_fs.read_file = AsyncMock(return_value=content)
        return mock_fs

    def _create_mock_config(self, mock_vlm: LanguageAwareMockVLM) -> MagicMock:
        mock_config = MagicMock()
        mock_config.vlm = mock_vlm
        mock_config.language_fallback = "en"
        mock_config.semantic.max_file_content_chars = 10000
        mock_config.code.code_summary_mode = "llm"
        return mock_config

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "file_key,file_name,expected_lang",
        [
            ("chinese_py", "chinese_code.py", "zh-CN"),
            ("english_py", "english_code.py", "en"),
            ("japanese_py", "japanese_code.py", "ja"),
            ("korean_py", "korean_code.py", "ko"),
            ("chinese_md", "chinese_doc.md", "zh-CN"),
            ("english_md", "english_doc.md", "en"),
        ],
    )
    async def test_e2e_code_output_language(
        self, temp_multilang_files, file_key, file_name, expected_lang
    ):
        """端到端测试：文件 -> 语言检测 -> 生成对应语言摘要"""
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        content = Path(temp_multilang_files[file_key]).read_text()
        mock_vlm = LanguageAwareMockVLM()
        mock_viking_fs = self._create_mock_viking_fs(content)
        mock_config = self._create_mock_config(mock_vlm)

        with (
            patch.dict(
                os.environ,
                {"LC_ALL": self._LANGUAGE_LOCALE[expected_lang]},
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=mock_viking_fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_openviking_config",
                return_value=mock_config,
            ),
        ):
            processor = SemanticProcessor()
            processor._current_ctx = MagicMock()

            result = await processor._generate_text_summary(
                file_path=temp_multilang_files[file_key],
                file_name=file_name,
                llm_sem=asyncio.Semaphore(1),
            )

            prompt_sent = mock_vlm.prompts_received[0]
            assert f"Output Language: {expected_lang}" in prompt_sent, (
                f"{file_name}: Prompt missing Output Language: {expected_lang}"
            )

            assert _verify_content_language(result["summary"], expected_lang), (
                f"{file_name}: Content language mismatch. Expected {expected_lang}, got: {result['summary']}"
            )
            assert result["content"] == content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content,file_name,expected_lang",
        [
            ("Это русский тестовый файл Python", "russian_code.py", "ru"),
            ("هذا ملف اختبار كود بايثون عربي", "arabic_code.py", "ar"),
        ],
    )
    async def test_e2e_russian_arabic_output_language(self, content, file_name, expected_lang):
        """端到端测试：俄文和阿拉伯文内容"""
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        mock_vlm = LanguageAwareMockVLM()
        mock_viking_fs = self._create_mock_viking_fs(content)
        mock_config = self._create_mock_config(mock_vlm)

        with (
            patch.dict(
                os.environ,
                {"LC_ALL": self._LANGUAGE_LOCALE[expected_lang]},
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_viking_fs",
                return_value=mock_viking_fs,
            ),
            patch(
                "openviking.storage.queuefs.semantic_processor.get_openviking_config",
                return_value=mock_config,
            ),
        ):
            processor = SemanticProcessor()
            processor._current_ctx = MagicMock()

            result = await processor._generate_text_summary(
                file_path=f"/tmp/{file_name}",
                file_name=file_name,
                llm_sem=asyncio.Semaphore(1),
            )

            prompt_sent = mock_vlm.prompts_received[0]
            assert f"Output Language: {expected_lang}" in prompt_sent

            assert _verify_content_language(result["summary"], expected_lang), (
                f"{file_name}: Content language mismatch. Expected {expected_lang}, got: {result['summary']}"
            )


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

    def test_override_set_bypasses_detection(self):
        config = self._make_config(override="en")
        result = resolve_output_language("これは日本語のテキストです", config=config)
        assert result == "en"

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
        with patch.dict(os.environ, {"TZ": "Asia/Tokyo"}, clear=True):
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
        with patch.dict(os.environ, {"TZ": "America/New_York"}, clear=True):
            result = resolve_output_language("12345 ---", config=config)
        assert result == "en"

    def test_arabic_timezone_hint_used_when_locale_hint_absent(self):
        config = self._make_config(override="")
        with patch.dict(os.environ, {"TZ": "Asia/Riyadh"}, clear=True):
            result = resolve_output_language("12345 ---", config=config)
        assert result == "ar"

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
