from openviking.parse.accessors.web_crawler.render_heuristics import (
    looks_like_unrendered_page,
    should_render_with_playwright,
)


def test_js_required_pattern_triggers_render():
    html = "<html>You need to enable JavaScript to run this app.</html>"
    assert should_render_with_playwright(html) is True


def test_react_root_triggers_render():
    html = "<html><body><div id='root'></div></body></html>"
    assert should_render_with_playwright(html) is True


def test_ssr_app_root_with_rich_content_skips_render():
    body = "OpenViking 快速开始文档，包含安装、配置、添加资源、创建集合和检索示例。" * 8
    html = f"<html><body><div id='app'><main><p>{body}</p></main></div></body></html>"
    assert should_render_with_playwright(html) is False


def test_next_data_triggers_render():
    html = "<html><script>__NEXT_DATA__</script></html>"
    assert should_render_with_playwright(html) is True


def test_many_scripts_with_tiny_body_triggers_render():
    html = "<html><body>x</body>" + "<script></script>" * 6 + "</html>"
    assert should_render_with_playwright(html) is True


def test_content_rich_static_page_skips_render():
    body = "This is a fully server-rendered article with plenty of readable text. " * 3
    html = f"<html><body><p>{body}</p></body></html>"
    assert should_render_with_playwright(html) is False


def test_challenge_page_is_unrendered():
    html = "<html><body>Please wait...</body></html>"
    assert looks_like_unrendered_page(html) is True


def test_empty_shell_is_unrendered():
    html = "<html><body><div id='root'></div></body></html>"
    assert looks_like_unrendered_page(html) is True


def test_real_content_is_not_unrendered():
    body = "Collection 数据过滤删除任务的完整说明文档，包含请求参数、示例与返回值。" * 3
    html = f"<html><body><article>{body}</article></body></html>"
    assert looks_like_unrendered_page(html) is False


def test_unrendered_check_fails_closed_on_parser_error(monkeypatch):
    from openviking.parse.accessors.web_crawler import render_heuristics

    def raise_parser_error(*_args, **_kwargs):
        raise RuntimeError("parser failed")

    monkeypatch.setattr(render_heuristics, "BeautifulSoup", raise_parser_error)

    assert render_heuristics.looks_like_unrendered_page("<html></html>") is True
