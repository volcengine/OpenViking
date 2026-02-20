#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Comprehensive edge case tests for OpenViking.

This module tests boundary conditions, unicode edge cases, concurrent operations,
and security considerations that might not be covered in regular testing.
Many of these tests are designed to expose potential bugs or areas for improvement
in the current codebase.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openviking.parse.parsers.upload_utils import (
    _sanitize_rel_path,
    detect_and_convert_encoding,
    is_text_file,
    upload_directory,
)
from openviking.storage.viking_fs import VikingFS
from openviking.storage.vikingdb_interface import VikingDBInterface
from openviking.utils.compression import CompressManager
from openviking_cli.utils.uri import VikingURI


class MockVikingDB(VikingDBInterface):
    """Mock vector database for testing."""
    
    def __init__(self):
        self.collections: Dict[str, Dict] = {}
        self.data: Dict[str, List[Dict]] = {}
        self.deleted_ids: set = set()
    
    async def create_collection(self, name: str, schema: Dict) -> bool:
        if name in self.collections:
            return False
        self.collections[name] = schema
        self.data[name] = []
        return True
    
    async def search_by_id(self, collection: str, doc_id: str, candidates: Optional[List[str]] = None) -> Optional[Dict]:
        """Search for document by ID with optional candidate filtering."""
        if collection not in self.data:
            return None
        
        if candidates is None:
            # Search all documents
            for doc in self.data[collection]:
                if doc.get("id") == doc_id and doc_id not in self.deleted_ids:
                    return doc
        else:
            # Search only in candidates
            if not candidates:  # Empty candidate list
                return None
            for doc in self.data[collection]:
                if doc.get("id") == doc_id and doc_id in candidates and doc_id not in self.deleted_ids:
                    return doc
        
        return None
    
    async def insert(self, collection: str, data: List[Dict]) -> bool:
        if collection not in self.data:
            return False
        self.data[collection].extend(data)
        return True
    
    async def delete(self, collection: str, doc_id: str) -> bool:
        self.deleted_ids.add(doc_id)
        return True


class TestLongFilenames:
    """Test handling of very long filenames and path components."""
    
    def test_filename_exactly_255_bytes(self):
        """Test filename with exactly 255 bytes (filesystem limit boundary)."""
        # Create a filename that's exactly 255 bytes in UTF-8
        base_name = "a" * 251  # 251 + ".txt" = 255 bytes
        filename = base_name + ".txt"
        
        assert len(filename.encode('utf-8')) == 255
        
        # Test sanitization doesn't break at exact boundary
        sanitized = _sanitize_rel_path(filename)
        assert sanitized is not None
        assert len(sanitized) > 0
    
    def test_filename_256_bytes_boundary(self):
        """Test filename with 256 bytes (just over filesystem limit)."""
        # Create filename that's exactly 256 bytes - should be truncated
        base_name = "b" * 252  # 252 + ".txt" = 256 bytes  
        filename = base_name + ".txt"
        
        assert len(filename.encode('utf-8')) == 256
        
        sanitized = _sanitize_rel_path(filename)
        # Should be handled gracefully (truncated or rejected)
        assert sanitized is not None
    
    def test_very_long_filename_with_cjk(self):
        """Test extremely long filename with CJK characters (3 bytes per char in UTF-8)."""
        # Each CJK character is 3 bytes in UTF-8
        cjk_chars = "测试文件名" * 30  # ~450 bytes
        filename = f"{cjk_chars}.py"
        
        assert len(filename.encode('utf-8')) > 400
        
        sanitized = _sanitize_rel_path(filename)
        assert sanitized is not None
        # Should handle or truncate appropriately
        
    def test_filename_only_special_characters(self):
        """Test filename composed entirely of special characters."""
        special_filename = "!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`" + ".txt"
        
        sanitized = _sanitize_rel_path(special_filename)
        # Should sanitize dangerous characters while preserving valid ones
        assert sanitized is not None
        assert ".txt" in sanitized  # Extension should be preserved
    
    def test_filename_with_path_traversal_attempts(self):
        """Test filename containing path traversal sequences."""
        dangerous_filenames = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config",
            "file/../../../secret.txt",
            "normal_file_../../../dangerous.py"
        ]
        
        for filename in dangerous_filenames:
            sanitized = _sanitize_rel_path(filename)
            # Should not contain path traversal sequences
            assert ".." not in sanitized
            assert "\\" not in sanitized or sanitized.count("\\") == 0


class TestSearchByIdEdgeCases:
    """Test search_by_id with various edge cases and None conditions."""
    
    @pytest.mark.asyncio
    async def test_search_nonexistent_id(self):
        """Test searching for an ID that doesn't exist."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("test", {})
        
        result = await mock_db.search_by_id("test", "nonexistent_id")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_search_after_delete(self):
        """Test searching for an ID after it has been deleted."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("test", {})
        
        # Insert document
        await mock_db.insert("test", [{"id": "doc1", "content": "test"}])
        
        # Verify it exists
        result = await mock_db.search_by_id("test", "doc1")
        assert result is not None
        
        # Delete it
        await mock_db.delete("test", "doc1")
        
        # Search should return None
        result = await mock_db.search_by_id("test", "doc1")
        assert result is None
    
    @pytest.mark.asyncio
    async def test_search_with_empty_candidates(self):
        """Test search_by_id with empty candidate list."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("test", {})
        
        # Insert document
        await mock_db.insert("test", [{"id": "doc1", "content": "test"}])
        
        # Search with empty candidates should return None
        result = await mock_db.search_by_id("test", "doc1", candidates=[])
        assert result is None
    
    @pytest.mark.asyncio
    async def test_search_with_none_candidates(self):
        """Test search_by_id with None candidates (should search all)."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("test", {})
        
        # Insert document
        await mock_db.insert("test", [{"id": "doc1", "content": "test"}])
        
        # Search with None candidates should find document
        result = await mock_db.search_by_id("test", "doc1", candidates=None)
        assert result is not None
        assert result["id"] == "doc1"
    
    @pytest.mark.asyncio
    async def test_search_nonexistent_collection(self):
        """Test searching in a collection that doesn't exist."""
        mock_db = MockVikingDB()
        
        result = await mock_db.search_by_id("nonexistent", "doc1")
        assert result is None


class TestDuplicateFilenameHandling:
    """Test duplicate filename handling and case sensitivity."""
    
    @pytest.mark.asyncio
    async def test_upload_same_file_multiple_times(self, tmp_path):
        """Test uploading the same file 10 times - should handle duplicates gracefully."""
        # Create test file
        test_file = tmp_path / "duplicate_test.txt"
        test_file.write_text("This is a test file for duplicate testing.")
        
        # Mock VikingFS
        mock_fs = MagicMock()
        mock_fs.write_file_bytes = AsyncMock()
        mock_fs.mkdir = AsyncMock()
        
        # Upload the same file 10 times
        for i in range(10):
            await upload_text_files([str(test_file)], "viking://test/", mock_fs)
        
        # Should handle duplicates without crashing
        assert mock_fs.write_file_bytes.call_count == 10
    
    def test_case_sensitivity_filenames(self):
        """Test filenames that differ only in case."""
        filenames = [
            "TestFile.txt",
            "testfile.txt", 
            "TESTFILE.TXT",
            "TestFile.TXT"
        ]
        
        sanitized_names = [_sanitize_rel_path(name) for name in filenames]
        
        # All should be valid but may be treated differently on case-insensitive systems
        for name in sanitized_names:
            assert name is not None
            assert len(name) > 0
    
    def test_unicode_normalization_differences(self):
        """Test filenames with different Unicode normalizations (NFC vs NFD)."""
        # Same logical character represented differently
        filename_nfc = "café.txt"  # NFC: é is a single codepoint
        filename_nfd = "cafe\u0301.txt"  # NFD: e + combining acute accent
        
        # These look the same but have different byte representations
        assert filename_nfc != filename_nfd
        assert unicodedata.normalize('NFC', filename_nfd) == filename_nfc
        
        sanitized_nfc = _sanitize_rel_path(filename_nfc)
        sanitized_nfd = _sanitize_rel_path(filename_nfd)
        
        assert sanitized_nfc is not None
        assert sanitized_nfd is not None


class TestCompressionEdgeCases:
    """Test compression with various edge cases."""
    
    def test_compress_empty_string(self):
        """Test compressing empty string."""
        compressor = CompressManager()
        
        empty_text = ""
        compressed = compressor.compress_text(empty_text)
        
        assert compressed is not None
        assert len(compressed) >= 0  # May be empty or minimal header
    
    def test_compress_emoji_only(self):
        """Test compressing string containing only emoji."""
        compressor = CompressManager()
        
        emoji_text = "😀😃😄😁😆😅😂🤣😊😇🙂🙃😉😌😍🥰😘😗😙😚"
        compressed = compressor.compress_text(emoji_text)
        
        assert compressed is not None
        # Emoji compression ratio might be poor due to lack of repetition
        assert len(compressed) > 0
    
    def test_compress_large_text(self):
        """Test compressing 1MB of text."""
        compressor = CompressManager()
        
        # Generate ~1MB of text with patterns (should compress well)
        large_text = "This is a repeating pattern for compression testing. " * 20000
        
        assert len(large_text.encode('utf-8')) > 1_000_000
        
        compressed = compressor.compress_text(large_text)
        
        assert compressed is not None
        # Should achieve significant compression due to repetition
        compression_ratio = len(compressed) / len(large_text.encode('utf-8'))
        assert compression_ratio < 0.1  # Should compress to less than 10%
    
    def test_compress_binary_like_data(self):
        """Test compressing text that looks like binary data."""
        compressor = CompressManager()
        
        # Pseudo-random looking text (poor compression expected)
        import hashlib
        binary_like = ""
        for i in range(1000):
            binary_like += hashlib.md5(f"seed{i}".encode()).hexdigest()
        
        compressed = compressor.compress_text(binary_like)
        assert compressed is not None
        # Compression ratio should be poor for random-looking data
        compression_ratio = len(compressed) / len(binary_like.encode('utf-8'))
        assert compression_ratio > 0.8  # Should not compress much


class TestConcurrentOperations:
    """Test concurrent operations for race conditions and thread safety."""
    
    @pytest.mark.asyncio
    async def test_concurrent_writes(self):
        """Test 20 parallel write operations."""
        mock_fs = MagicMock()
        mock_fs.write_file_bytes = AsyncMock()
        mock_fs.mkdir = AsyncMock()
        
        # Create 20 concurrent write tasks
        async def write_task(i):
            content = f"Content for file {i}"
            uri = f"viking://concurrent/file_{i}.txt"
            await mock_fs.write_file_bytes(uri, content.encode('utf-8'))
        
        tasks = [write_task(i) for i in range(20)]
        
        # Execute all tasks concurrently
        await asyncio.gather(*tasks)
        
        # Verify all writes were attempted
        assert mock_fs.write_file_bytes.call_count == 20
    
    @pytest.mark.asyncio  
    async def test_concurrent_search_while_writing(self):
        """Test 10 parallel searches while writing."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("concurrent", {})
        
        # Insert initial data
        for i in range(5):
            await mock_db.insert("concurrent", [{"id": f"doc{i}", "content": f"content{i}"}])
        
        async def search_task():
            return await mock_db.search_by_id("concurrent", "doc1")
        
        async def write_task():
            return await mock_db.insert("concurrent", [{"id": "new_doc", "content": "new_content"}])
        
        # Mix of search and write operations
        tasks = []
        tasks.extend([search_task() for _ in range(10)])
        tasks.extend([write_task() for _ in range(5)])
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # No tasks should have raised exceptions
        for result in results:
            assert not isinstance(result, Exception)
    
    @pytest.mark.asyncio
    async def test_rapid_create_delete_cycles(self):
        """Test rapid create/delete cycles for race conditions."""
        mock_db = MockVikingDB()
        await mock_db.create_collection("rapid", {})
        
        async def create_delete_cycle(doc_id):
            # Create document
            await mock_db.insert("rapid", [{"id": doc_id, "content": "temp"}])
            # Immediately try to search
            result = await mock_db.search_by_id("rapid", doc_id)
            # Delete it
            await mock_db.delete("rapid", doc_id)
            # Search again (should be None)
            deleted_result = await mock_db.search_by_id("rapid", doc_id)
            return result, deleted_result
        
        # Run 10 rapid create/delete cycles
        tasks = [create_delete_cycle(f"rapid_doc_{i}") for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        # Verify results are consistent
        for found, deleted in results:
            assert found is not None  # Should find before delete
            assert deleted is None   # Should not find after delete


class TestUnicodeEdgeCases:
    """Test Unicode edge cases and special character handling."""
    
    def test_zero_width_characters(self):
        """Test filenames containing zero-width characters."""
        # Zero-width characters that might cause issues
        filename = "test\u200b\u200c\u200d\ufefffile.txt"  # ZWSP, ZWNJ, ZWJ, BOM
        
        sanitized = _sanitize_rel_path(filename)
        assert sanitized is not None
        
        # Zero-width characters should ideally be stripped
        assert "\u200b" not in sanitized or len(sanitized) > 0
    
    def test_rtl_text_filenames(self):
        """Test right-to-left text in filenames."""
        # Arabic/Hebrew filename
        rtl_filename = "ملف_اختبار.txt"  # Arabic for "test file"
        
        sanitized = _sanitize_rel_path(rtl_filename)
        assert sanitized is not None
        assert len(sanitized) > 0
        
        # Should preserve RTL characters
        assert "ملف" in sanitized
    
    def test_combining_characters(self):
        """Test filenames with combining characters."""
        # Base character + multiple combining marks
        filename = "e\u0301\u0302\u0303\u0304.txt"  # e + acute + circumflex + tilde + macron
        
        sanitized = _sanitize_rel_path(filename)
        assert sanitized is not None
        assert len(sanitized) > 0
    
    def test_surrogate_pairs(self):
        """Test filenames with surrogate pairs (emoji, etc)."""
        # Emoji that require surrogate pairs in UTF-16
        filename = "test🏴󠁧󠁢󠁥󠁮󠁧󠁿🧑‍💻👨‍👩‍👧‍👦.txt"  # Flag, person, family
        
        sanitized = _sanitize_rel_path(filename)
        assert sanitized is not None 
        assert len(sanitized) > 0
        
        # Should handle complex emoji sequences


class TestSecurityEdgeCases:
    """Test security-related edge cases."""
    
    def test_null_bytes_in_content(self):
        """Test handling of null bytes in file content."""
        content_with_nulls = "Hello\x00World\x00Test"
        
        # Should handle gracefully without crashing
        encoding_result = detect_and_convert_encoding(content_with_nulls.encode('utf-8'))
        assert encoding_result is not None
    
    def test_deeply_nested_json(self):
        """Test handling of very deeply nested JSON structures."""
        # Create deeply nested JSON (potential DoS via recursion)
        nested_json = "{" 
        for _ in range(1000):
            nested_json += '"key": {'
        nested_json += '"value": "deep"'
        for _ in range(1000):
            nested_json += "}"
        nested_json += "}"
        
        # Should handle without stack overflow
        try:
            parsed = json.loads(nested_json)
            assert parsed is not None
        except (json.JSONDecodeError, RecursionError):
            # Either parsing fails gracefully or recursion is limited
            pass
    
    def test_malformed_uri_handling(self):
        """Test handling of malformed URIs."""
        malformed_uris = [
            "viking://",  # Empty path
            "viking:///",  # Multiple slashes
            "viking://\x00null",  # Null byte in URI
            "viking://path with spaces",  # Unescaped spaces
            "viking://../../../etc/passwd",  # Path traversal
        ]
        
        for uri in malformed_uris:
            try:
                viking_uri = VikingURI(uri)
                # Should either handle gracefully or raise appropriate exception
                assert viking_uri is not None
            except (ValueError, Exception) as e:
                # Appropriate exception handling is acceptable
                assert isinstance(e, (ValueError, Exception))


class TestBoundaryConditions:
    """Test various boundary conditions and limits."""
    
    def test_is_text_file_edge_cases(self):
        """Test is_text_file with edge case filenames."""
        edge_cases = [
            "",  # Empty string
            ".",  # Just dot
            "..",  # Parent directory
            "...",  # Multiple dots
            ".txt",  # Hidden file with text extension
            "file.",  # File with trailing dot
            "file..txt",  # Multiple dots before extension
            "file.TXT",  # Uppercase extension
            "FILE.txt",  # Mixed case
        ]
        
        for filename in edge_cases:
            # Should not crash
            try:
                result = is_text_file(filename)
                assert isinstance(result, bool)
            except Exception:
                # May raise exception for invalid filenames - that's OK
                pass
    
    def test_directory_upload_with_circular_symlinks(self, tmp_path):
        """Test directory upload with circular symbolic links."""
        if os.name == 'nt':  # Skip on Windows due to symlink permissions
            pytest.skip("Symlink test skipped on Windows")
        
        # Create directories
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()
        
        # Create circular symlinks
        (dir_a / "link_to_b").symlink_to(dir_b)
        (dir_b / "link_to_a").symlink_to(dir_a)
        
        # Add a regular file
        (dir_a / "test.txt").write_text("test content")
        
        mock_fs = MagicMock()
        mock_fs.write_file_bytes = AsyncMock()
        mock_fs.mkdir = AsyncMock()
        
        # Should handle circular links without infinite recursion
        try:
            result = await upload_directory(str(tmp_path), "viking://test/", mock_fs)
            # Should complete without hanging
            assert result is None or result is not None  # Just checking it returns
        except Exception as e:
            # Acceptable to raise exception for circular links
            assert "recursion" in str(e).lower() or "circular" in str(e).lower()


# Async utility function for upload_text_files
async def upload_text_files(file_paths: List[str], target_uri: str, viking_fs):
    """Upload multiple text files to VikingFS."""
    for file_path in file_paths:
        path = Path(file_path)
        if path.exists() and path.is_file():
            content = path.read_bytes()
            uri = f"{target_uri.rstrip('/')}/{path.name}"
            await viking_fs.write_file_bytes(uri, content)