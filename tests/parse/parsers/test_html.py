from openviking.parse.parsers.html import HTMLParser


class TestHTMLParserMarkdownCleaning:
    def setup_method(self):
        self.parser = HTMLParser()

    def test_strips_data_image_markdown(self):
        md = "before ![alt](data:image/png;base64,AAA) after"
        assert "data:image" not in self.parser._clean_markdown(md)

    def test_strips_data_image_html_tag(self):
        md = "<img src='data:image/png;base64,AAA' alt='x'/> tail"
        assert "data:image" not in self.parser._clean_markdown(md)

    def test_strips_empty_anchor_spans(self):
        md = "head <span id='foo'></span><a name='bar'></a> tail"
        cleaned = self.parser._clean_markdown(md)
        assert "<span" not in cleaned
        assert "<a name" not in cleaned

    def test_keeps_spa_notice_text(self):
        md = "You need to enable JavaScript to run this app."
        assert self.parser._clean_markdown(md) == md

    def test_collapses_blank_lines(self):
        cleaned = self.parser._clean_markdown("a\n\n\n\n\nb")
        assert cleaned == "a\n\nb"

    def test_trims_leading_trailing_whitespace(self):
        assert self.parser._clean_markdown("   \n\nhello\n  ") == "hello"


class TestHTMLParserNoscriptFallback:
    def setup_method(self):
        self.parser = HTMLParser()

    def test_spa_shell_keeps_noscript_notice(self):
        html = (
            "<html><head><title></title></head><body>"
            "<noscript>You need to enable JavaScript to run this app.</noscript>"
            '<div id="root"></div></body></html>'
        )
        md = self.parser._html_to_markdown(html, base_url="https://example.com/")
        assert "You need to enable JavaScript to run this app." in md

    def test_noscript_fallback_skipped_when_body_extractable(self):
        html = (
            "<html><head><title>Doc</title></head><body><article>"
            "<p>" + ("Real body content that trafilatura can extract. " * 20) + "</p>"
            "</article>"
            "<noscript>enable javascript</noscript></body></html>"
        )
        md = self.parser._html_to_markdown(html, base_url="https://example.com/")
        assert "Real body content" in md
        assert "enable javascript" not in md


class TestHTMLParserTitleExtraction:
    def setup_method(self):
        self.parser = HTMLParser()

    def test_title_from_og_meta(self):
        html = '<html><head><meta property="og:title" content="HelloOG"></head><body>x</body></html>'
        title = self.parser._extract_title(html, "http://example.com/")
        assert title and "HelloOG" in title

    def test_title_from_title_tag(self):
        html = "<html><head><title>HelloTitle</title></head><body>x</body></html>"
        title = self.parser._extract_title(html, "http://example.com/")
        assert title and "HelloTitle" in title

    def test_returns_empty_on_blank_html(self):
        assert self.parser._extract_title("", "http://example.com/") == ""

    def test_returns_empty_on_no_title(self):
        html = "<html><body>no title</body></html>"
        assert self.parser._extract_title(html, "http://example.com/") == ""
