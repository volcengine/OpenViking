"""Tests for HTTPAccessor._extract_filename_from_disposition (RFC 6266 / RFC 5987)."""

import pytest

from openviking.parse.accessors.http_accessor import HTTPAccessor


class TestExtractFilenameFromDisposition:
    """Verify Content-Disposition filename extraction compliance with RFC 6266 / RFC 5987."""

    # --- RFC 5987 extended notation (filename*) ---

    def test_utf8_extended(self):
        """Standard UTF-8 extended notation."""
        header = "attachment; filename*=UTF-8''%E2%82%AC%20rates.pdf"
        assert HTTPAccessor._extract_filename_from_disposition(header) == "€ rates.pdf"

    def test_non_utf8_charset(self):
        """Non-UTF-8 charset (iso-8859-1) should be decoded correctly, not dropped."""
        header = "attachment; filename*=iso-8859-1''r%E9sum%E9.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        assert result is not None
        assert result.endswith(".pdf")

    def test_shift_jis_charset(self):
        """Shift_JIS charset should be decoded correctly."""
        header = "attachment; filename*=Shift_JIS''%82%B1%82%F1%82%C9%82%BF%82%CD.txt"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        assert result is not None
        assert result.endswith(".txt")

    def test_extended_with_language_tag(self):
        """RFC 5987 allows a language tag between the charset and the value."""
        header = "attachment; filename*=UTF-8'en'%E2%82%AC%20rates.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        assert result is not None
        assert result.endswith(".pdf")

    def test_extended_takes_precedence_over_legacy(self):
        """When both filename* and filename are present, filename* wins (RFC 6266 §4.3)."""
        header = "attachment; filename=\"legacy.pdf\"; filename*=UTF-8''%E2%82%AC%20rates.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        assert result is not None
        assert "rates" in result

    def test_unknown_charset_falls_back_to_utf8(self):
        """Unknown charset should not crash; fall back to UTF-8 decoding."""
        header = "attachment; filename*=unknown-charset''%E2%82%AC%20rates.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        # Should not be None — fallback decoding should still produce a result
        assert result is not None

    # --- Legacy filename parameter ---

    def test_quoted_filename(self):
        """Quoted filename parameter."""
        header = 'attachment; filename="document.pdf"'
        assert HTTPAccessor._extract_filename_from_disposition(header) == "document.pdf"

    def test_quoted_filename_with_inline(self):
        """inline disposition with quoted filename."""
        header = 'inline; filename="2601.00014v1.pdf"'
        assert HTTPAccessor._extract_filename_from_disposition(header) == "2601.00014v1.pdf"

    def test_bare_token_filename(self):
        """Bare token filename (no quotes)."""
        header = "attachment; filename=document.pdf"
        assert HTTPAccessor._extract_filename_from_disposition(header) == "document.pdf"

    def test_filename_with_extra_params(self):
        """Filename followed by other parameters."""
        header = 'attachment; filename="foo.pdf"; size=12345'
        assert HTTPAccessor._extract_filename_from_disposition(header) == "foo.pdf"

    # --- Edge cases ---

    def test_empty_header(self):
        """Empty or None header should return None."""
        assert HTTPAccessor._extract_filename_from_disposition("") is None
        assert HTTPAccessor._extract_filename_from_disposition(None) is None

    def test_no_filename_parameter(self):
        """Header without any filename parameter."""
        header = "attachment; size=12345"
        assert HTTPAccessor._extract_filename_from_disposition(header) is None

    def test_filename_star_not_matched_by_legacy_regex(self):
        """The bare-token regex must not match filename*= (the extended parameter).

        This is the second defect from issue #2857: the old ``filename=([^;]+)``
        regex could match the ``*`` in ``filename*=``, producing a corrupted name.
        """
        # Only filename* present, no legacy filename — legacy regex must not fire
        header = "attachment; filename*=UTF-8''%E2%82%AC.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        # Should get the decoded extended value, not a corrupted match
        assert result is not None
        assert result.endswith(".pdf")
        # Must not contain stray characters from matching filename*= incorrectly
        assert "*" not in result

    def test_case_insensitive_parameter_name(self):
        """Parameter names are case-insensitive per RFC 2231/5987."""
        header = "attachment; FILENAME*=UTF-8''%E2%82%AC%20rates.pdf"
        result = HTTPAccessor._extract_filename_from_disposition(header)
        assert result is not None
        assert result.endswith(".pdf")
