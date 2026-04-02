# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.prompts import render_prompt
from openviking.session.memory.utils.language import _detect_language_from_text


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
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "ja"

    def test_detect_language_korean(self):
        text = "이것은 한국어 문서입니다"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "ko"

    def test_detect_language_russian(self):
        text = "Это русский документ"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "ru"

    def test_detect_language_arabic(self):
        text = "هذا مستند باللغة العربية"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "ar"

    def test_detect_language_empty_text(self):
        text = ""
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "en"

    def test_detect_language_mixed_chinese_english(self):
        text = "这是一个 mixed 文档"
        language = _detect_language_from_text(text, fallback_language="en")
        assert language == "zh-CN"


class TestLanguageFlow:
    """语言检测 + 模板渲染流程测试。"""

    @pytest.mark.parametrize("lang,content,file_name", [
        ("zh-CN", "这是一个中文Python文件，包含测试代码", "chinese_code.py"),
        ("en", "This is an English Python file for testing", "english_code.py"),
        ("ja", "これは日本語のPythonコードテストファイルです", "japanese_code.py"),
        ("ko", "이것은 한국어 Python 코드 테스트 파일입니다", "korean_code.py"),
        ("ru", "Это русский тестовый файл Python кода", "russian_code.py"),
        ("ar", "هذا ملف اختبار كود بايثون عربي", "arabic_code.py"),
    ])
    def test_language_detection_to_template_flow(self, lang, content, file_name):
        """语言检测 -> output_language 注入模板 -> prompt 包含语言指令"""
        detected_lang = _detect_language_from_text(content, fallback_language="en")
        assert detected_lang == lang, f"Expected {lang}, got {detected_lang}"

        prompt = render_prompt(
            "semantic.code_summary",
            {"file_name": file_name, "content": content, "output_language": detected_lang},
        )
        assert f"Output Language: {lang}" in prompt


class TestOverviewGenerationFlow:
    """目录概述生成流程测试。"""

    @pytest.mark.parametrize("lang,file_summaries", [
        ("zh-CN", "[1] file1.py: 这是一个Python文件\n[2] file2.py: 这是另一个文件"),
        ("en", "[1] file1.py: This is a Python file\n[2] file2.py: Another file"),
        ("ja", "[1] file1.py: それはPythonファイルです\n[2] file2.py: これもPython"),
    ])
    def test_overview_generation_language_flow(self, lang, file_summaries):
        """目录摘要 -> 语言检测 -> overview 模板"""
        detected_lang = _detect_language_from_text(file_summaries, fallback_language="en")
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

    @pytest.fixture
    def temp_multilang_files(self):
        """创建包含多种语言内容的临时测试文件。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            files = {}

            files["chinese_py"] = tmppath / "chinese_code.py"
            files["chinese_py"].write_text("# 中文Python文件\ndef 你好():\n    print('你好世界')\n")

            files["english_py"] = tmppath / "english_code.py"
            files["english_py"].write_text("# English Python file\ndef hello():\n    print('Hello World')\n")

            files["japanese_py"] = tmppath / "japanese_code.py"
            files["japanese_py"].write_text("# 日本語Pythonファイル\ndef こんにちは():\n    print('こんにちは世界')\n")

            files["korean_py"] = tmppath / "korean_code.py"
            files["korean_py"].write_text("# 한국어 Python 파일\ndef 안녕하세요():\n    print('안녕하세요')\n")

            files["chinese_md"] = tmppath / "chinese_doc.md"
            files["chinese_md"].write_text("# 中文文档\n\n这是一个测试文档，包含中文技术内容。\n")

            files["english_md"] = tmppath / "english_doc.md"
            files["english_md"].write_text("# English Documentation\n\nThis is a test document with English content.\n")

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
    @pytest.mark.parametrize("file_key,file_name,expected_lang", [
        ("chinese_py", "chinese_code.py", "zh-CN"),
        ("english_py", "english_code.py", "en"),
        ("japanese_py", "japanese_code.py", "ja"),
        ("korean_py", "korean_code.py", "ko"),
        ("chinese_md", "chinese_doc.md", "zh-CN"),
        ("english_md", "english_doc.md", "en"),
    ])
    async def test_e2e_code_output_language(
        self, temp_multilang_files, file_key, file_name, expected_lang
    ):
        """端到端测试：文件 -> 语言检测 -> 生成对应语言摘要"""
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        content = Path(temp_multilang_files[file_key]).read_text()
        mock_vlm = LanguageAwareMockVLM()
        mock_viking_fs = self._create_mock_viking_fs(content)
        mock_config = self._create_mock_config(mock_vlm)

        with patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_viking_fs):
            with patch("openviking.storage.queuefs.semantic_processor.get_openviking_config", return_value=mock_config):
                processor = SemanticProcessor()
                processor._current_ctx = MagicMock()

                result = await processor._generate_text_summary(
                    file_path=temp_multilang_files[file_key],
                    file_name=file_name,
                    llm_sem=asyncio.Semaphore(1),
                )

                prompt_sent = mock_vlm.prompts_received[0]
                assert f"Output Language: {expected_lang}" in prompt_sent, \
                    f"{file_name}: Prompt missing Output Language: {expected_lang}"

                assert _verify_content_language(result["summary"], expected_lang), \
                    f"{file_name}: Content language mismatch. Expected {expected_lang}, got: {result['summary']}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content,file_name,expected_lang", [
        ("Это русский тестовый файл Python", "russian_code.py", "ru"),
        ("هذا ملف اختبار كود بايثون عربي", "arabic_code.py", "ar"),
    ])
    async def test_e2e_russian_arabic_output_language(self, content, file_name, expected_lang):
        """端到端测试：俄文和阿拉伯文内容"""
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        mock_vlm = LanguageAwareMockVLM()
        mock_viking_fs = self._create_mock_viking_fs(content)
        mock_config = self._create_mock_config(mock_vlm)

        with patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=mock_viking_fs):
            with patch("openviking.storage.queuefs.semantic_processor.get_openviking_config", return_value=mock_config):
                processor = SemanticProcessor()
                processor._current_ctx = MagicMock()

                result = await processor._generate_text_summary(
                    file_path=f"/tmp/{file_name}",
                    file_name=file_name,
                    llm_sem=asyncio.Semaphore(1),
                )

                prompt_sent = mock_vlm.prompts_received[0]
                assert f"Output Language: {expected_lang}" in prompt_sent

                assert _verify_content_language(result["summary"], expected_lang), \
                    f"{file_name}: Content language mismatch. Expected {expected_lang}, got: {result['summary']}"
