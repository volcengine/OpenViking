import pytest
from openviking.parse.parsers.html import HTMLParser


class TestHTMLParserRawUrlConversion:
    """Test suite for HTMLParser._convert_to_raw_url method."""

    def setup_method(self):
        self.parser = HTMLParser()

    def test_github_blob_conversion(self):
        blob_url = "https://github.com/volcengine/OpenViking/blob/main/docs/design.md"
        expected = "https://raw.githubusercontent.com/volcengine/OpenViking/main/docs/design.md"
        assert self.parser._convert_to_raw_url(blob_url) == expected

        blob_deep = "https://github.com/user/repo/blob/feature/branch/src/components/Button.tsx"
        expected_deep = (
            "https://raw.githubusercontent.com/user/repo/feature/branch/src/components/Button.tsx"
        )
        assert self.parser._convert_to_raw_url(blob_deep) == expected_deep

    def test_github_non_blob_urls(self):
        repo_root = "https://github.com/volcengine/OpenViking"
        assert self.parser._convert_to_raw_url(repo_root) == repo_root

        issue_url = "https://github.com/volcengine/OpenViking/issues/1"
        assert self.parser._convert_to_raw_url(issue_url) == issue_url

        raw_url = "https://raw.githubusercontent.com/volcengine/OpenViking/main/README.md"
        assert self.parser._convert_to_raw_url(raw_url) == raw_url

    def test_gitlab_blob_conversion(self):
        blob_url = "https://gitlab.com/gitlab-org/gitlab/-/blob/master/README.md"
        expected = "https://gitlab.com/gitlab-org/gitlab/-/raw/master/README.md"
        assert self.parser._convert_to_raw_url(blob_url) == expected

        blob_deep = "https://gitlab.com/group/project/-/blob/dev/src/main.rs"
        expected_deep = "https://gitlab.com/group/project/-/raw/dev/src/main.rs"
        assert self.parser._convert_to_raw_url(blob_deep) == expected_deep

    def test_gitlab_non_blob_urls(self):
        root = "https://gitlab.com/gitlab-org/gitlab"
        assert self.parser._convert_to_raw_url(root) == root

        issue = "https://gitlab.com/gitlab-org/gitlab/-/issues/123"
        assert self.parser._convert_to_raw_url(issue) == issue

    def test_other_domains(self):
        url = "https://example.com/blob/main/file.txt"
        assert self.parser._convert_to_raw_url(url) == url

        bitbucket = "https://bitbucket.org/user/repo/src/master/README.md"
        assert self.parser._convert_to_raw_url(bitbucket) == bitbucket
