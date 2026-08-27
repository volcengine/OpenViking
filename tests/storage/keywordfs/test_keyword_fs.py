# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the local keyword (SQLite FTS5) sidecar."""

import sqlite3

import pytest

from openviking.storage.keywordfs.keyword_fs import KeywordFS
from openviking.storage.keywordfs.keyword_msg import Delete, DeletePrefix, KeywordMsg, Move, Upsert
from openviking.storage.keywordfs.keyword_processor import KeywordProcessor
from openviking.storage.keywordfs.tokenizer import has_cjk, terms, tokenize
from openviking_cli.utils.config.keyword_config import KeywordConfig

ACCOUNT = "default"


@pytest.fixture
def kfs(tmp_path):
    return KeywordFS(tmp_path, KeywordConfig(enabled=True, cjk_mode="char"))


# ---------------------------------------------------------------------------
# tokenizer
# ---------------------------------------------------------------------------


def test_tokenizer_latin_and_digits():
    assert tokenize("OpenViking 2.4.1 rollback") == "openviking 2 4 1 rollback"
    assert terms("Version 2.4.1") == ["version", "2", "4", "1"]
    assert has_cjk("单元圆")
    assert not has_cjk("openviking")


def test_tokenizer_cjk_char_and_bigram():
    assert tokenize("单元圆") == "单 元 圆"
    assert tokenize("单元圆", cjk_mode="bigram") == "单 元 圆 单元 元圆"


# ---------------------------------------------------------------------------
# KeywordFS mutations
# ---------------------------------------------------------------------------


def test_upsert_create_and_update(kfs):
    uri = "viking://resources/proj/a.md"
    assert kfs.upsert(ACCOUNT, uri, "OpenViking rollback runbook version 2.4.1", level=2)
    assert kfs.upsert(ACCOUNT, uri, "OpenViking revised content only", level=2)
    assert any(u == uri for u, _ in kfs.lookup(ACCOUNT, "revised", limit=10))
    assert not kfs.lookup(ACCOUNT, "rollback", limit=10), "stale content after update"


def test_upsert_oversized_dropped(kfs):
    uri = "viking://resources/big.md"
    big = "x" * (kfs._config.max_doc_bytes + 10)
    assert kfs.upsert(ACCOUNT, uri, big) is False
    assert kfs.lookup(ACCOUNT, "x", limit=10) == []


def test_delete_and_move(kfs):
    a = "viking://resources/proj/a.md"
    b = "viking://resources/proj/b.md"
    kfs.upsert(ACCOUNT, a, "foo bar token", level=2)
    kfs.upsert(ACCOUNT, b, "foo bar token", level=2)
    assert kfs.move(ACCOUNT, a, "viking://resources/proj/a2.md")
    assert any(u.endswith("a2.md") for u, _ in kfs.lookup(ACCOUNT, "foo", limit=10))
    assert kfs.delete(ACCOUNT, "viking://resources/proj/a2.md")
    assert any(u == b for u, _ in kfs.lookup(ACCOUNT, "foo", limit=10))
    assert not any(u.endswith("a2.md") for u, _ in kfs.lookup(ACCOUNT, "foo", limit=10))


def test_delete_prefix(kfs):
    kfs.upsert(ACCOUNT, "viking://resources/p/a.md", "foo token", level=2)
    kfs.upsert(ACCOUNT, "viking://resources/p/sub/b.md", "foo token", level=2)
    kfs.upsert(ACCOUNT, "viking://resources/other/c.md", "foo token", level=2)
    assert kfs.delete_prefix(ACCOUNT, "viking://resources/p") == 2
    uris = [u for u, _ in kfs.lookup(ACCOUNT, "foo", limit=100)]
    assert uris == ["viking://resources/other/c.md"]


def test_lookup_scope_and_exclude(kfs):
    kfs.upsert(ACCOUNT, "viking://resources/proj/a.md", "openviking rollback", level=2)
    kfs.upsert(ACCOUNT, "viking://resources/other/b.md", "openviking rollback", level=2)
    hits = kfs.lookup(ACCOUNT, "rollback", scope_uri="viking://resources/proj", limit=10)
    assert [u for u, _ in hits] == ["viking://resources/proj/a.md"]
    hits = kfs.lookup(
        ACCOUNT,
        "rollback",
        scope_uri="viking://resources",
        exclude_uri="viking://resources/other",
        limit=10,
    )
    assert [u for u, _ in hits] == ["viking://resources/proj/a.md"]


def test_rebuild_after_live_wal_connection(kfs):
    kfs.upsert(ACCOUNT, "viking://resources/old/a.md", "stale token", level=2)
    items = [
        {"uri": "viking://resources/rebuild/r1.md", "text": "rebuild item one token", "level": 2}
    ]
    assert kfs.rebuild_account(ACCOUNT, items) == 1
    assert kfs.lookup(ACCOUNT, "rebuild", limit=10)
    assert not kfs.lookup(ACCOUNT, "stale", limit=10), "stale rows after rebuild"
    # On-disk db only contains the rebuilt row.
    conn = sqlite3.connect(str(kfs.db_path(ACCOUNT)))
    rows = [r[0] for r in conn.execute("SELECT uri FROM kf")]
    conn.close()
    assert rows == ["viking://resources/rebuild/r1.md"]


def test_clear_and_is_ready(kfs):
    kfs.upsert(ACCOUNT, "viking://resources/a.md", "foo", level=2)
    assert kfs.is_ready(ACCOUNT)
    kfs.clear(ACCOUNT)
    assert not kfs.is_ready(ACCOUNT)


# ---------------------------------------------------------------------------
# KeywordProcessor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_upsert_delete_move_delete_prefix(kfs):
    proc = KeywordProcessor(keyword_fs=kfs, config=KeywordConfig(enabled=True))
    await proc.on_dequeue(
        {"data": KeywordMsg(kind=Upsert, uri="viking://resources/p/a.md", account_id=ACCOUNT, text="foo bar").to_dict()}
    )
    assert kfs.lookup(ACCOUNT, "foo", limit=10)
    await proc.on_dequeue(
        {"data": KeywordMsg(kind=Delete, uri="viking://resources/p/a.md", account_id=ACCOUNT).to_dict()}
    )
    assert not kfs.lookup(ACCOUNT, "foo", limit=10)

    kfs.upsert(ACCOUNT, "viking://resources/p/b.md", "bar token", level=2)
    await proc.on_dequeue(
        {"data": KeywordMsg(kind=Move, old_uri="viking://resources/p/b.md", new_uri="viking://resources/p/b2.md", account_id=ACCOUNT).to_dict()}
    )
    assert any(u.endswith("b2.md") for u, _ in kfs.lookup(ACCOUNT, "bar", limit=10))

    kfs.upsert(ACCOUNT, "viking://resources/p/c.md", "baz token", level=2)
    await proc.on_dequeue(
        {"data": KeywordMsg(kind=DeletePrefix, uri="viking://resources/p", account_id=ACCOUNT).to_dict()}
    )
    assert kfs.lookup(ACCOUNT, "baz", limit=10) == []
    assert kfs.lookup(ACCOUNT, "bar", limit=10) == []


@pytest.mark.asyncio
async def test_processor_disabled_drops(kfs):
    proc = KeywordProcessor(keyword_fs=kfs, config=KeywordConfig(enabled=False))
    await proc.on_dequeue(
        {"data": KeywordMsg(kind=Upsert, uri="viking://resources/x.md", account_id=ACCOUNT, text="whatever").to_dict()}
    )
    assert kfs.lookup(ACCOUNT, "whatever", limit=10) == []


# ---------------------------------------------------------------------------
# KeywordMsg.from_embedding
# ---------------------------------------------------------------------------


def test_from_embedding_text_and_multimodal(kfs):
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg

    emb = EmbeddingMsg(
        message="OpenViking rollback runbook",
        context_data={"uri": "viking://resources/a.md", "account_id": ACCOUNT, "level": 2},
    )
    msg = KeywordMsg.from_embedding(emb)
    assert msg is not None and msg.kind == Upsert and msg.text == "OpenViking rollback runbook"

    emb2 = EmbeddingMsg(
        message=[{"type": "text", "text": "部分内容甲"}, {"type": "image_url", "url": "x"}],
        context_data={"uri": "viking://resources/b.md", "account_id": ACCOUNT, "level": 2},
    )
    msg2 = KeywordMsg.from_embedding(emb2)
    assert msg2 is not None and "部分内容甲" in msg2.text

    emb3 = EmbeddingMsg(message="", context_data={"uri": "viking://resources/c.md", "account_id": ACCOUNT})
    assert KeywordMsg.from_embedding(emb3) is None
