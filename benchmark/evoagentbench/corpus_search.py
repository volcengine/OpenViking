"""Read-only, full-corpus BM25 search for benchmark tools."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

STOPWORDS = set(
    "a an and are as at be by do for from had has have how i in is it of on or "
    "that the their this to was were what when where which who with".split()
)


def excerpt(text: str, tokens: list[str], budget: int = 512, char_budget: int = 4000) -> str:
    """Keep a bounded window with the greatest coverage of query constraints."""
    words = text.split()
    if len(words) <= budget:
        snippet = text
    else:
        normalized = [re.sub(r"\W+", "", word.lower()) for word in words]
        wanted = set(tokens)
        candidates = [*range(0, len(words) - budget + 1, 64), len(words) - budget]
        start = max(
            candidates,
            key=lambda i: len(wanted.intersection(normalized[i : i + budget])),
        )
        snippet = ("… " if start else "") + " ".join(words[start : start + budget]) + " …"
    # A comma-separated word list can be 800 KB but count as a single word.
    # Keep such a result inline: the retrieval agent receives results inline.
    if len(snippet) > char_budget:
        width = char_budget - 4
        lowered = snippet.lower()
        positions = [lowered.find(t.lower()) for t in tokens if t]
        starts = {0, len(snippet) - width}
        starts.update(
            max(0, min(p - width // 3, len(snippet) - width)) for p in positions if p >= 0
        )
        start = max(
            starts, key=lambda i: (sum(t.lower() in lowered[i : i + width] for t in tokens), -i)
        )
        return "… " + snippet[start : start + width] + " …"
    return snippet


def search_corpus(corpus: Path, query: str, k: int = 5) -> dict:
    tokens = [word for word in re.findall(r"\w+", query.lower()) if word not in STOPWORDS][:32]
    if not tokens:
        return {"query": query, "results": []}
    quoted = [f'"{word}"' for word in dict.fromkeys(tokens)]
    with sqlite3.connect(f"{corpus.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        if not connection.execute("SELECT value FROM metadata WHERE key='complete'").fetchone():
            raise RuntimeError("Corpus index is incomplete")
        for operator in (" AND ", " OR "):
            expression = operator.join(quoted)
            rows = connection.execute(
                "SELECT docid,url,text,bm25(documents) "
                "FROM documents WHERE documents MATCH ? ORDER BY bm25(documents) LIMIT ?",
                (expression, max(1, min(k, 10))),
            ).fetchall()
            if rows:
                break
    return {
        "query": query,
        "retriever": "full-corpus SQLite FTS5 BM25",
        "results": [
            {"docid": docid, "url": url, "text": excerpt(text, tokens), "score": -score}
            for docid, url, text, score in rows
        ],
    }
