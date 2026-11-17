"""Hidden conflict metadata is not canonical knowledge for derived summaries."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.parsers.code.ast import SkeletonExtractionResult
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from tests.storage.test_semantic_processor_code_routing import _config, _TestVLMResolver


def test_projection_preserves_code_and_unrelated_comments():
    from openviking.utils.markdown_visibility import without_conflict_comments

    example = "```md\n<!-- CONFLICTS_CONTENT\nexample\n-->\n```\n"
    text = (
        "# Rule\n\nCommon facts.\n"
        + example
        + "<!-- ordinary -->\n\n<!-- CONFLICTS_CONTENT\nsecret evidence\n-->\n"
    )
    visible = without_conflict_comments(text)
    assert "secret evidence" not in visible
    assert example in visible and "<!-- ordinary -->" in visible
    assert without_conflict_comments(visible) == visible


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("prefix", ["", "> ", "- "])
def test_real_inline_comment_is_hidden_and_identical_code_literal_remains(newline, prefix):
    from openviking.utils.markdown_visibility import without_conflict_comments

    comment = "<!-- CONFLICTS_CONTENT evidence -->"
    text = f"{prefix}`{comment}` is syntax. {comment}\n"
    visible = without_conflict_comments(text.replace("\n", newline))
    assert visible.count(comment) == 1 and f"`{comment}`" in visible


@pytest.mark.parametrize("file_content", [False, True])
async def test_summary_receives_visible_content_before_truncation(file_content):
    config = _config()
    config.semantic.max_file_content_chars = 100
    raw = "# Rule\n\n<!-- CONFLICTS_CONTENT\n" + "secret " * 1000 + "\n-->\n\nCommon fact: 0.05.\n"
    fs = type("FS", (), {})()
    fs.read_file = AsyncMock(return_value=raw.encode())
    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=config,
        ),
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fs),
    ):
        await SemanticProcessor(vlm_resolver=_TestVLMResolver(config.vlm))._generate_text_summary(
            "viking://resources/rule.md",
            "rule.md",
            asyncio.Semaphore(1),
            file_content=raw.encode() if file_content else None,
        )
    config.vlm.get_completion_async.assert_awaited_once()
    prompt = config.vlm.get_completion_async.call_args.args[0]
    assert "secret" not in prompt and "CONFLICTS_CONTENT" not in prompt
    assert "Common fact: 0.05." in prompt
    assert raw.encode() == (raw.encode() if file_content else fs.read_file.return_value)


async def test_non_markdown_code_does_not_strip_comment_like_source_literals():
    config = _config()
    raw = 'const value = "<!-- CONFLICTS_CONTENT literal -->";'
    fs = type("FS", (), {})()
    fs.read_file = AsyncMock(return_value=raw)
    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=config,
        ),
        patch("openviking.storage.queuefs.semantic_processor.get_viking_fs", return_value=fs),
        patch(
            "openviking.parse.parsers.code.ast.extract_skeleton_result",
            return_value=SkeletonExtractionResult(
                text=None, provider="llm", should_fallback_to_llm=True, reason="unsupported"
            ),
        ),
    ):
        await SemanticProcessor(vlm_resolver=_TestVLMResolver(config.vlm))._generate_text_summary(
            "viking://resources/example.js", "example.js", asyncio.Semaphore(1)
        )
    assert "CONFLICTS_CONTENT literal" in config.vlm.get_completion_async.call_args.args[0]


@pytest.mark.parametrize(
    "text",
    [
        "> Claim <!-- CONFLICTS_CONTENT\n> secret --> kept.\n",
        "- Claim <!-- CONFLICTS_CONTENT\n  secret --> kept.\n",
        "# Heading <!-- CONFLICTS_CONTENT secret -->\n\nkept.\n",
        "<!-- CONFLICTS_CONTENT\nsecret until eof",
    ],
)
def test_hidden_metadata_in_container_blocks_cannot_leak(text):
    from openviking.utils.markdown_visibility import without_conflict_comments

    visible = without_conflict_comments(text)
    assert "secret" not in visible
    if "kept." in text:
        assert "kept." in visible
