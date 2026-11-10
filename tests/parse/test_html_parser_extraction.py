"""Regression tests for HTMLParser._html_to_markdown after switching to trafilatura.

Focus: verify that the WeChat-public-account preprocessing path still produces
non-empty Markdown after the underlying extractor changed from
readabilipy + markdownify to trafilatura.
"""

from openviking.parse.parsers.html import HTMLParser


WECHAT_HTML = """
<!doctype html>
<html>
<head><title>WeChat Article</title></head>
<body>
  <div id="page-content">
    <div id="js_content" style="visibility: hidden; opacity: 0;">
      <h1>OpenViking 周报</h1>
      <p>这是一篇微信公众号文章的正文段落，至少包含两百字以上的有效内容，
      用于让基于文本密度的抽取器能够稳定识别出主体区域。</p>
      <p>第二段同样是足够长的正文，避免被启发式规则误判为噪声。我们再加一些
      内容以保证抽取器有充分的密度信号去命中这块隐藏 div 区域。</p>
      <p>第三段是为了进一步增加正文密度。OpenViking 的 HTMLParser 在切换
      抽取器后仍然需要正确地从 #js_content 这个被 CSS 隐藏的容器里取出文本。</p>
      <img src="" data-src="https://example.com/cover.jpg" alt="cover" />
    </div>
  </div>
</body>
</html>
"""


def test_preprocess_strips_hidden_style_and_keeps_content():
    parser = HTMLParser()
    cleaned = parser._preprocess_html(WECHAT_HTML)
    assert "visibility: hidden" not in cleaned
    assert "OpenViking 周报" in cleaned
    assert 'src="https://example.com/cover.jpg"' in cleaned


def test_html_to_markdown_extracts_wechat_body():
    parser = HTMLParser()
    md = parser._html_to_markdown(WECHAT_HTML)
    assert md, "trafilatura should extract non-empty markdown from a WeChat-style article"
    assert "OpenViking 周报" in md
    assert "微信公众号" in md


def test_html_to_markdown_returns_empty_string_on_garbage_input():
    parser = HTMLParser()
    md = parser._html_to_markdown("<html><body></body></html>")
    assert md == ""
