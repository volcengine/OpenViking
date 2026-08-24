"""#4221 — a message whose `content` is a plain string aborted the whole sync.

`parse_messages()` assumed every `content` was a block list. Iterating a string
yields characters, so `"p".get("type")` raised

    AttributeError: 'str' object has no attribute 'get'

out of the parser, and because the raise happens before any session is
committed, one such message made the skill non-functional for the entire
workspace — not just for that message. The reporter measured 91 string bodies
in 1609 real messages (5.7%).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_dream_module():
    module_path = Path("examples/skills/ov_dream/scripts/dream.py").resolve()
    spec = importlib.util.spec_from_file_location("ov_dream_cli_content", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dream = _load_dream_module()


def _session_with(tmp_path: Path, bodies: list[object]) -> tuple[Path, object]:
    """One session file whose messages carry the given `content` values."""
    session_id = "sess-content"
    rows: list[dict] = [{"id": session_id, "timestamp": "2026-04-20T00:00:00Z", "cwd": "/tmp"}]
    for index, body in enumerate(bodies, start=1):
        rows.append(
            {
                "type": "message",
                "timestamp": f"2026-04-20T00:00:{index:02d}Z",
                "message": {"role": "user", "content": body},
            }
        )
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    session = dream.Session(
        session_id=session_id,
        cwd="/tmp",
        created_at="2026-04-20T00:00:00Z",
        session_key=session_id,
        session_file=path.name,
    )
    return path, session


def test_string_content_is_parsed_as_text(tmp_path: Path) -> None:
    _, session = _session_with(tmp_path, ["plain string instead of block list"])

    messages = list(dream.parse_messages(tmp_path, session, None))

    assert [message.content for message in messages] == ["plain string instead of block list"]


def test_one_string_message_no_longer_drops_the_others(tmp_path: Path) -> None:
    """The regression that mattered: the raise aborted every later message."""
    _, session = _session_with(
        tmp_path,
        [
            [{"type": "text", "text": "before"}],
            "string body in the middle",
            [{"type": "text", "text": "after"}],
        ],
    )

    messages = list(dream.parse_messages(tmp_path, session, None))

    assert [message.content for message in messages] == [
        "before",
        "string body in the middle",
        "after",
    ]


def test_block_lists_still_behave_the_same(tmp_path: Path) -> None:
    _, session = _session_with(
        tmp_path,
        [
            [
                {"type": "text", "text": " kept "},
                {"type": "thinking", "text": "dropped"},
                {"type": "text", "text": "also kept"},
            ]
        ],
    )

    messages = list(dream.parse_messages(tmp_path, session, None))

    assert [message.content for message in messages] == ["kept\nalso kept"]


def test_empty_and_unusable_bodies_are_skipped(tmp_path: Path) -> None:
    # Whitespace-only and empty bodies were skipped before and still are; a
    # non-dict block inside a list would have raised the same AttributeError.
    _, session = _session_with(
        tmp_path,
        ["   ", "", [], [{"type": "text", "text": "   "}], ["not a dict"], {"type": "text"}],
    )

    assert list(dream.parse_messages(tmp_path, session, None)) == []


def test_message_text_helper_directly() -> None:
    assert dream._message_text("hello") == "hello"
    assert dream._message_text([{"type": "text", "text": "hello"}]) == "hello"
    assert dream._message_text(["not a dict"]) == ""
    assert dream._message_text(None) == ""
    assert dream._message_text({"type": "text", "text": "hello"}) == ""
