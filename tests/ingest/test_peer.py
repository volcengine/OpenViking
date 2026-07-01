# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""peer_id resolution: assistant model peers, human/git peers, group usernames."""

from openviking.ingest.peer import (
    assistant_peer_id,
    resolve_git_human_peer,
    safe_external_peer,
)


def test_assistant_peer_with_and_without_provider():
    assert assistant_peer_id("claude_code", "claude-opus-4-8") == "claude_code__claude-opus-4-8"
    assert assistant_peer_id("opencode", "glm-4.7", "tiktok") == "opencode__tiktok__glm-4.7"
    assert assistant_peer_id("codex", None) == "codex__unknown"


def test_external_peer_readable_and_sanitized():
    assert safe_external_peer("alice") == "alice"
    assert safe_external_peer("zhengxiao.wu@bytedance.com") == "zhengxiao.wu@bytedance.com"
    assert safe_external_peer("foo/bar baz") == "foo-bar-baz"
    assert safe_external_peer("") is None


def test_external_peer_non_ascii_falls_back_to_ext():
    pid = safe_external_peer("杨冠姝")
    assert pid is not None and pid.startswith("ext-")


def test_git_human_peer_falls_back_without_repo(tmp_path):
    # tmp_path is not a git repo -> configured fallback is used
    assert resolve_git_human_peer(str(tmp_path), "me") == "me"
    assert resolve_git_human_peer(None, "me") == "me"
