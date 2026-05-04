# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unit tests for WM v2 guards, regex recovery, merge, and pending_tokens.

These tests target pure/static methods on Session and related helpers,
so they don't need a running OpenViking server.
"""

from openviking.session.session import Session, WM_SEVEN_SECTIONS


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_wm(**section_bodies: str) -> str:
    """Build a valid WM v2 markdown document for test input.

    Keyword args map section short-names (underscored) to body text:
        _make_wm(session_title="Debug Session", current_state="Working")
    Unmapped sections get empty bodies.
    """
    parts = ["# Working Memory", ""]
    for section in WM_SEVEN_SECTIONS:
        key = section.lower().replace(" ", "_").replace("&", "and")
        body = section_bodies.get(key, "")
        parts.append(f"## {section}")
        if body:
            parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _calc_pending_tokens(tokens: list, keep_recent_count: int) -> int:
    """Mirror the _rebuild_pending_tokens math for unit testing."""
    msgs = [type("M", (), {"estimated_tokens": t})() for t in tokens]
    keep = int(keep_recent_count or 0)
    total = len(msgs)
    if keep <= 0:
        return sum(int(m.estimated_tokens or 0) for m in msgs)
    elif total > keep:
        return sum(int(m.estimated_tokens or 0) for m in msgs[:total - keep])
    return 0


# =======================================================================
# _parse_wm_sections
# =======================================================================

class TestParseWmSections:
    def test_round_trip(self):
        wm = _make_wm(session_title="My Session", current_state="Idle")
        parsed = Session._parse_wm_sections(wm)
        assert parsed["## Session Title"] == "My Session"
        assert parsed["## Current State"] == "Idle"
        assert parsed.get("## Task & Goals", "") == ""

    def test_empty_input(self):
        assert Session._parse_wm_sections("") == {}
        assert Session._parse_wm_sections(None) == {}

    def test_multiline_body(self):
        body = "- item 1\n- item 2\n- item 3"
        wm = _make_wm(key_facts_and_decisions=body)
        parsed = Session._parse_wm_sections(wm)
        assert "item 1" in parsed["## Key Facts & Decisions"]
        assert "item 3" in parsed["## Key Facts & Decisions"]


# =======================================================================
# _wm_extract_bullet_items
# =======================================================================

class TestExtractBulletItems:
    def test_dash_bullets(self):
        items = Session._wm_extract_bullet_items("- foo\n- bar\n- baz")
        assert items == ["foo", "bar", "baz"]

    def test_numbered_list(self):
        items = Session._wm_extract_bullet_items("1. first\n2. second\n3) third")
        assert items == ["first", "second", "third"]

    def test_plain_lines(self):
        items = Session._wm_extract_bullet_items("plain line one\nplain line two")
        assert items == ["plain line one", "plain line two"]

    def test_skips_headings_and_blank(self):
        items = Session._wm_extract_bullet_items("# heading\n\n- real")
        assert items == ["real"]

    def test_empty(self):
        assert Session._wm_extract_bullet_items("") == []
        assert Session._wm_extract_bullet_items(None) == []


# =======================================================================
# _wm_enforce_append_only (Key Facts, Errors & Corrections)
# =======================================================================

class TestEnforceAppendOnly:
    def test_keep_passes_through(self):
        op = {"op": "KEEP"}
        result = Session._wm_enforce_append_only("Key Facts & Decisions", op, "- old fact")
        assert result == {"op": "KEEP"}

    def test_append_passes_through(self):
        op = {"op": "APPEND", "items": ["new item"]}
        result = Session._wm_enforce_append_only("Key Facts & Decisions", op, "- old fact")
        assert result == op

    def test_update_demoted_to_append(self):
        old = "- existing fact"
        op = {"op": "UPDATE", "content": "- existing fact\n- brand new fact"}
        result = Session._wm_enforce_append_only("Key Facts & Decisions", op, old)
        assert result["op"] == "APPEND"
        assert "brand new fact" in result["items"]

    def test_update_with_all_duplicates_becomes_keep(self):
        old = "- fact alpha\n- fact beta"
        op = {"op": "UPDATE", "content": "- fact alpha\n- fact beta"}
        result = Session._wm_enforce_append_only("Key Facts & Decisions", op, old)
        assert result["op"] == "KEEP"

    def test_none_op_becomes_keep(self):
        result = Session._wm_enforce_append_only("Errors & Corrections", None, "old")
        assert result == {"op": "KEEP"}

    def test_unknown_op_becomes_keep(self):
        result = Session._wm_enforce_append_only("Errors & Corrections", {"op": "DELETE"}, "old")
        assert result == {"op": "KEEP"}


# =======================================================================
# _wm_enforce_files_no_regression
# =======================================================================

class TestEnforceFilesNoRegression:
    def test_keep_passes_through(self):
        result = Session._wm_enforce_files_no_regression({"op": "KEEP"}, "- src/main.py")
        assert result == {"op": "KEEP"}

    def test_update_preserving_all_paths(self):
        old = "- src/main.py\n- config.yaml"
        op = {"op": "UPDATE", "content": "- src/main.py updated\n- config.yaml stays\n- new.ts added"}
        result = Session._wm_enforce_files_no_regression(op, old)
        assert result["op"] == "UPDATE"

    def test_update_dropping_path_rejected(self):
        old = "- src/main.py\n- src/utils.ts\n- config.yaml"
        op = {"op": "UPDATE", "content": "- src/main.py\n- config.yaml"}
        result = Session._wm_enforce_files_no_regression(op, old)
        assert result["op"] in ("KEEP", "APPEND")

    def test_update_dropping_path_but_adding_new(self):
        old = "- src/main.py\n- src/utils.ts"
        op = {"op": "UPDATE", "content": "- src/main.py\n- brand_new/file.go"}
        result = Session._wm_enforce_files_no_regression(op, old)
        assert result["op"] in ("KEEP", "APPEND")
        if result["op"] == "APPEND":
            items_text = " ".join(result.get("items", []))
            assert "brand_new/file.go" in items_text

    def test_append_passes_through(self):
        op = {"op": "APPEND", "items": ["new_dir/new_file.rs"]}
        result = Session._wm_enforce_files_no_regression(op, "- src/main.py")
        assert result == op

    def test_none_op(self):
        result = Session._wm_enforce_files_no_regression(None, "old")
        assert result == {"op": "KEEP"}


# =======================================================================
# _wm_enforce_title_stability
# =======================================================================

class TestEnforceTitleStability:
    def test_keep_passes_through(self):
        result = Session._wm_enforce_title_stability({"op": "KEEP"}, "old title")
        assert result == {"op": "KEEP"}

    def test_update_with_overlap_accepted(self):
        op = {"op": "UPDATE", "content": "Debug Session Refinement"}
        result = Session._wm_enforce_title_stability(op, "Debug Session Setup")
        assert result["op"] == "UPDATE"

    def test_update_with_zero_overlap_rejected(self):
        op = {"op": "UPDATE", "content": "Completely Different Topic"}
        result = Session._wm_enforce_title_stability(op, "Database Migration Plan")
        assert result == {"op": "KEEP"}

    def test_empty_old_title_accepts_anything(self):
        op = {"op": "UPDATE", "content": "Brand New Title"}
        result = Session._wm_enforce_title_stability(op, "")
        assert result["op"] == "UPDATE"

    def test_stopwords_only_old_title_accepts(self):
        """When old title has NO meaningful words (all stopwords), any update is accepted."""
        op = {"op": "UPDATE", "content": "The Plan for Notes"}
        result = Session._wm_enforce_title_stability(op, "A Session Title")
        assert result["op"] == "UPDATE"

    def test_meaningful_words_no_overlap_rejected(self):
        op = {"op": "UPDATE", "content": "React Components"}
        result = Session._wm_enforce_title_stability(op, "Python Migration Tools")
        assert result == {"op": "KEEP"}


# =======================================================================
# _wm_enforce_open_issues_resolved
# =======================================================================

class TestEnforceOpenIssuesResolved:
    def test_update_preserving_all_passes(self):
        old = "- bug in auth module\n- slow query on /api/users"
        new_content = "- bug in auth module (investigating)\n- slow query on /api/users\n- new: memory leak"
        op = {"op": "UPDATE", "content": new_content}
        result = Session._wm_enforce_open_issues_resolved(op, old)
        assert result["op"] == "UPDATE"
        assert "memory leak" in result["content"]

    def test_update_silently_dropping_item_restores(self):
        old = "- bug in auth module\n- slow query on /api/users"
        op = {"op": "UPDATE", "content": "- slow query on /api/users"}
        result = Session._wm_enforce_open_issues_resolved(op, old)
        assert result["op"] == "UPDATE"
        assert "silently dropped, restored" in result["content"]
        assert "bug in auth" in result["content"]

    def test_keep_passes_through(self):
        result = Session._wm_enforce_open_issues_resolved({"op": "KEEP"}, "old")
        assert result == {"op": "KEEP"}

    def test_already_restored_item_not_restored_again(self):
        """Items already tagged [silently dropped, restored] should NOT be
        restored a second time -- they had their chance."""
        old = (
            "- [silently dropped, restored] stale follow-up issue\n"
            "- fresh unresolved bug"
        )
        op = {"op": "UPDATE", "content": "- fresh unresolved bug"}
        result = Session._wm_enforce_open_issues_resolved(op, old)
        assert "stale follow-up" not in result["content"]
        assert "fresh unresolved bug" in result["content"]

    def test_multi_layer_restored_tags_not_restored(self):
        """Items with nested [silently dropped, restored] tags must not
        be restored again (regression guard for the old accumulation bug)."""
        old = (
            "- [silently dropped, restored] [silently dropped, restored] old issue\n"
            "- active issue"
        )
        op = {"op": "UPDATE", "content": "- active issue"}
        result = Session._wm_enforce_open_issues_resolved(op, old)
        assert "old issue" not in result["content"]
        assert "active issue" in result["content"]

    def test_fresh_item_still_restored_once(self):
        """A fresh item (no tag) that gets dropped should be restored with
        the marker exactly once."""
        old = "- never-restored issue\n- kept issue"
        op = {"op": "UPDATE", "content": "- kept issue"}
        result = Session._wm_enforce_open_issues_resolved(op, old)
        assert "[silently dropped, restored]" in result["content"]
        assert "never-restored issue" in result["content"]


# =======================================================================
# _merge_wm_sections
# =======================================================================

class TestMergeWmSections:
    def test_all_keep(self):
        old = _make_wm(session_title="Title", current_state="Working")
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        result = Session._merge_wm_sections(old, ops)
        assert "## Session Title" in result
        assert "Title" in result
        assert "Working" in result

    def test_update_current_state(self):
        old = _make_wm(current_state="Idle")
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        ops["Current State"] = {"op": "UPDATE", "content": "Active debugging"}
        result = Session._merge_wm_sections(old, ops)
        assert "Active debugging" in result
        assert "Idle" not in result

    def test_append_to_open_issues(self):
        old = _make_wm(open_issues="- existing issue")
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        ops["Open Issues"] = {"op": "APPEND", "items": ["new issue found"]}
        result = Session._merge_wm_sections(old, ops)
        assert "existing issue" in result
        assert "new issue found" in result

    def test_missing_section_defaults_to_keep(self):
        old = _make_wm(session_title="Original Title")
        ops = {}
        result = Session._merge_wm_sections(old, ops)
        assert "Original Title" in result

    def test_guard_key_facts_update_accepted_when_no_anchors(self):
        """With trivially small content and no lexical anchors, UPDATE passes
        the consolidation guard — old items are replaced."""
        old = _make_wm(key_facts_and_decisions="- fact A\n- fact B")
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        ops["Key Facts & Decisions"] = {"op": "UPDATE", "content": "- fact A\n- fact C"}
        result = Session._merge_wm_sections(old, ops)
        assert "fact A" in result
        assert "fact C" in result
        assert "fact B" not in result

    def test_guard_title_drift_blocked(self):
        old = _make_wm(session_title="Database Migration Plan")
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        ops["Session Title"] = {"op": "UPDATE", "content": "Completely Unrelated Topic"}
        result = Session._merge_wm_sections(old, ops)
        assert "Database Migration Plan" in result
        assert "Completely Unrelated Topic" not in result

    def test_all_seven_sections_present(self):
        old = _make_wm()
        ops = {s: {"op": "KEEP"} for s in WM_SEVEN_SECTIONS}
        result = Session._merge_wm_sections(old, ops)
        for section in WM_SEVEN_SECTIONS:
            assert f"## {section}" in result

    def test_none_ops(self):
        old = _make_wm(session_title="Keep Me")
        result = Session._merge_wm_sections(old, None)
        assert "Keep Me" in result
        for section in WM_SEVEN_SECTIONS:
            assert f"## {section}" in result


# =======================================================================
# _wm_recover_ops_from_raw
# =======================================================================

class TestWmRecoverOpsFromRaw:
    def test_recover_keep_ops(self):
        raw = '"Session Title": {"op": "KEEP"}, "Current State": {"op": "KEEP"}'
        ops = Session._wm_recover_ops_from_raw(raw)
        assert ops["Session Title"] == {"op": "KEEP"}
        assert ops["Current State"] == {"op": "KEEP"}

    def test_recover_update_op(self):
        raw = '"Current State": {"op": "UPDATE", "content": "Now debugging auth"}'
        ops = Session._wm_recover_ops_from_raw(raw)
        assert ops["Current State"]["op"] == "UPDATE"
        assert "debugging auth" in ops["Current State"]["content"]

    def test_recover_append_op(self):
        raw = '"Open Issues": {"op": "APPEND", "items": ["new bug", "another issue"]}'
        ops = Session._wm_recover_ops_from_raw(raw)
        assert ops["Open Issues"]["op"] == "APPEND"
        assert "new bug" in ops["Open Issues"]["items"]
        assert "another issue" in ops["Open Issues"]["items"]

    def test_recover_mixed_ops(self):
        raw = (
            '"Session Title": {"op": "KEEP"}, '
            '"Current State": {"op": "UPDATE", "content": "active"}, '
            '"Open Issues": {"op": "APPEND", "items": ["todo"]}'
        )
        ops = Session._wm_recover_ops_from_raw(raw)
        assert len(ops) == 3
        assert ops["Session Title"]["op"] == "KEEP"
        assert ops["Current State"]["op"] == "UPDATE"
        assert ops["Open Issues"]["op"] == "APPEND"

    def test_empty_input(self):
        assert Session._wm_recover_ops_from_raw("") == {}
        assert Session._wm_recover_ops_from_raw(None) == {}

    def test_partial_recovery(self):
        raw = '"Session Title": {"op": "KEEP"}, garbled content here...'
        ops = Session._wm_recover_ops_from_raw(raw)
        assert "Session Title" in ops
        assert len(ops) >= 1

    def test_truncated_update_content(self):
        raw = (
            '"Session Title": {"op": "KEEP"}, '
            '"Current State": {"op": "UPDATE", "content": "partial content '
        )
        ops = Session._wm_recover_ops_from_raw(raw)
        assert "Session Title" in ops

    def test_escaped_content(self):
        raw = r'"Current State": {"op": "UPDATE", "content": "line1\nline2 with \"quotes\""}'
        ops = Session._wm_recover_ops_from_raw(raw)
        assert ops["Current State"]["op"] == "UPDATE"
        assert "line1" in ops["Current State"]["content"]


# =======================================================================
# _rebuild_pending_tokens (via SessionMeta simulation)
# =======================================================================

class TestRebuildPendingTokens:
    """Test the _rebuild_pending_tokens math using _calc_pending_tokens helper."""

    def test_no_keep_sums_all(self):
        assert _calc_pending_tokens([100, 200, 300], keep_recent_count=0) == 600

    def test_keep_2_of_5(self):
        assert _calc_pending_tokens([100, 200, 300, 400, 500], keep_recent_count=2) == 600

    def test_keep_more_than_total(self):
        assert _calc_pending_tokens([100, 100, 100], keep_recent_count=10) == 0

    def test_none_tokens_treated_as_zero(self):
        assert _calc_pending_tokens([None, None, None], keep_recent_count=0) == 0

    def test_empty_messages(self):
        assert _calc_pending_tokens([], keep_recent_count=0) == 0

    def test_keep_equals_total(self):
        assert _calc_pending_tokens([100, 200], keep_recent_count=2) == 0


# =======================================================================
# _is_wm_v2 detection (tests the condition in _generate_archive_summary_async)
# =======================================================================

class TestIsWmV2Detection:
    def _is_wm_v2(self, overview: str) -> bool:
        """Mirror the detection logic from session.py."""
        return bool(overview) and any(
            f"## {s}" in overview for s in WM_SEVEN_SECTIONS
        )

    def test_valid_v2(self):
        wm = _make_wm(session_title="Test")
        assert self._is_wm_v2(wm) is True

    def test_legacy_format(self):
        legacy = "Session overview: this is a legacy format with no sections"
        assert self._is_wm_v2(legacy) is False

    def test_empty(self):
        assert self._is_wm_v2("") is False
        assert self._is_wm_v2(None) is False

    def test_partial_sections(self):
        partial = "## Session Title\nSome title\n## Current State\nWorking"
        assert self._is_wm_v2(partial) is True

    def test_wrong_heading_level(self):
        """'### Session Title' contains '## Session Title' as substring -> matches."""
        wrong = "### Session Title\nSome title"
        assert self._is_wm_v2(wrong) is True

    def test_no_section_headers(self):
        no_headers = "Just some plain text without any section headers."
        assert self._is_wm_v2(no_headers) is False


# =======================================================================
# WM_PATH_LIKE_RE
# =======================================================================

class TestWmPathLikeRe:
    def test_python_file(self):
        assert Session._WM_PATH_LIKE_RE.search("src/main.py")

    def test_typescript_file(self):
        assert Session._WM_PATH_LIKE_RE.search("components/App.tsx")

    def test_deep_path(self):
        assert Session._WM_PATH_LIKE_RE.search("openviking/session/session")

    def test_yaml_file(self):
        assert Session._WM_PATH_LIKE_RE.search("config.yaml")

    def test_no_match_plain_word(self):
        assert not Session._WM_PATH_LIKE_RE.search("hello")


# =======================================================================
# pending_tokens defensive clamp
# =======================================================================

class TestPendingTokensClamp:
    """Verify that pending_tokens is always clamped to >= 0."""

    def test_negative_pending_tokens_clamped_on_rebuild(self):
        result = _calc_pending_tokens([], keep_recent_count=0)
        assert result >= 0

    def test_negative_keep_recent_count_treated_as_zero(self):
        result = _calc_pending_tokens([100, 200], keep_recent_count=-5)
        assert result == 300

    def test_from_dict_clamps_negative_pending(self):
        from openviking.session.session import SessionMeta
        data = {"pending_tokens": -100, "keep_recent_count": -5}
        meta = SessionMeta.from_dict(data)
        assert meta.pending_tokens >= 0
        assert meta.keep_recent_count >= 0


# =======================================================================
# APPEND non-string items handling
# =======================================================================

class TestAppendNonStringItems:
    """APPEND items that are not strings should be dropped (not crash)."""

    def test_append_with_mixed_types_keeps_strings(self):
        old_wm = _make_wm(open_issues="- Existing issue")
        ops = {
            "Session Title": {"op": "KEEP"},
            "Current State": {"op": "KEEP"},
            "Task & Goals": {"op": "KEEP"},
            "Key Facts & Decisions": {"op": "KEEP"},
            "Files & Context": {"op": "KEEP"},
            "Errors & Corrections": {"op": "KEEP"},
            "Open Issues": {"op": "APPEND", "items": ["valid item", 42, None, {"bad": True}]},
        }
        merged = Session._merge_wm_sections(old_wm, ops)
        assert "valid item" in merged
        assert "Existing issue" in merged
        assert "42" not in merged

    def test_append_with_empty_items(self):
        old_wm = _make_wm(open_issues="- Existing issue")
        ops = {
            "Session Title": {"op": "KEEP"},
            "Current State": {"op": "KEEP"},
            "Task & Goals": {"op": "KEEP"},
            "Key Facts & Decisions": {"op": "KEEP"},
            "Files & Context": {"op": "KEEP"},
            "Errors & Corrections": {"op": "KEEP"},
            "Open Issues": {"op": "APPEND", "items": []},
        }
        merged = Session._merge_wm_sections(old_wm, ops)
        assert "Existing issue" in merged


# =======================================================================
# _merge_wm_sections edge cases
# =======================================================================

class TestMergeWmSectionsEdgeCases:

    def test_missing_ops_default_to_keep(self):
        old_wm = _make_wm(
            session_title="Original Title",
            current_state="Running",
        )
        merged = Session._merge_wm_sections(old_wm, {})
        assert "Original Title" in merged
        assert "Running" in merged

    def test_unknown_op_defaults_to_keep(self):
        old_wm = _make_wm(current_state="Running")
        ops = {
            "Session Title": {"op": "KEEP"},
            "Current State": {"op": "UNKNOWN_OP"},
            "Task & Goals": {"op": "KEEP"},
            "Key Facts & Decisions": {"op": "KEEP"},
            "Files & Context": {"op": "KEEP"},
            "Errors & Corrections": {"op": "KEEP"},
            "Open Issues": {"op": "KEEP"},
        }
        merged = Session._merge_wm_sections(old_wm, ops)
        assert "Running" in merged

    def test_all_sections_update(self):
        old_wm = _make_wm(
            session_title="Old Title",
            current_state="Old State",
        )
        ops = {
            "Session Title": {"op": "UPDATE", "content": "Old Title (updated)"},
            "Current State": {"op": "UPDATE", "content": "New State"},
            "Task & Goals": {"op": "UPDATE", "content": "New Goals"},
            "Key Facts & Decisions": {"op": "UPDATE", "content": "- New fact"},
            "Files & Context": {"op": "UPDATE", "content": "- new/file.py"},
            "Errors & Corrections": {"op": "KEEP"},
            "Open Issues": {"op": "UPDATE", "content": "- New issue"},
        }
        merged = Session._merge_wm_sections(old_wm, ops)
        assert "New State" in merged
        assert "New Goals" in merged


# -----------------------------------------------------------------------
# _format_message_for_wm
# -----------------------------------------------------------------------

from openviking.message.message import Message
from openviking.message.part import TextPart, ToolPart, ContextPart


def _msg(role, parts):
    """Convenience builder for a Message with an auto-id."""
    return Message(id="test-msg", role=role, parts=parts)


class TestFormatMessageForWm:
    """Tests for Session._format_message_for_wm."""

    def test_text_only(self):
        m = _msg("user", [TextPart(text="Hello world")])
        result = Session._format_message_for_wm(m)
        assert result == "[user]: Hello world"

    def test_tool_part_included(self):
        m = _msg("assistant", [
            ToolPart(tool_name="search", tool_status="completed",
                     tool_output="found 3 results"),
        ])
        result = Session._format_message_for_wm(m)
        assert "[tool:search (completed)]" in result
        assert "found 3 results" in result
        assert result.startswith("[assistant]:")

    def test_context_part_included(self):
        m = _msg("assistant", [
            ContextPart(abstract="Summary of prior session"),
        ])
        result = Session._format_message_for_wm(m)
        assert "[context] Summary of prior session" in result

    def test_mixed_parts(self):
        m = _msg("assistant", [
            TextPart(text="Let me check."),
            ToolPart(tool_name="read_file", tool_status="completed",
                     tool_output="/path/to/file content here"),
            TextPart(text="Done reading."),
        ])
        result = Session._format_message_for_wm(m)
        lines = result.split("\n")
        assert lines[0] == "[assistant]: Let me check."
        assert "[tool:read_file (completed)]" in lines[1]
        assert "Done reading." in lines[2]

    def test_empty_parts(self):
        m = _msg("user", [])
        result = Session._format_message_for_wm(m)
        assert "(no content)" in result

    def test_whitespace_only_text_skipped(self):
        m = _msg("user", [TextPart(text="   ")])
        result = Session._format_message_for_wm(m)
        assert "(no content)" in result

    def test_tool_with_empty_output(self):
        m = _msg("assistant", [
            ToolPart(tool_name="delete", tool_status="completed", tool_output=""),
        ])
        result = Session._format_message_for_wm(m)
        assert "[tool:delete (completed)]" in result

    def test_tool_default_status(self):
        m = _msg("assistant", [
            ToolPart(tool_name="run", tool_output="ok"),
        ])
        result = Session._format_message_for_wm(m)
        assert "(pending)" in result
