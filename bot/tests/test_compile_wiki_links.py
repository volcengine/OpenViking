"""Wiki links use the containing page directory, including at publication."""

import asyncio
from collections import Counter
from types import SimpleNamespace

import pytest
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.ops import finalize
from vikingbot.compile.renderer import (
    normalize_wiki_page_links,
    validate_wiki_link_destinations,
)


def test_wiki_links_preserve_fenced_examples():
    example = "[Example](concept/b.md) [Whitespace](concept/a b.md)\n"
    body = (
        f" ```md\n{example} ```\n"
        f"````md\n```\n{example}````\n"
        f"   ~~~md\n```\n{example}   ~~~~\n"
        f"> ```md\n> {example}> ```\n"
        f"- ```md\n  {example}  ```\n"
        f"``multiline\n{example}``\n"
        f'<span title="`"></span> `{example.rstrip()}`\n'
        '<span title="`"></span> [Actual](concept/b.md) `inline code`\n'
        f"![`{example.rstrip()}`][preview]\n\n[preview]: preview.png\n\n"
        f"> ```md\n> {example}\n"
        "\n[Actual](concept/b.md)\n\n"
        f"```md\n{example}"
    )
    validate_wiki_link_destinations(body)
    result, count = normalize_wiki_page_links(
        body, path="concept/a.md", known_paths={"concept/b.md"}
    )
    assert result == body.replace("[Actual](concept/b.md)", "[Actual](b.md)")
    assert count == 2


def test_normalize_only_unambiguous_known_output_links():
    body = (
        '[Sibling](concept/b.md#part "Title") [Other](entity/c.md?view=1#anchor)\n'
        "[Correct](b.md) [Parent](../entity/c.md) [Unknown](concept/missing.md)\n"
        "[Source](viking://resources/source.md) [Web](https://example.org/concept/b.md)\n"
        "[Anchor](#part) ![Image](concept/b.md)\n"
        "`[Code](concept/b.md)`\n<!-- [Comment](concept/b.md) -->\n"
        "```md\n[Fenced](concept/b.md)\n```\n    [Indented](concept/b.md)\n\n"
        "\\[Literal](concept/a b.md)\n"
        "\\\\[After backslash](concept/b.md)\n\n"
        "1. Item\n\n    [Nested](concept/b.md)\n"
    )
    validate_wiki_link_destinations(body)
    result, count = normalize_wiki_page_links(
        body, path="concept/a.md", known_paths={"concept/b.md", "entity/c.md"}
    )
    assert count == 4
    assert result == (
        body.replace('concept/b.md#part "Title"', 'b.md#part "Title"')
        .replace("entity/c.md?view=1#anchor", "../entity/c.md?view=1#anchor")
        .replace("[After backslash](concept/b.md)", "[After backslash](b.md)")
        .replace("[Nested](concept/b.md)", "[Nested](b.md)")
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
    destination = "concept/b.md" if existing_relative else "b.md"
    assert published == frontmatter + f"[Sibling]({destination})"
    assert values["a"]["content"] == original
    assert values["a"]["sha256"] == content_hash(original)
    assert values["finalize"]["outputs"]["concept/a.md"]["sha256"] == content_hash(published)
    assert values["finalize"]["normalized_links"] == (0 if existing_relative else 1)
    assert queried[0].endswith("/concept/concept/b.md")


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_finalize_bounds_and_deduplicates_remote_link_checks(fail):
    from openviking_cli.exceptions import NotFoundError

    body = "\n".join(f"[Link](concept/{i}.md) [Again](concept/{i}.md)" for i in range(12))
    values = {
        "a": {
            "path": "concept/a.md",
            "owner": "a",
            "content": body,
            "sha256": content_hash(body),
            "base_hash": None,
            "source_refs": [],
        }
    }
    active = peak = 0
    queried = Counter()

    async def stat(uri):
        nonlocal active, peak
        queried[uri] += 1
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            if fail and uri.endswith("/0.md"):
                raise PermissionError("denied")
            if "/concept/concept/" in uri:
                raise NotFoundError(uri)
            return {"isDir": False}
        finally:
            active -= 1

    async def get(ref):
        return values[ref]

    async def put(ref, value):
        values[ref] = value

    runtime = SimpleNamespace(
        files=SimpleNamespace(get=get, put=put),
        skill_target=False,
        output_format="wiki",
        target="viking://resources/wiki",
        old={},
        client=SimpleNamespace(stat=stat),
        metrics=Counter(),
    )
    if fail:
        with pytest.raises(PermissionError, match="denied"):
            await finalize.run(runtime, ["a"])
        assert "finalize" not in values
    else:
        rendered = await finalize.run(runtime, ["a"])
        assert rendered.operations[0]["content"] == body.replace("](concept/", "](")
        assert len(queried) == 24
    assert set(queried.values()) == {1}
    assert 1 < peak <= 8
    assert active == 0
