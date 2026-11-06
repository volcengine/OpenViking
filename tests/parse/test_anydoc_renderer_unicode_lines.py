# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression: the AnyDoc Markdown renderer must not treat Unicode line
separators (U+2028, U+2029, NEL U+0085, VT, FF) as Markdown line breaks.

``str.splitlines()`` splits on those code points in addition to CR/LF, so a
literal payload character inside a code block, table cell or note was turned
into an ASCII newline / ``<br>`` when the block was prefixed inside a quote,
list, table cell or footnote definition. Only CRLF, CR and LF are Markdown
line endings; every other character must survive verbatim.
"""

from types import SimpleNamespace as NS

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer

U2028 = "\u2028"
U2029 = "\u2029"
NEL = "\u0085"


def _render(blocks, notes=(), source_format="epub"):
    document = NS(blocks=list(blocks), notes=list(notes), assets=[])
    renderer = _AnyDocMarkdownRenderer(
        document, source_format=source_format, resource_name="unicode", storage=None
    )
    return renderer.render()


def _code(text):
    return NS(kind="code_block", text=text, lang="")


def _para(text):
    return NS(kind="paragraph", content=[NS(kind="text", text=text, style=None)])


def test_code_block_in_block_quote_preserves_unicode_separator():
    doc = [NS(kind="block_quote", blocks=[_code(f'value = "left{U2028}right"')])]
    assert U2028 in _render(doc)


def test_code_block_in_list_item_preserves_unicode_separator():
    item = NS(blocks=[_code(f'value = "left{U2029}right"')], marker_label=None)
    list_model = NS(items=[item], start=1, marker="bullet")
    doc = [NS(kind="list", list=list_model)]
    assert U2029 in _render(doc)


def test_table_cell_preserves_unicode_separator():
    header = NS(cell=NS(blocks=[_para("h")]), kind="origin")
    data = NS(cell=NS(blocks=[_code(f"left{U2028}right")]), kind="origin")
    table = NS(grid=[[header], [data]], kind="data", header_rows=1)
    doc = [NS(kind="table", table=table)]
    out = _render(doc)
    assert U2028 in out
    assert "<br>" not in out


def test_note_definition_preserves_unicode_separator():
    note = NS(id="n1", blocks=[_para(f"line one{NEL}line two")])
    ref = NS(
        kind="paragraph",
        content=[NS(kind="text", text="see ", style=None), NS(kind="note_ref", note_id="n1")],
    )
    assert NEL in _render([ref], notes=[note])


def test_real_newlines_still_split_in_block_quote():
    doc = [NS(kind="block_quote", blocks=[_para("alpha\nbeta")])]
    assert _render(doc) == "> alpha\n> beta\n"
