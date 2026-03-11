# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from openviking.session.memory_deduplicator import MemoryDeduplicator


class TestExtractFacetKey:
    def test_extract_with_chinese_colon(self):
        result = MemoryDeduplicator._extract_facet_key("饮食偏好：喜欢吃苹果和草莓")
        assert result == "饮食偏好"

    def test_extract_with_english_colon(self):
        result = MemoryDeduplicator._extract_facet_key("User preference: dark mode enabled")
        assert result == "user preference"

    def test_extract_with_hyphen(self):
        result = MemoryDeduplicator._extract_facet_key("Coding style - prefer type hints")
        assert result == "coding style"

    def test_extract_with_em_dash(self):
        result = MemoryDeduplicator._extract_facet_key("Work schedule — remote on Fridays")
        assert result == "work schedule"

    def test_extract_with_no_separator_returns_prefix(self):
        result = MemoryDeduplicator._extract_facet_key(
            "This is a long abstract without any separator"
        )
        assert len(result) <= 24
        assert result == "this is a long abstract"

    def test_extract_with_empty_string(self):
        result = MemoryDeduplicator._extract_facet_key("")
        assert result == ""

    def test_extract_with_none(self):
        result = MemoryDeduplicator._extract_facet_key(None)
        assert result == ""

    def test_extract_normalizes_whitespace(self):
        result = MemoryDeduplicator._extract_facet_key("  Multiple   spaces  :  value  ")
        assert result == "multiple spaces"

    def test_extract_with_short_text_no_separator(self):
        result = MemoryDeduplicator._extract_facet_key("Short")
        assert result == "short"

    def test_extract_returns_lowercase(self):
        result = MemoryDeduplicator._extract_facet_key("FOOD PREFERENCE: pizza")
        assert result == "food preference"

    def test_extract_with_separator_at_start(self):
        result = MemoryDeduplicator._extract_facet_key(": starts with separator")
        assert result == ": starts with"

    def test_extract_with_multiple_separators_uses_first(self):
        result = MemoryDeduplicator._extract_facet_key("Topic: Subtopic - Detail")
        assert result == "topic"


class TestCosineSimilarity:
    def test_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec, vec)
        assert abs(result - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result) < 1e-9

    def test_opposite_vectors(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [-1.0, -2.0, -3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result + 1.0) < 1e-9

    def test_different_length_vectors(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [1.0, 2.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_zero_vector_a(self):
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_zero_vector_b(self):
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [0.0, 0.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_both_zero_vectors(self):
        vec_a = [0.0, 0.0, 0.0]
        vec_b = [0.0, 0.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert result == 0.0

    def test_partial_similarity(self):
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [1.0, 1.0, 0.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        expected = 1.0 / (2.0**0.5)
        assert abs(result - expected) < 1e-9

    def test_negative_values(self):
        vec_a = [1.0, -2.0, 3.0]
        vec_b = [-1.0, 2.0, 3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert 0 < result < 1

    def test_single_element_vectors(self):
        vec_a = [5.0]
        vec_b = [3.0]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result - 1.0) < 1e-9

    def test_large_vectors(self):
        vec_a = [float(i) for i in range(100)]
        vec_b = [float(i * 2) for i in range(100)]
        result = MemoryDeduplicator._cosine_similarity(vec_a, vec_b)
        assert abs(result - 1.0) < 1e-6
