# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SAG2 tokenization, FTS expressions and deterministic lexical helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Iterable, Optional

import jieba

TOKENIZER_PROFILE = "sag2-zh-v1"
TOKENIZER_IMPLEMENTATION = f"jieba-{version('jieba')}"
CHUNK_POLICY = "ov-record-v1"
SCHEMA_VERSION = 1

_STOPWORDS = frozenset(
    "的 了 是 在 和 与 或 哪 什么 哪个 哪些 如何 怎么 为什么 是否 这 那 一个 一种 请 我 你 他 它 吗 呢 吧 地 得 "
    "the a an of in on at to for and or is are was were be by with from which what who how when where does do did that this it as".split()
)
_WORD = re.compile(r"\w", re.UNICODE)
_NAME_WORD = r"(?:[A-Z]\.|[A-Z][^\W\d_]*(?:[-’'][A-Z][^\W\d_]*)*)"
_NAMES = re.compile(rf"(?<![\w]){_NAME_WORD}(?:[ \t]+{_NAME_WORD})+(?![\w])", re.UNICODE)
_SENTENCES = re.compile(r"(?<!\b[A-Z])[.!?]\s+|[。！？]+\s*|\n+")


def normalize_text(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def tokenize(text: Any) -> list[str]:
    """Match SAG-Wiki's NFKC/lowercase/jieba-search token pipeline."""
    normalized = normalize_text(text)
    return [
        token
        for token in jieba.cut_for_search(normalized, HMM=True)
        if _WORD.search(token) and token not in _STOPWORDS
    ]


def index_text(text: Any) -> str:
    return " ".join(tokenize(text))


def quote(term: str) -> str:
    """Quote one already-tokenized value for FTS5; never concatenate raw queries."""
    return '"{}"'.format(str(term).replace('"', '""'))


def search_terms(text: Any, limit: int = 80) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in tokenize(text):
        if term not in seen:
            seen.add(term)
            terms.append(term)
            if len(terms) == limit:
                break
    return terms


def match_query(text: Any, limit: int = 80) -> str:
    return " OR ".join(quote(term) for term in search_terms(text, limit))


def expression_for_terms(terms: Iterable[str]) -> str:
    return " OR ".join(quote(term) for term in terms)


def name_phrases(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), " ".join(tokenize(m.group()))) for m in _NAMES.finditer(text)]


def sentences(text: str) -> list[str]:
    result: list[str] = []
    end = 0
    for match in _SENTENCES.finditer(text):
        result.append(text[end : match.start()])
        end = match.end()
    result.append(text[end:])
    return result


def normalized_alias(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", normalize_text(value).strip())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def title_and_heading(record: dict[str, Any]) -> tuple[str, str]:
    uri = str(record.get("uri") or "")
    title = str(record.get("name") or uri.rstrip("/").rsplit("/", 1)[-1])
    heading = uri.rstrip("/").rsplit("/", 1)[0] if "/" in uri.rstrip("/") else ""
    return title, heading


@dataclass(frozen=True)
class Term:
    term: str
    score: float
    kind: str = "word"


@dataclass
class Sag2TenantScope:
    """Canonical account/ACL/filter scope shared by every hop of one query."""

    account_id: str
    filter: Any
    epoch: int
    async_adapter: Any

    async def query(
        self,
        expression: str,
        *,
        limit: int,
        output_fields: Optional[list[str]] = None,
        required_term: Optional[str] = None,
        excluded_ids: Optional[Iterable[str]] = None,
    ) -> list[dict[str, Any]]:
        return await self.async_adapter.call(
            "lexical_query",
            expression=expression,
            filter=self.filter,
            limit=limit,
            output_fields=output_fields,
            required_term=required_term,
            excluded_ids=list(excluded_ids or []),
            expected_epoch=self.epoch,
            account_id=self.account_id,
        )

    async def has_match(self, expression: str, excluded_ids: Iterable[str] = ()) -> bool:
        return bool(
            await self.query(expression, limit=1, output_fields=["id"], excluded_ids=excluded_ids)
        )

    async def titles(self) -> list[dict[str, Any]]:
        return await self.async_adapter.call(
            "scope_titles",
            filter=self.filter,
            expected_epoch=self.epoch,
            account_id=self.account_id,
        )

    async def term_frequencies(self, terms: Iterable[str]) -> tuple[int, dict[str, int]]:
        return await self.async_adapter.call(
            "term_frequencies",
            account_id=self.account_id,
            terms=list(terms),
            expected_epoch=self.epoch,
        )

    async def check_epoch(self) -> None:
        await self.async_adapter.call(
            "check_epoch", account_id=self.account_id, expected_epoch=self.epoch
        )
