# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import pytest

from openviking.session.memory_extractor import (
    FIELD_MAX_LENGTHS,
    MemoryExtractor,
)


@pytest.fixture
def extractor():
    return MemoryExtractor()


class TestParseToolStatistics:
    def test_parse_chinese_format_full(self, extractor):
        content = """
Tool: test_tool

总调用次数: 100
成功率: 85.0%（85 成功，15 失败）
平均耗时: 150.5ms
平均Token: 500
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 100
        assert stats["success_count"] == 85
        assert stats["fail_count"] == 15
        assert stats["total_time_ms"] == 15050.0
        assert stats["total_tokens"] == 50000

    def test_parse_chinese_format_with_colon(self, extractor):
        content = """
总调用次数：200
成功率：90.5%（181 成功，19 失败）
平均耗时：200.0ms
平均Token：800
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 200
        assert stats["success_count"] == 181
        assert stats["fail_count"] == 19

    def test_parse_english_format_full(self, extractor):
        content = """
Tool: test_tool

Based on 50 historical calls:
- Success rate: 80.0% (40 successful, 10 failed)
- Avg time: 1.5s, Avg tokens: 600
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 50
        assert stats["success_count"] == 40
        assert stats["fail_count"] == 10
        assert stats["total_time_ms"] == 75000.0
        assert stats["total_tokens"] == 30000

    def test_parse_english_format_ms(self, extractor):
        content = """
Based on 30 historical calls:
- Success rate: 90.0% (27 successful, 3 failed)
- Avg time: 250.5ms, Avg tokens: 400
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 30
        assert stats["success_count"] == 27
        assert stats["fail_count"] == 3
        assert stats["total_time_ms"] == 7515.0
        assert stats["total_tokens"] == 12000

    def test_parse_chinese_success_rate_only(self, extractor):
        content = """
总调用次数: 100
成功率: 75.0%
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 100
        assert stats["success_count"] == 75
        assert stats["fail_count"] == 25

    def test_parse_english_success_rate_only(self, extractor):
        content = """
Based on 80 historical calls:
- Success rate: 87.5%
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 80
        assert stats["success_count"] == 70
        assert stats["fail_count"] == 10

    def test_parse_empty_content(self, extractor):
        stats = extractor._parse_tool_statistics("")
        assert stats["total_calls"] == 0
        assert stats["success_count"] == 0
        assert stats["fail_count"] == 0
        assert stats["total_time_ms"] == 0
        assert stats["total_tokens"] == 0

    def test_parse_chinese_avg_time_seconds(self, extractor):
        content = """
总调用次数: 10
平均耗时: 2.5s
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 10
        assert stats["total_time_ms"] == 25000.0

    def test_parse_no_total_calls_infers_from_success_fail(self, extractor):
        content = """
成功率: 80.0%（40 成功，10 失败）
"""
        stats = extractor._parse_tool_statistics(content)
        assert stats["total_calls"] == 50
        assert stats["success_count"] == 40
        assert stats["fail_count"] == 10


class TestMergeToolStatistics:
    def test_merge_basic(self, extractor):
        existing = {
            "total_calls": 100,
            "success_count": 80,
            "fail_count": 20,
            "total_time_ms": 10000.0,
            "total_tokens": 50000,
        }
        new = {
            "total_calls": 50,
            "success_count": 45,
            "fail_count": 5,
            "total_time_ms": 5000.0,
            "total_tokens": 25000,
        }
        merged = extractor._merge_tool_statistics(existing, new)
        assert merged["total_calls"] == 150
        assert merged["success_count"] == 125
        assert merged["fail_count"] == 25
        assert merged["total_time_ms"] == 15000.0
        assert merged["total_tokens"] == 75000
        assert abs(merged["avg_time_ms"] - 100.0) < 0.01
        assert abs(merged["avg_tokens"] - 500.0) < 0.01
        assert abs(merged["success_rate"] - 0.8333) < 0.01

    def test_merge_with_zero_existing(self, extractor):
        existing = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }
        new = {
            "total_calls": 10,
            "success_count": 8,
            "fail_count": 2,
            "total_time_ms": 1000.0,
            "total_tokens": 5000,
        }
        merged = extractor._merge_tool_statistics(existing, new)
        assert merged["total_calls"] == 10
        assert merged["success_count"] == 8
        assert merged["fail_count"] == 2

    def test_merge_with_zero_new(self, extractor):
        existing = {
            "total_calls": 20,
            "success_count": 15,
            "fail_count": 5,
            "total_time_ms": 2000.0,
            "total_tokens": 10000,
        }
        new = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }
        merged = extractor._merge_tool_statistics(existing, new)
        assert merged["total_calls"] == 20
        assert merged["success_count"] == 15
        assert merged["fail_count"] == 5

    def test_merge_both_zero(self, extractor):
        existing = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }
        new = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }
        merged = extractor._merge_tool_statistics(existing, new)
        assert merged["total_calls"] == 0
        assert merged["avg_time_ms"] == 0
        assert merged["avg_tokens"] == 0
        assert merged["success_rate"] == 0


class TestGenerateToolMemoryContent:
    def test_generate_basic(self, extractor):
        with patch.object(extractor, "_get_tool_static_description", return_value="A test tool"):
            stats = {
                "total_calls": 100,
                "success_count": 85,
                "fail_count": 15,
                "avg_time_ms": 150.5,
                "avg_tokens": 500,
                "success_rate": 0.85,
            }
            guidelines = "Use this tool for testing purposes."
            content = extractor._generate_tool_memory_content("test_tool", stats, guidelines)
            assert "Tool: test_tool" in content
            assert "Based on 100 historical calls:" in content
            assert "Success rate: 85.0%" in content
            assert "85 successful, 15 failed" in content
            assert "Use this tool for testing purposes." in content

    def test_generate_with_fields(self, extractor):
        with patch.object(extractor, "_get_tool_static_description", return_value="A test tool"):
            stats = {
                "total_calls": 50,
                "success_count": 40,
                "fail_count": 10,
                "avg_time_ms": 200.0,
                "avg_tokens": 600,
                "success_rate": 0.8,
            }
            fields = {
                "best_for": "Data processing tasks",
                "optimal_params": "batch_size=100",
                "common_failures": "Timeout on large inputs",
                "recommendation": "Use with small batches",
            }
            content = extractor._generate_tool_memory_content("test_tool", stats, "", fields=fields)
            assert "Best for: Data processing tasks" in content
            assert "Optimal params: batch_size=100" in content
            assert "Common failures: Timeout on large inputs" in content
            assert "Recommendation: Use with small batches" in content

    def test_generate_with_empty_fields(self, extractor):
        with patch.object(extractor, "_get_tool_static_description", return_value="A test tool"):
            stats = {
                "total_calls": 10,
                "success_count": 10,
                "fail_count": 0,
                "avg_time_ms": 100.0,
                "avg_tokens": 300,
                "success_rate": 1.0,
            }
            content = extractor._generate_tool_memory_content("test_tool", stats, "", fields={})
            assert "Best for: " in content
            assert "Optimal params: " in content

    def test_generate_extracts_fields_from_guidelines(self, extractor):
        with patch.object(extractor, "_get_tool_static_description", return_value="A test tool"):
            stats = {
                "total_calls": 20,
                "success_count": 18,
                "fail_count": 2,
                "avg_time_ms": 50.0,
                "avg_tokens": 200,
                "success_rate": 0.9,
            }
            guidelines = """
Best for: Quick data validation
Optimal params: strict_mode=true
Common failures: Invalid input format
Recommendation: Always validate input first
"""
            content = extractor._generate_tool_memory_content("test_tool", stats, guidelines)
            assert "Best for: Quick data validation" in content
            assert "Optimal params: strict_mode=true" in content


class TestParseSkillStatistics:
    def test_parse_chinese_format_full(self, extractor):
        content = """
Skill: test_skill

总执行次数: 100
成功率: 90.0%（90 成功，10 失败）
"""
        stats = extractor._parse_skill_statistics(content)
        assert stats["total_executions"] == 100
        assert stats["success_count"] == 90
        assert stats["fail_count"] == 10

    def test_parse_chinese_format_with_colon(self, extractor):
        content = """
总执行次数：50
成功率：80.0%（40 成功，10 失败）
"""
        stats = extractor._parse_skill_statistics(content)
        assert stats["total_executions"] == 50
        assert stats["success_count"] == 40
        assert stats["fail_count"] == 10

    def test_parse_english_format_full(self, extractor):
        content = """
Skill: test_skill

Based on 75 historical executions:
- Success rate: 85.0% (64 successful, 11 failed)
"""
        stats = extractor._parse_skill_statistics(content)
        assert stats["total_executions"] == 75
        assert stats["success_count"] == 64
        assert stats["fail_count"] == 11

    def test_parse_english_success_rate_only(self, extractor):
        content = """
Based on 60 historical executions:
- Success rate: 75.0%
"""
        stats = extractor._parse_skill_statistics(content)
        assert stats["total_executions"] == 60
        assert stats["success_count"] == 45
        assert stats["fail_count"] == 15

    def test_parse_empty_content(self, extractor):
        stats = extractor._parse_skill_statistics("")
        assert stats["total_executions"] == 0
        assert stats["success_count"] == 0
        assert stats["fail_count"] == 0

    def test_parse_no_total_executions_infers_from_success_fail(self, extractor):
        content = """
成功率: 70.0%（35 成功，15 失败）
"""
        stats = extractor._parse_skill_statistics(content)
        assert stats["total_executions"] == 50
        assert stats["success_count"] == 35
        assert stats["fail_count"] == 15


class TestMergeSkillStatistics:
    def test_merge_basic(self, extractor):
        existing = {
            "total_executions": 100,
            "success_count": 90,
            "fail_count": 10,
        }
        new = {
            "total_executions": 50,
            "success_count": 45,
            "fail_count": 5,
        }
        merged = extractor._merge_skill_statistics(existing, new)
        assert merged["total_executions"] == 150
        assert merged["success_count"] == 135
        assert merged["fail_count"] == 15
        assert abs(merged["success_rate"] - 0.9) < 0.01

    def test_merge_with_zero_existing(self, extractor):
        existing = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        new = {
            "total_executions": 20,
            "success_count": 18,
            "fail_count": 2,
        }
        merged = extractor._merge_skill_statistics(existing, new)
        assert merged["total_executions"] == 20
        assert merged["success_count"] == 18
        assert merged["fail_count"] == 2

    def test_merge_with_zero_new(self, extractor):
        existing = {
            "total_executions": 30,
            "success_count": 25,
            "fail_count": 5,
        }
        new = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        merged = extractor._merge_skill_statistics(existing, new)
        assert merged["total_executions"] == 30
        assert merged["success_count"] == 25
        assert merged["fail_count"] == 5

    def test_merge_both_zero(self, extractor):
        existing = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        new = {
            "total_executions": 0,
            "success_count": 0,
            "fail_count": 0,
        }
        merged = extractor._merge_skill_statistics(existing, new)
        assert merged["total_executions"] == 0
        assert merged["success_rate"] == 0


class TestGenerateSkillMemoryContent:
    def test_generate_basic(self, extractor):
        stats = {
            "total_executions": 100,
            "success_count": 90,
            "fail_count": 10,
            "success_rate": 0.9,
        }
        guidelines = "Use this skill for data processing."
        content = extractor._generate_skill_memory_content("test_skill", stats, guidelines)
        assert "Skill: test_skill" in content
        assert "Based on 100 historical executions:" in content
        assert "Success rate: 90.0%" in content
        assert "90 successful, 10 failed" in content
        assert "Use this skill for data processing." in content

    def test_generate_with_fields(self, extractor):
        stats = {
            "total_executions": 50,
            "success_count": 45,
            "fail_count": 5,
            "success_rate": 0.9,
        }
        fields = {
            "best_for": "Automated workflows",
            "recommended_flow": "Step 1 -> Step 2 -> Step 3",
            "key_dependencies": "Database connection",
            "common_failures": "Network timeout",
            "recommendation": "Use with retry logic",
        }
        content = extractor._generate_skill_memory_content("test_skill", stats, "", fields=fields)
        assert "Best for: Automated workflows" in content
        assert "Recommended flow: Step 1 -> Step 2 -> Step 3" in content
        assert "Key dependencies: Database connection" in content
        assert "Common failures: Network timeout" in content
        assert "Recommendation: Use with retry logic" in content

    def test_generate_with_empty_fields(self, extractor):
        stats = {
            "total_executions": 10,
            "success_count": 10,
            "fail_count": 0,
            "success_rate": 1.0,
        }
        content = extractor._generate_skill_memory_content("test_skill", stats, "", fields={})
        assert "Best for: " in content
        assert "Recommended flow: " in content

    def test_generate_extracts_fields_from_guidelines(self, extractor):
        stats = {
            "total_executions": 20,
            "success_count": 18,
            "fail_count": 2,
            "success_rate": 0.9,
        }
        guidelines = """
Best for: Complex data transformations
Recommended flow: Validate -> Transform -> Store
Key dependencies: S3 bucket access
Common failures: Permission denied
Recommendation: Check permissions first
"""
        content = extractor._generate_skill_memory_content("test_skill", stats, guidelines)
        assert "Best for: Complex data transformations" in content
        assert "Recommended flow: Validate -> Transform -> Store" in content


class TestMergeKvField:
    @pytest.mark.asyncio
    async def test_merge_both_empty(self, extractor):
        result = await extractor._merge_kv_field("", "", "best_for")
        assert result == ""

    @pytest.mark.asyncio
    async def test_merge_existing_empty(self, extractor):
        result = await extractor._merge_kv_field("", "new value", "best_for")
        assert result == "new value"

    @pytest.mark.asyncio
    async def test_merge_new_empty(self, extractor):
        result = await extractor._merge_kv_field("existing value", "", "best_for")
        assert result == "existing value"

    @pytest.mark.asyncio
    async def test_merge_identical_values(self, extractor):
        result = await extractor._merge_kv_field("same value", "same value", "best_for")
        assert result == "same value"

    @pytest.mark.asyncio
    async def test_merge_different_values(self, extractor):
        result = await extractor._merge_kv_field("value A", "value B", "best_for")
        assert "value A" in result
        assert "value B" in result
        assert ";" in result

    @pytest.mark.asyncio
    async def test_merge_with_semicolon_separator(self, extractor):
        result = await extractor._merge_kv_field("item1; item2", "item3", "best_for")
        assert "item1" in result
        assert "item2" in result
        assert "item3" in result

    @pytest.mark.asyncio
    async def test_merge_deduplicates(self, extractor):
        result = await extractor._merge_kv_field("item1; item2", "item2; item3", "best_for")
        assert result.count("item2") == 1
        assert "item1" in result
        assert "item3" in result

    @pytest.mark.asyncio
    async def test_merge_respects_max_length(self, extractor):
        long_value = "x" * 600
        result = await extractor._merge_kv_field(long_value, "new", "best_for")
        assert len(result) <= FIELD_MAX_LENGTHS["best_for"]

    @pytest.mark.asyncio
    async def test_merge_with_newline_separator(self, extractor):
        result = await extractor._merge_kv_field("item1\nitem2", "item3", "best_for")
        assert "item1" in result
        assert "item2" in result
        assert "item3" in result


class TestSmartTruncate:
    def test_no_truncation_needed(self, extractor):
        text = "short text"
        result = extractor._smart_truncate(text, 100)
        assert result == text

    def test_truncate_at_semicolon(self, extractor):
        text = "item1; item2; item3; item4; item5"
        result = extractor._smart_truncate(text, 25)
        assert len(result) <= 25
        assert result.endswith(";") or result.count(";") >= 1

    def test_truncate_at_space(self, extractor):
        text = "word1 word2 word3 word4 word5"
        result = extractor._smart_truncate(text, 20)
        assert len(result) <= 20

    def test_truncate_fallback(self, extractor):
        text = "abcdefghijklmnopqrstuvwxyz"
        result = extractor._smart_truncate(text, 10)
        assert len(result) == 10
        assert result == "abcdefghij"

    def test_truncate_empty_string(self, extractor):
        result = extractor._smart_truncate("", 10)
        assert result == ""

    def test_truncate_exact_length(self, extractor):
        text = "exactly10!"
        result = extractor._smart_truncate(text, 10)
        assert result == text


class TestComputeStatisticsDerived:
    def test_compute_with_calls(self, extractor):
        stats = {
            "total_calls": 100,
            "success_count": 80,
            "fail_count": 20,
            "total_time_ms": 10000.0,
            "total_tokens": 50000,
        }
        result = extractor._compute_statistics_derived(stats)
        assert abs(result["avg_time_ms"] - 100.0) < 0.01
        assert abs(result["avg_tokens"] - 500.0) < 0.01
        assert abs(result["success_rate"] - 0.8) < 0.01

    def test_compute_with_zero_calls(self, extractor):
        stats = {
            "total_calls": 0,
            "success_count": 0,
            "fail_count": 0,
            "total_time_ms": 0,
            "total_tokens": 0,
        }
        result = extractor._compute_statistics_derived(stats)
        assert result["avg_time_ms"] == 0
        assert result["avg_tokens"] == 0
        assert result["success_rate"] == 0

    def test_compute_preserves_original_values(self, extractor):
        stats = {
            "total_calls": 50,
            "success_count": 40,
            "fail_count": 10,
            "total_time_ms": 5000.0,
            "total_tokens": 25000,
        }
        result = extractor._compute_statistics_derived(stats)
        assert result["total_calls"] == 50
        assert result["success_count"] == 40
        assert result["fail_count"] == 10
        assert result["total_time_ms"] == 5000.0
        assert result["total_tokens"] == 25000


class TestFormatDuration:
    def test_format_zero(self, extractor):
        result = extractor._format_duration(0)
        assert result == "0s"

    def test_format_milliseconds(self, extractor):
        result = extractor._format_duration(500)
        assert result == "500ms"

    def test_format_seconds(self, extractor):
        result = extractor._format_duration(1500)
        assert result == "1.5s"

    def test_format_large_seconds(self, extractor):
        result = extractor._format_duration(10000)
        assert result == "10.0s"

    def test_format_none(self, extractor):
        result = extractor._format_duration(None)
        assert result == "N/A"

    def test_format_negative(self, extractor):
        result = extractor._format_duration(-100)
        assert result == "0s"

    def test_format_invalid_type(self, extractor):
        result = extractor._format_duration("invalid")
        assert result == "N/A"

    def test_format_exactly_one_second(self, extractor):
        result = extractor._format_duration(1000)
        assert result == "1.0s"

    def test_format_just_under_one_second(self, extractor):
        result = extractor._format_duration(999)
        assert result == "999ms"


class TestExtractContentField:
    def test_extract_with_chinese_colon(self, extractor):
        content = "Best for：数据处理任务"
        result = extractor._extract_content_field(content, ["Best for"])
        assert result == "数据处理任务"

    def test_extract_with_english_colon(self, extractor):
        content = "Best for: data processing tasks"
        result = extractor._extract_content_field(content, ["Best for"])
        assert result == "data processing tasks"

    def test_extract_with_multiple_keys(self, extractor):
        content = "最佳场景: 快速验证"
        result = extractor._extract_content_field(content, ["Best for", "最佳场景"])
        assert result == "快速验证"

    def test_extract_not_found(self, extractor):
        content = "Some other content"
        result = extractor._extract_content_field(content, ["Best for"])
        assert result == ""

    def test_extract_empty_content(self, extractor):
        result = extractor._extract_content_field("", ["Best for"])
        assert result == ""


class TestCompactBlock:
    def test_compact_basic(self, extractor):
        text = "Line 1\nLine 2\nLine 3"
        result = extractor._compact_block(text)
        assert result == "Line 1; Line 2; Line 3"

    def test_compact_with_prefixes(self, extractor):
        text = "> Point 1\n- Point 2\n* Point 3"
        result = extractor._compact_block(text)
        assert "Point 1" in result
        assert "Point 2" in result
        assert "Point 3" in result

    def test_compact_empty(self, extractor):
        result = extractor._compact_block("")
        assert result == ""

    def test_compact_whitespace_only(self, extractor):
        result = extractor._compact_block("   \n   \n   ")
        assert result == ""


class TestExtractToolMemoryContextFieldsFromText:
    def test_extract_all_fields(self, extractor):
        text = """
Best for: Data processing
Optimal params: batch_size=100
Common failures: Timeout
Recommendation: Use small batches
"""
        result = extractor._extract_tool_memory_context_fields_from_text(text)
        assert result["best_for"] == "Data processing"
        assert result["optimal_params"] == "batch_size=100"
        assert result["common_failures"] == "Timeout"
        assert result["recommendation"] == "Use small batches"

    def test_extract_partial_fields(self, extractor):
        text = """
Best for: Testing
Recommendation: Run in dev mode
"""
        result = extractor._extract_tool_memory_context_fields_from_text(text)
        assert result["best_for"] == "Testing"
        assert result["optimal_params"] == ""
        assert result["common_failures"] == ""
        assert result["recommendation"] == "Run in dev mode"

    def test_extract_chinese_fields(self, extractor):
        text = """
最佳场景: 数据处理
最优参数: 批量大小=100
常见失败: 超时
推荐: 使用小批量
"""
        result = extractor._extract_tool_memory_context_fields_from_text(text)
        assert result["best_for"] == "数据处理"
        assert result["optimal_params"] == "批量大小=100"
        assert result["common_failures"] == "超时"
        assert result["recommendation"] == "使用小批量"


class TestExtractSkillMemoryContextFieldsFromText:
    def test_extract_all_fields(self, extractor):
        text = """
Best for: Automated workflows
Recommended flow: Step1 -> Step2 -> Step3
Key dependencies: Database
Common failures: Connection error
Recommendation: Use connection pool
"""
        result = extractor._extract_skill_memory_context_fields_from_text(text)
        assert result["best_for"] == "Automated workflows"
        assert result["recommended_flow"] == "Step1 -> Step2 -> Step3"
        assert result["key_dependencies"] == "Database"
        assert result["common_failures"] == "Connection error"
        assert result["recommendation"] == "Use connection pool"

    def test_extract_chinese_fields(self, extractor):
        text = """
最佳场景: 自动化工作流
推荐流程: 步骤1 -> 步骤2 -> 步骤3
关键依赖: 数据库
常见失败: 连接错误
推荐: 使用连接池
"""
        result = extractor._extract_skill_memory_context_fields_from_text(text)
        assert result["best_for"] == "自动化工作流"
        assert result["recommended_flow"] == "步骤1 -> 步骤2 -> 步骤3"
        assert result["key_dependencies"] == "数据库"
        assert result["common_failures"] == "连接错误"
        assert result["recommendation"] == "使用连接池"


class TestFormatMs:
    def test_format_zero(self, extractor):
        result = extractor._format_ms(0)
        assert result == "0.000ms"

    def test_format_normal_value(self, extractor):
        result = extractor._format_ms(123.456)
        assert result == "123.456ms"

    def test_format_very_small_value(self, extractor):
        result = extractor._format_ms(0.000123)
        assert "ms" in result
        assert float(result.replace("ms", "")) > 0

    def test_format_large_value(self, extractor):
        result = extractor._format_ms(9999.999)
        assert result == "9999.999ms"
