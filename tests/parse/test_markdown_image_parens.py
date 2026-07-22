"""Tests for MarkdownParser image extraction — regression for #3455.

Verifies that image paths containing balanced parentheses are correctly
captured instead of being truncated at the first ``)`` character.

Tests exercise the production ``MarkdownParser._image_pattern`` regex
directly, so they fail if the regex regresses to the old ``[^)]+`` form.
"""

from openviking.parse.parsers.markdown import MarkdownParser

# Instantiate once to access the production regex
_parser = MarkdownParser()
IMAGE_PATTERN = _parser._image_pattern


class TestImagePatternParentheses:
    """Image path extraction with parentheses in filenames/directories."""

    def test_simple_path(self):
        """Standard image path without parentheses."""
        text = "![alt text](images/photo.png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "images/photo.png"

    def test_path_with_balanced_parens(self):
        """Path containing one level of balanced parentheses.

        This is the core regression: ``文档_17 (17号项目)/image1.png``
        should be captured in full, not truncated at the first ``)``.
        """
        text = "![image1](文档_17 (17号项目)/image1.png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "文档_17 (17号项目)/image1.png"

    def test_path_with_parens_in_directory(self):
        """Parentheses in directory component only."""
        text = "![img](docs (v2)/screenshot.png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "docs (v2)/screenshot.png"

    def test_path_with_parens_in_filename(self):
        """Parentheses in filename component only."""
        text = "![img](report (final).pdf)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "report (final).pdf"

    def test_empty_alt_text(self):
        """Empty alt text should still capture the path."""
        text = "![](images/photo.png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "images/photo.png"

    def test_multiple_images(self):
        """Multiple images on the same line, some with parens."""
        text = "![a](img1.png) and ![b](docs (v2)/img2.png) and ![c](img3.png)"
        matches = [m.group(2) for m in IMAGE_PATTERN.finditer(text)]
        assert matches == ["img1.png", "docs (v2)/img2.png", "img3.png"]

    def test_url_with_parens(self):
        """URL-style paths with parentheses (e.g. Wikipedia)."""
        text = "![test](https://example.com/File_(name).png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        assert m.group(2) == "https://example.com/File_(name).png"

    def test_no_false_match_on_link(self):
        """The image pattern should not match plain links (no leading ``!``)."""
        text = "[link](path (with parens)/page.md)"
        # IMAGE_PATTERN requires leading !
        matches = IMAGE_PATTERN.findall(text)
        assert len(matches) == 0

    def test_truncated_path_regression(self):
        """The old regex ``[^)]+`` would truncate at the first ``)``.

        With the new regex, the full path is captured.
        """
        text = "![image1](文档_17 (17号项目)/image1.png)"
        m = IMAGE_PATTERN.search(text)
        assert m is not None
        # Must NOT be truncated
        assert m.group(2) != "文档_17 (17号项目"
        assert m.group(2).endswith("image1.png")
