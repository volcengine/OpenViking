"""HTML table cell text remains one Markdown cell after actual local-file parsing."""

from pathlib import Path

import pytest

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.html import HTMLParser

BODY = (
    "This synthetic paragraph gives ordinary article extraction enough context for the following table. "
    * 5
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cell,expected",
    [
        ("one | two", r"one \| two"),
        (r"one \| two", r"one \\\| two"),
        ("<code>A|B</code>", r"`A\|B`"),
        (
            '<a href="https://example.test/guide">left|right</a>',
            r"[left\|right](https://example.test/guide)",
        ),
    ],
)
async def test_html_file_table_cell_pipe_survives_markdown_artifact(tmp_path, cell, expected):
    source = tmp_path / "table.html"
    source.write_text(
        f"<html><body><h1>Synthetic table</h1><p>{BODY}</p><table><tr><th>name</th><th>value</th></tr><tr><td>{cell}</td><td>kept</td></tr></table></body></html>"
    )
    result = await HTMLParser().parse(
        source,
        parse_output_store=LocalParseOutputStore(str(tmp_path / "artifacts")),
        split_content=False,
    )
    assert result.warnings == []
    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root)
    files = list(root.rglob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "| name | value |" in content
    assert f"| {expected} | kept |" in content
    assert BODY.strip() in content


def test_nested_cells_are_escaped_once_and_ordinary_body_is_unchanged():
    from bs4 import BeautifulSoup

    html = "<html><body><p>body | text</p><table><tr><td>outer | text<table><tr><td>inner | text</td></tr></table></td></tr></table></body></html>"
    soup = BeautifulSoup(HTMLParser()._preprocess_html(html), "html.parser")
    assert soup.p.get_text() == "body | text"
    assert soup.find_all("td")[-1].get_text() == r"inner \| text"
    assert r"outer \| text" in soup.find("td").get_text()


def test_non_table_content_keeps_original_preprocessing_contract():
    html = "<html><body><p>ordinary | body \\ path</p></body></html>"
    assert HTMLParser()._preprocess_html(html) == html


def test_wechat_table_uses_same_escaping_and_retains_lazy_images():
    from bs4 import BeautifulSoup

    html = '<html><body><div id="js_content" style="display:none"><table><tr><th>left|right</th></tr></table><img data-src="https://example.test/image.png"></div></body></html>'
    soup = BeautifulSoup(HTMLParser()._preprocess_html(html), "html.parser")
    assert soup.th.get_text() == r"left\|right"
    assert soup.find(id="js_content").get("style") is None
    assert soup.img["src"] == "https://example.test/image.png"


def test_table_comments_keep_comment_identity_and_do_not_become_cell_text():
    from bs4 import BeautifulSoup, Comment

    html = "<table><tr><td>visible|value<!-- hidden|comment --></td></tr></table>"
    soup = BeautifulSoup(HTMLParser()._preprocess_html(html), "html.parser")
    assert soup.td.get_text() == r"visible\|value"
    comments = soup.find_all(string=lambda value: isinstance(value, Comment))
    assert [str(value) for value in comments] == [" hidden|comment "]
