"""Wiki links use the containing page directory, including at publication."""

from collections import Counter
from types import SimpleNamespace

import pytest
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.ops import finalize
from vikingbot.compile.renderer import (
    _split_frontmatter,
    normalize_wiki_page_links,
    validate_wiki_link_destinations,
)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        (" ```md\n", " ```\n"),
        ("````md\n```\n", "````\n"),
        ("   ~~~md\n```\n", "   ~~~~\n"),
        ("```md\n", ""),
    ],
)
def test_wiki_links_preserve_fenced_examples(opening, closing):
    example = "[Example](concept/b.md) [Whitespace](concept/a b.md)\n"
    body = opening + example + closing
    if closing:
        body += "\n[Actual](concept/b.md)"
    validate_wiki_link_destinations(body)
    result, count = normalize_wiki_page_links(
        body, path="concept/a.md", known_paths={"concept/b.md"}
    )
    assert result == body.replace("[Actual](concept/b.md)", "[Actual](b.md)")
    assert count == int(bool(closing))


def test_normalize_only_unambiguous_known_output_links():
    body = (
        '[Sibling](concept/b.md#part "Title") [Other](entity/c.md?view=1#anchor)\n'
        "[Correct](b.md) [Parent](../entity/c.md) [Unknown](concept/missing.md)\n"
        "[Source](viking://resources/source.md) [Web](https://example.org/concept/b.md)\n"
        "[Anchor](#part) ![Image](concept/b.md)\n"
        "`[Code](concept/b.md)`\n<!-- [Comment](concept/b.md) -->\n"
        "```md\n[Fenced](concept/b.md)\n```\n    [Indented](concept/b.md)\n"
    )
    result, count = normalize_wiki_page_links(
        body, path="concept/a.md", known_paths={"concept/b.md", "entity/c.md"}
    )
    assert count == 2
    assert result == body.replace('concept/b.md#part "Title"', 'b.md#part "Title"').replace(
        "entity/c.md?view=1#anchor", "../entity/c.md?view=1#anchor"
    )
    assert normalize_wiki_page_links(
        result, path="concept/a.md", known_paths={"concept/b.md", "entity/c.md"}
    ) == (result, 0)


def test_normalize_preserves_valid_relative_meaning_and_encoded_literal_names():
    body = '[Ambiguous](concept/b.md) [Space](<entity/a%20b%23x.md#part> "title")'
    result, count = normalize_wiki_page_links(
        body,
        path="concept/a.md",
        known_paths={"concept/concept/b.md", "concept/b.md", "entity/a b#x.md"},
    )
    assert result == '[Ambiguous](concept/b.md) [Space](<../entity/a%20b%23x.md#part> "title")'
    assert count == 1
    assert normalize_wiki_page_links(body, path="index.md", known_paths={"concept/b.md"}) == (
        body,
        0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_relative", [False, True])
async def test_finalize_checks_historical_paths_and_records_publication_hashes(existing_relative):
    frontmatter = "---\ntype: concept\nexample: '[Book](concept/O%27Reilly.md)'\n---\n"
    original = frontmatter + "[Sibling](concept/b.md)"
    values = {
        "a": {
            "path": "concept/a.md",
            "owner": "group-a",
            "content": original,
            "sha256": content_hash(original),
            "base_hash": None,
            "source_refs": ["source-a"],
        }
    }

    async def get(ref):
        return values[ref]

    async def put(ref, value):
        values[ref] = value

    queried = []

    async def stat(uri):
        from openviking_cli.exceptions import NotFoundError

        queried.append(uri)
        if uri.endswith("/concept/concept/b.md") and not existing_relative:
            raise NotFoundError(uri)
        return {"isDir": False}

    runtime = SimpleNamespace(
        files=SimpleNamespace(get=get, put=put),
        skill_target=False,
        output_format="wiki",
        target="viking://resources/wiki",
        old={"concept/O'Reilly.md": "Existing book", "concept/concept/O'Reilly.md": None},
        catalog={"concept/b.md": {}},
        client=SimpleNamespace(stat=stat),
        metrics=Counter(),
    )
    rendered = await finalize.run(runtime, ["a"])
    published = rendered.operations[0]["content"]
    assert published.startswith(frontmatter)
    assert _split_frontmatter(published)[0]["example"] == "[Book](concept/O%27Reilly.md)"
    if existing_relative:
        assert "[Sibling](concept/b.md)" in published
    else:
        assert "[Sibling](b.md)" in published
    assert values["a"]["content"] == original
    assert values["a"]["sha256"] == content_hash(original)
    assert values["finalize"]["outputs"]["concept/a.md"]["sha256"] == content_hash(published)
    assert values["finalize"]["normalized_links"] == (0 if existing_relative else 1)
    assert queried[0].endswith("/concept/concept/b.md")
