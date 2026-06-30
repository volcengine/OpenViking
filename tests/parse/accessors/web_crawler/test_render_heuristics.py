from openviking.parse.accessors.web_crawler.render_heuristics import (
    should_render_with_playwright,
)


def test_js_required_pattern_triggers_render():
    html = "<html>You need to enable JavaScript to run this app.</html>"
    assert should_render_with_playwright(html) is True


def test_react_root_triggers_render():
    html = "<html><body><div id='root'></div></body></html>"
    assert should_render_with_playwright(html) is True


def test_next_data_triggers_render():
    html = "<html><script>__NEXT_DATA__</script></html>"
    assert should_render_with_playwright(html) is True


def test_many_scripts_with_tiny_body_triggers_render():
    html = "<html><body>x</body>" + "<script></script>" * 6 + "</html>"
    assert should_render_with_playwright(html) is True


def test_static_page_without_spa_signal_skips_render():
    html = "<html><body>Static page with short body.</body></html>"
    assert should_render_with_playwright(html) is False
