"""Resource Compile must publish declared retrieval metadata along with page bodies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import WikiBundleDraft
from vikingbot.compile.ops.finalize import run
from vikingbot.compile.renderer import WikiRenderer

from openviking.utils.tags import merge_search_tags

ROOT = "viking://resources/wiki"
BODY = "---\ntype: guide\ntitle: Retry guide\ntags: [topic, Role=Canonical, type=guide]\n---\n# Retry guide\nRetry up to seven times.\n"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("binary", [False, True])
def test_submitted_resource_files_publish_explicit_tags_even_when_body_is_unchanged(
    existing, binary
):
    uri = ROOT + "/guide.md"
    file = {"update_uri": uri} if existing else {"path": "guide.md"}
    file.update({"workspace_path": "guide.md"} if binary else {"content": BODY})
    rendered = WikiRenderer().render(
        bundle=WikiBundleDraft(pages=[], files=[file], links=[]),
        target_uri=ROOT,
        source_roots={},
        catalog_uris={uri} if existing else set(),
        existing_raw={},
        existing_bytes={uri: BODY.encode()} if existing else {},
        file_payloads=[BODY.encode()] if binary else [None],
    )
    operation = rendered.operations[0]
    assert operation["tags"] == ["role=canonical", "type=guide"]
    assert operation["tag_mode"] == "append"
    assert "Retry up to seven times." in operation.get("content", BODY)
    assert merge_search_tags(["team=search", "role=draft"], operation["tags"]) == [
        "team=search",
        "role=canonical",
        "type=guide",
    ]


def test_unchanged_structured_resource_page_still_reconciles_metadata():
    page = {
        "page_id": 1,
        "title": "Retry guide",
        "page_type": "guide",
        "summary": "Retry limits.",
        "source_ids": ["source"],
        "body_markdown": "Retry up to seven times.",
        "tags": ["role=canonical", "type=guide"],
    }
    renderer = WikiRenderer()
    first = renderer.render(
        bundle=WikiBundleDraft(pages=[page], links=[]),
        target_uri=ROOT,
        source_roots={"source": "viking://resources/evidence.md"},
        catalog_uris=set(),
        existing_raw={},
    )
    original = first.operations[0]
    page["update_uri"] = original["uri"]
    second = renderer.render(
        bundle=WikiBundleDraft(pages=[page], links=[]),
        target_uri=ROOT,
        source_roots={"source": "viking://resources/evidence.md"},
        catalog_uris={original["uri"]},
        existing_raw={original["uri"]: original["content"]},
    )
    assert second.operations[0]["content"] == original["content"]
    assert second.operations[0]["tags"] == ["role=canonical", "type=guide"]


class Sandbox:
    def __init__(self):
        self.files = {}

    async def write_file(self, path, text):
        self.files[path] = text.encode()

    async def write_file_bytes(self, path, raw):
        self.files[path] = raw

    async def read_file_bytes(self, path):
        return self.files[path]

    async def list_files(self, root, max_entries=None):
        return [SimpleNamespace(path=p) for p in self.files if p.startswith(root + "/")]


@pytest.mark.parametrize("old", [None, BODY, BODY.replace("seven", "three")])
@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("tags: [topic, Role=Canonical, type=guide]", ["role=canonical", "type=guide"]),
        ("tags: [team=old, team=new, topic, '=bad', 'bad=']", ["team=new"]),
        ("tags: [topic]", None),
        ("tags: []", None),
        ("tags: role=canonical", None),
        ("", None),
    ],
)
async def test_pipeline_publication_preserves_declared_metadata_and_body(
    old, declaration, expected
):
    body = BODY.replace("tags: [topic, Role=Canonical, type=guide]", declaration)
    artifact = {
        "path": "guide.md",
        "owner": "writer",
        "content": body,
        "sha256": content_hash(body),
        "base_hash": content_hash(old) if old else None,
        "source_refs": [],
    }
    files = SimpleNamespace(
        sandbox=Sandbox(), get=AsyncMock(return_value=artifact), put=AsyncMock()
    )
    runtime = SimpleNamespace(
        skill_target=False,
        files=files,
        skill="Preserve declared tags.",
        old={"guide.md": old} if old else {},
        target=ROOT,
        changes=None,
        output_instructions="",
        request=SimpleNamespace(instruction="Publish guide."),
        warnings=[],
        evidence={},
        output_format="wiki",
    )
    rendered = await run(runtime, ["artifact"])
    operation = rendered.operations[0]
    assert operation["content"] == body
    assert operation["mode"] == ("replace" if old else "create")
    assert operation.get("tags") == expected
    assert operation.get("tag_mode") == ("append" if expected else None)
