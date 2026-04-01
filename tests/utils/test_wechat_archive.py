# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from pathlib import Path

from openviking.utils.wechat_archive import export_wechat_archive


def test_export_wechat_archive_builds_markdown_and_copies_link_docs(tmp_path: Path):
    source_root = tmp_path / "chat_archive"
    chat_dir = source_root / "chats" / "demo__chat_1"
    message_dir = chat_dir / "messages"
    message_dir.mkdir(parents=True)

    (chat_dir / "chat_meta.json").write_text(
        json.dumps(
            {
                "chat_id": "chat_1",
                "current_name": "测试群",
                "chat_type": "chatroom",
                "dir_name": "demo__chat_1",
                "first_seen_ts": "2026-03-30T10:00:00",
                "last_seen_ts": "2026-03-31T11:00:00",
                "aliases": ["测试群"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    linked_doc = source_root / "link_docs" / "doc1" / "2026-03-31" / "9" / "document.md"
    linked_doc.parent.mkdir(parents=True)
    linked_doc.write_text("# 文章正文\n\n这里是正文。", encoding="utf-8")

    messages = [
        {
            "message_key": "chat_1::1",
            "event_kind": "message",
            "sender": "Alice",
            "base_type": 1,
            "sub_type": 0,
            "type_label": "文本",
            "message_ts": 1774924770,
            "first_seen_ts": "2026-03-31T10:39:44",
            "processed_ts": "2026-03-31T10:40:00",
            "content": "今天聊自动驾驶测试。",
            "details": {},
            "analysis": {
                "analysis_text": "讨论主题是自动驾驶测试。",
                "url_list": [],
                "document": {
                    "status": "skip",
                    "doc_type": "skip",
                    "doc_path": "",
                    "summary": "",
                },
            },
        },
        {
            "message_key": "chat_1::2",
            "event_kind": "message",
            "sender": "Bob",
            "base_type": 49,
            "sub_type": 5,
            "type_label": "分享链接",
            "message_ts": 1774924870,
            "first_seen_ts": "2026-03-31T10:41:44",
            "processed_ts": "2026-03-31T10:42:00",
            "content": "一篇相关文章",
            "details": {
                "title": "自动驾驶测试白皮书",
                "url": "https://example.com/doc",
            },
            "analysis": {
                "analysis_text": "",
                "url_list": ["https://example.com/doc"],
                "document": {
                    "status": "ok",
                    "doc_type": "wechat_article",
                    "doc_path": str(linked_doc),
                    "summary": "自动驾驶测试白皮书摘要",
                },
            },
        },
    ]

    (message_dir / "2026-03-31.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in messages) + "\n",
        encoding="utf-8",
    )

    export_root = tmp_path / "export"
    stats = export_wechat_archive(source_root, export_root)

    assert stats.chats == 1
    assert stats.message_files == 1
    assert stats.messages == 2
    assert stats.linked_docs == 1

    day_file = export_root / "chats" / "demo__chat_1" / "days" / "2026-03-31.md"
    assert day_file.exists()
    day_content = day_file.read_text(encoding="utf-8")
    assert "今天聊自动驾驶测试。" in day_content
    assert "自动驾驶测试白皮书摘要" in day_content
    assert "link_docs/doc1/2026-03-31/9/document.md" in day_content

    copied_doc = export_root / "link_docs" / "doc1" / "2026-03-31" / "9" / "document.md"
    assert copied_doc.exists()
    assert "这里是正文。" in copied_doc.read_text(encoding="utf-8")

    root_readme = export_root / "README.md"
    assert root_readme.exists()
    assert "测试群" in root_readme.read_text(encoding="utf-8")


def test_export_wechat_archive_reports_missing_linked_doc(tmp_path: Path):
    source_root = tmp_path / "chat_archive"
    chat_dir = source_root / "chats" / "demo__chat_2"
    message_dir = chat_dir / "messages"
    message_dir.mkdir(parents=True)

    (chat_dir / "chat_meta.json").write_text(
        json.dumps({"chat_id": "chat_2", "current_name": "告警群"}, ensure_ascii=False),
        encoding="utf-8",
    )

    missing_doc = source_root / "link_docs" / "missing" / "document.md"
    message = {
        "message_key": "chat_2::1",
        "event_kind": "message",
        "sender": "System",
        "base_type": 49,
        "sub_type": 5,
        "type_label": "分享链接",
        "message_ts": 1774924870,
        "first_seen_ts": "2026-03-31T10:41:44",
        "processed_ts": "2026-03-31T10:42:00",
        "content": "无法抓取的链接",
        "details": {"url": "https://example.com/missing"},
        "analysis": {
            "analysis_text": "",
            "url_list": ["https://example.com/missing"],
            "document": {
                "status": "ok",
                "doc_type": "wechat_article",
                "doc_path": str(missing_doc),
                "summary": "",
            },
        },
    }
    (message_dir / "2026-03-31.jsonl").write_text(
        json.dumps(message, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    stats = export_wechat_archive(source_root, tmp_path / "export")

    assert stats.linked_docs == 0
    assert any("Linked document missing" in warning for warning in stats.warnings)
