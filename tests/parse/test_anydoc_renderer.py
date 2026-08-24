from pathlib import Path
from types import SimpleNamespace

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer


class FakeStorage:
    def __init__(self, root: Path):
        self.media_dir = root
        self.saved = []

    def save_image(self, resource_name, image_data, filename=None, extension=".png"):
        path = self.media_dir / resource_name / "images" / f"{filename}{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_data)
        self.saved.append(path)
        return path


def _style(**overrides):
    values = {
        "bold": False,
        "italic": False,
        "strike": False,
        "code": False,
        "underline": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _block(kind, **overrides):
    values = {
        "kind": kind,
        "level": None,
        "anchor": None,
        "content": None,
        "list": None,
        "table": None,
        "blocks": None,
        "lang": None,
        "text": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _paragraph(*inlines):
    return _block("paragraph", content=list(inlines))


def _text(text, **style):
    style_obj = _style(**style) if style else None
    return SimpleNamespace(
        kind="text",
        text=text,
        style=style_obj,
        content=None,
        target=None,
        alt=None,
        source=None,
        anchor=None,
        note_id=None,
    )


def _inline(kind, **overrides):
    values = {
        "kind": kind,
        "text": None,
        "style": None,
        "content": None,
        "target": None,
        "alt": None,
        "source": None,
        "anchor": None,
        "note_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _doc(blocks, *, notes=None, assets=None):
    return SimpleNamespace(blocks=list(blocks), notes=notes or [], assets=assets or [])


def _list_item(*blocks, checked=None, marker_label=None):
    return SimpleNamespace(blocks=list(blocks), checked=checked, marker_label=marker_label)


def _cell(*blocks):
    return SimpleNamespace(blocks=list(blocks), col_span=1, row_span=1)


def _slot(cell=None, kind="origin"):
    return SimpleNamespace(kind=kind, cell=cell, origin_row=None, origin_col=None)


def _render_document(tmp_path, document, *, source_format="docx"):
    renderer = _AnyDocMarkdownRenderer(
        document,
        source_format=source_format,
        resource_name="Demo",
        storage=FakeStorage(tmp_path),
    )
    return renderer.render()


def test_renderer_escapes_markdown_special_text(tmp_path):
    document = _doc([_paragraph(_text("# Heading\n- item [x] a*b*"))])

    markdown = _render_document(tmp_path, document)

    assert markdown == "\\# Heading\n\\- item \\[x] a\\*b*\n"


def test_renderer_merges_adjacent_styled_text_and_preserves_outer_spaces(tmp_path):
    document = _doc(
        [
            _paragraph(
                _text("  "),
                _text("foo", bold=True),
                _text(" "),
                _text("bar", bold=True),
                _text("  "),
            )
        ]
    )

    markdown = _render_document(tmp_path, document)

    assert markdown == "**foo bar**\n"


def test_renderer_preserves_underlined_text(tmp_path):
    document = _doc(
        [
            _paragraph(
                _text("plain "),
                _text("underlined", underline=True),
                _text(" and "),
                _text("strong", bold=True, underline=True),
            )
        ]
    )

    markdown = _render_document(tmp_path, document)

    assert markdown == "plain <ins>underlined</ins> and <ins>**strong**</ins>\n"


def test_renderer_uses_dynamic_backtick_fences(tmp_path):
    document = _doc(
        [
            _paragraph(_text("`edge`", code=True)),
            _block("code_block", text="a ``` fence", lang="py"),
        ]
    )

    markdown = _render_document(tmp_path, document)

    assert "`` `edge` ``" in markdown
    assert "````py\na ``` fence\n````" in markdown


def test_renderer_formats_urls_and_anchor_links(tmp_path):
    heading = _block(
        "heading",
        level=1,
        anchor="target-id",
        content=[_text("Target Title")],
    )
    external = _inline(
        "link",
        content=[],
        target=SimpleNamespace(kind="external", value="https://ex ample.com/a(b)|<x>"),
    )
    anchor = _inline(
        "link",
        content=[_text("jump")],
        target=SimpleNamespace(kind="anchor", value="target-id"),
    )
    document = _doc([heading, _paragraph(external), _paragraph(anchor)])

    markdown = _render_document(tmp_path, document)

    assert (
        "[https://ex ample.com/a(b)|\\<x>](<https://ex ample.com/a(b)%7C%3Cx%3E>)"
        in markdown
    )
    assert "[jump](#target-title)" in markdown


def test_renderer_emits_html_anchor_for_non_heading_anchor(tmp_path):
    anchor = _inline("anchor", anchor="Custom Anchor")
    link = _inline(
        "link",
        content=[_text("go")],
        target=SimpleNamespace(kind="anchor", value="Custom Anchor"),
    )
    document = _doc([_paragraph(anchor, _text("Target")), _paragraph(link)])

    markdown = _render_document(tmp_path, document)

    assert '<a id="custom-anchor"></a>Target' in markdown
    assert "[go](#custom-anchor)" in markdown


def test_renderer_serializes_rich_lists(tmp_path):
    def item(text, *, checked=None, marker_label=None, extra=None):
        blocks = [_paragraph(_text(text))]
        if extra:
            blocks.append(_paragraph(_text(extra)))
        return _list_item(*blocks, checked=checked, marker_label=marker_label)

    alpha = _block(
        "list",
        list=SimpleNamespace(
            marker="lower_alpha",
            start=2,
            items=[item("Task", checked=False), item("Loose", extra="detail")],
        ),
    )
    labeled = _block(
        "list",
        list=SimpleNamespace(
            marker="bullet",
            start=1,
            items=[item("Custom", marker_label="A)")],
        ),
    )
    document = _doc([alpha, labeled])

    markdown = _render_document(tmp_path, document)

    assert "- b. [ ] Task" in markdown
    assert "- c. Loose\n\n     detail" in markdown
    assert "- A) Custom" in markdown


def test_renderer_serializes_table_slots_header_and_cell_blocks(tmp_path):
    def origin(*blocks):
        return _slot(_cell(*blocks))

    covered = _slot(kind="covered")
    inner_table = _block(
        "table",
        table=SimpleNamespace(
            grid=[[origin(_paragraph(_text("N1"))), origin(_paragraph(_text("N2")))]],
            header_rows=0,
            kind="data",
        ),
    )
    list_block = _block(
        "list",
        list=SimpleNamespace(
            marker="bullet",
            start=1,
            items=[_list_item(_paragraph(_text("L")))],
        ),
    )
    table = _block(
        "table",
        table=SimpleNamespace(
            grid=[
                [origin(_paragraph(_text("A|B"))), covered],
                [
                    origin(
                        _block("heading", level=2, content=[_text("Head")]),
                        list_block,
                    ),
                    origin(_block("code_block", text="`code`", lang=""), inner_table),
                ],
            ],
            header_rows=0,
            kind="data",
        ),
    )
    document = _doc([table])

    markdown = _render_document(tmp_path, document)

    assert "|  |  |" in markdown
    assert "| A\\|B |  |" in markdown
    assert "| **Head**<br>- L | `` `code` ``<br>N1 / N2 |" in markdown


def test_renderer_flattens_single_cell_layout_table(tmp_path):
    cell = _slot(_cell(_paragraph(_text("Layout text"))))
    document = _doc(
        [
            _block(
                "table",
                table=SimpleNamespace(kind="layout", grid=[[cell]], header_rows=0),
            )
        ]
    )

    markdown = _render_document(tmp_path, document)

    assert markdown == "Layout text\n"


def test_renderer_numbers_notes_by_reference_order(tmp_path):
    body = _paragraph(
        _text("Body"),
        _inline("note_ref", note_id="b"),
    )
    note_b = SimpleNamespace(
        id="b",
        kind="footnote",
        blocks=[
            _paragraph(
                _text("B note"),
                _inline("note_ref", note_id="a"),
            )
        ],
    )
    note_a = SimpleNamespace(id="a", kind="footnote", blocks=[_paragraph(_text("A note"))])
    note_c = SimpleNamespace(id="c", kind="footnote", blocks=[_paragraph(_text("C note"))])
    document = _doc([body], notes=[note_a, note_b, note_c])

    markdown = _render_document(tmp_path, document)

    assert "Body[^1]" in markdown
    assert "[^1]: B note[^2]" in markdown
    assert "[^2]: A note" in markdown
    assert "[^3]: C note" in markdown


def test_renderer_presentation_blockquote_becomes_speaker_notes(tmp_path):
    document = _doc(
        [
            _block(
                "block_quote",
                blocks=[_paragraph(_text("Presenter only"))],
            )
        ]
    )

    markdown = _render_document(tmp_path, document, source_format="pptx")

    assert markdown == "### Speaker Notes\n\nPresenter only\n"


def test_renderer_non_presentation_blockquote_stays_quote(tmp_path):
    document = _doc(
        [
            _block(
                "block_quote",
                blocks=[_paragraph(_text("Quoted"))],
            )
        ]
    )

    markdown = _render_document(tmp_path, document, source_format="docx")

    assert markdown == "> Quoted\n"
