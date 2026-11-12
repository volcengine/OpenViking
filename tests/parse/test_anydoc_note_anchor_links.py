"""Regression tests for bookmark links inside notes (issue #7177).

AnyDoc's native model keeps the body bookmark anchor and the note's anchor link, but the renderer's
anchor resolution only collected *linked* anchors from body inlines. A bookmark that was linked from
a footnote (and not from the body) was therefore never resolved and the note link was dropped to its
label.
"""

from types import SimpleNamespace

from openviking.parse.parsers.anydoc_renderer import _AnyDocMarkdownRenderer


def _text(text):
    return SimpleNamespace(kind="text", text=text, style=None)


def _anchor(name):
    return SimpleNamespace(kind="anchor", anchor=name)


def _anchor_link(label, target):
    return SimpleNamespace(
        kind="link",
        target=SimpleNamespace(kind="anchor", value=target),
        content=[_text(label)],
    )


def _paragraph(content):
    return SimpleNamespace(kind="paragraph", content=content)


def _note(note_id, blocks):
    return SimpleNamespace(id=note_id, blocks=blocks)


def _render(blocks, notes):
    document = SimpleNamespace(assets=[], blocks=blocks, notes=notes)
    renderer = _AnyDocMarkdownRenderer(
        document,
        source_format="docx",
        resource_name="notes",
        storage=SimpleNamespace(),
    )
    return renderer.render()


def test_note_only_link_to_body_bookmark_is_resolved():
    body = [_paragraph([_anchor("target"), _text("Target paragraph")])]
    notes = [_note("1", [_paragraph([_anchor_link("Jump from note", "target")])])]

    markdown = _render(body, notes)

    assert '<a id="target"></a>' in markdown
    assert "[Jump from note](#target)" in markdown


def test_body_and_note_links_to_the_same_bookmark_still_work():
    body = [_paragraph([_anchor("target"), _anchor_link("Body jump", "target")])]
    notes = [_note("1", [_paragraph([_anchor_link("Note jump", "target")])])]

    markdown = _render(body, notes)

    assert '<a id="target"></a>' in markdown
    assert "[Body jump](#target)" in markdown
    assert "[Note jump](#target)" in markdown


def test_note_link_to_unknown_bookmark_still_falls_back_to_its_label():
    body = [_paragraph([_text("Reference")])]
    notes = [_note("1", [_paragraph([_anchor_link("Jump from note", "missing")])])]

    markdown = _render(body, notes)

    assert "Jump from note" in markdown
    assert "#missing" not in markdown
