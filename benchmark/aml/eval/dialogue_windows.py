# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay auxiliary source-dialogue retrieval without Add or model requests.

Existing memory results must already be selected/reranked. This module only
appends bounded, lossless dialogue windows; it does not alter native retrieval,
memory extraction, Answer prompts, or Judge rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

SOURCE_HEADER = "# Source dialogue context (record dates; event dates may differ)\n"
_SPEAKER = re.compile(r"\[D\d+:\d+\] ([^:]+):")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def build_windows(sources: Mapping[str, Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One focal message per window, grouped by user and physical session.

    Keys embed previous/current/next; values retain previous-two/current/next.
    Window construction never reads evaluation questions or reference answers.
    Explicit speakers are supported; otherwise LoCoMo name prefixes are parsed.
    """
    windows = []
    for user, messages in sources.items():
        _text(user, "user_id")
        sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen = set()
        for raw in messages:
            message = dict(raw)
            if message.get("user_id", user) != user:
                raise ValueError("source message and declared user disagree")
            ident = _text(message.get("id"), "message.id")
            if ident in seen:
                raise ValueError(f"duplicate message id for user {user}: {ident}")
            seen.add(ident)
            session = message.get("session_id", message.get("session_number"))
            if isinstance(session, bool) or not isinstance(session, (str, int)):
                raise ValueError("message requires session_id or session_number")
            _text(str(session), "session_id")
            turn = message.get("turn")
            if isinstance(turn, bool) or not isinstance(turn, int) or turn < 0:
                raise ValueError("message.turn must be a nonnegative integer")
            _text(message.get("role"), "message.role")
            text = _text(message.get("text"), "message.text")
            timestamp = _text(message.get("created_at"), "message.created_at")
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            match = _SPEAKER.match(text)
            message["speaker"] = _text(
                message.get("speaker") or (match[1] if match else "unresolved_speaker"),
                "message.speaker",
            )
            sessions[str(session)].append(message)
        for session, ordered in sorted(sessions.items()):
            ordered.sort(key=lambda m: m["turn"])
            if len({m["turn"] for m in ordered}) != len(ordered):
                raise ValueError(f"duplicate turn in user/session {user}/{session}")
            for j, focal in enumerate(ordered):
                keys = ordered[max(0, j - 1) : j + 2]
                context = ordered[max(0, j - 2) : j + 2]
                embedding_lines = []
                for message in keys:
                    # Only URL metadata is removed from the key, never from the value.
                    text = "\n".join(
                        line
                        for line in message["text"].split("\n")
                        if not line.startswith("[Image source]")
                    )
                    embedding_lines.append(
                        f"[{message['created_at'][:10]}][{message['speaker']}] {text}"
                    )
                windows.append(
                    {
                        "id": f"source-exchange:{user}:{focal['id']}",
                        "user_id": user,
                        "type": "source_exchange",
                        "session_id": session,
                        "focal_id": focal["id"],
                        "key_message_ids": [m["id"] for m in keys],
                        "embedding_text": "\n".join(embedding_lines),
                        "messages": context,
                    }
                )
    return windows


def _dense(data: dict[str, Any], dimension: int) -> np.ndarray:
    vector = np.asarray(data["embedding"], dtype=np.float64)
    if vector.ndim != 1 or len(vector) < dimension:
        raise ValueError("embedding has the wrong dimension")
    vector = vector[:dimension]
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or not math.isfinite(norm) or norm == 0:
        raise ValueError("embedding must be finite and nonzero")
    return vector / norm


def _sparse(data: dict[str, Any]) -> dict[str, float]:
    result = {}
    for item in data.get("sparse_embedding") or []:
        index, value = str(item["index"]), float(item["value"])
        if index in result or not math.isfinite(value):
            raise ValueError("sparse embedding has duplicate indices or nonfinite values")
        result[index] = value
    return result


class DialogueIndex:
    """Exact, user-scoped dense/sparse retrieval over frozen window vectors."""

    def __init__(
        self,
        windows: Sequence[dict[str, Any]],
        embeddings: Mapping[str, dict[str, Any]],
        *,
        dimension: int = 2048,
    ) -> None:
        if dimension < 1:
            raise ValueError("dimension must be positive")
        self.dimension = dimension
        self.users: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen = set()
        for window in windows:
            if window["id"] in seen:
                raise ValueError(f"duplicate window id: {window['id']}")
            seen.add(window["id"])
            self.users[window["user_id"]].append(window)
        self.dense = {}
        self.sparse = {}
        for user, cards in self.users.items():
            self.dense[user] = np.stack([_dense(embeddings[w["id"]], dimension) for w in cards])
            self.sparse[user] = [_sparse(embeddings[w["id"]]) for w in cards]

    def rank(
        self,
        user_id: str,
        query_vector: dict[str, Any],
        *,
        candidate_limit: int = 80,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        if candidate_limit < 1 or rrf_k < 0:
            raise ValueError("candidate_limit must be positive and rrf_k nonnegative")
        cards = self.users.get(user_id, [])
        if not cards:
            return []
        dense = self.dense[user_id] @ _dense(query_vector, self.dimension)
        query_sparse = _sparse(query_vector)
        sparse = [
            sum(value * query_sparse.get(key, 0) for key, value in vector.items())
            for vector in self.sparse[user_id]
        ]
        dense_order = sorted(range(len(cards)), key=lambda j: (-float(dense[j]), cards[j]["id"]))
        sparse_order = sorted(
            (j for j in range(len(cards)) if sparse[j] > 0),
            key=lambda j: (-sparse[j], cards[j]["id"]),
        )
        scores: dict[int, float] = defaultdict(float)
        for order in (dense_order[:candidate_limit], sparse_order[:candidate_limit]):
            for rank, j in enumerate(order, start=1):
                scores[j] += 1 / (rrf_k + rank)
        order = sorted(scores, key=lambda j: (-scores[j], cards[j]["id"]))
        return [
            {
                **cards[j],
                "dense_score": float(dense[j]),
                "sparse_score": sparse[j],
                "rrf": scores[j],
            }
            for j in order
        ]


def select_windows(
    ranked: Sequence[dict[str, Any]],
    existing_contexts: Sequence[str],
    count_tokens: Callable[[str], int],
    *,
    max_windows: int = 5,
    max_extra_tokens: int = 2000,
) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate source messages, then append whole windows within the budget.

    This is literal/message-id deduplication, not semantic fact deduplication.
    A large window is skipped rather than truncated. Overlap removal can leave
    fewer than four messages in a returned window.
    """
    if max_windows < 0 or max_extra_tokens < 0:
        raise ValueError("window and token budgets must be nonnegative")
    if not max_windows or not max_extra_tokens:
        return [], 0
    joined = "\n".join(existing_contexts)
    seen = set()
    picked = []
    cost = 0
    for window in ranked:
        messages = [m for m in window["messages"] if m["id"] not in seen]
        if not messages or all(m["text"] in joined for m in messages):
            continue
        content = SOURCE_HEADER + "\n".join(
            f"[{m['created_at']}][{m['speaker']}][protocol role {m['role']}] {m['text']}"
            for m in messages
        )
        tokens = count_tokens(content) + 2  # Same per-context overhead as the frozen experiment.
        if cost + tokens > max_extra_tokens:
            continue
        picked.append(
            {
                key: value
                for key, value in window.items()
                if key not in {"messages", "embedding_text", "key_message_ids"}
            }
            | {"content": content, "source_message_ids": [m["id"] for m in messages]}
        )
        seen.update(m["id"] for m in messages)
        cost += tokens
        if len(picked) == max_windows:
            break
    return picked, cost


def augment_row(
    row: dict[str, Any],
    index: DialogueIndex,
    query_vector: dict[str, Any],
    count_tokens: Callable[[str], int],
    *,
    max_windows: int = 5,
    max_extra_tokens: int = 2000,
) -> dict[str, Any]:
    """Preserve the AML evaluation row, replacing only its retrieval and audit fields."""
    user = _text(row.get("user_id"), "user_id")
    contexts, hits = row.get("retrieved_context"), row.get("memory_hits")
    if not isinstance(contexts, list) or any(not isinstance(c, str) for c in contexts):
        raise ValueError("retrieved_context must be an array of memory bodies")
    if not isinstance(hits, list) or len(hits) != len(contexts):
        raise ValueError("memory_hits must identify the owner of every existing context")
    for hit in hits:
        if hit.get("user_id") != user:
            raise ValueError("existing memory belongs to a different user")
        uri = _text(hit.get("id"), "memory hit id")
        if uri.startswith("viking://user/") and not uri.startswith(f"viking://user/{user}/"):
            raise ValueError("memory URI and declared owner disagree")
    if "source_dialogue_retrieval" in row:
        raise ValueError("row was already augmented; use the original memory results")
    ranked = index.rank(user, query_vector)
    picked, tokens = select_windows(
        ranked, contexts, count_tokens, max_windows=max_windows, max_extra_tokens=max_extra_tokens
    )
    return {
        **row,
        "retrieved_context": contexts + [w["content"] for w in picked],
        "memory_hits": hits + [{k: v for k, v in w.items() if k != "content"} for w in picked],
        "source_dialogue_retrieval": {"windows": picked, "additional_tokens": tokens},
    }


def read_cached_embedding(cache: Path, model: str, text: str) -> dict[str, Any]:
    """Read an exact-request cache; never send a request or accept a missing entry."""
    request = {
        "model": model,
        "input": [{"type": "text", "text": text}],
        "sparse_embedding": {"type": "enabled"},
    }
    digest = hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    path = cache / digest[:2] / f"{digest}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["request"] != request or record.get("http", record.get("http_status", 200)) != 200:
        raise ValueError(f"embedding cache does not match the requested text/model: {path}")
    return record["response"]["data"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--embedding-cache", type=Path, required=True)
    parser.add_argument("--query-embedding-cache", type=Path, required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--dimension", type=int, default=2048)
    parser.add_argument("--max-windows", type=int, default=5)
    parser.add_argument("--max-extra-tokens", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; frozen retrieval results must not be overwritten")

    import tiktoken

    tokenizer = tiktoken.get_encoding("o200k_base")
    windows = build_windows(json.loads(args.sources.read_text(encoding="utf-8")))
    vectors = {
        w["id"]: read_cached_embedding(
            args.embedding_cache, args.embedding_model, w["embedding_text"]
        )
        for w in windows
    }
    index = DialogueIndex(windows, vectors, dimension=args.dimension)
    rows = []
    ids = set()
    # splitlines() would also split literal U+2028 inside a valid JSONL string.
    for line in args.retrieval.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        ident = _text(row.get("id"), "row.id")
        if ident in ids:
            raise ValueError(f"duplicate query id: {ident}")
        ids.add(ident)
        question = _text(row.get("question"), "row.question")
        vector = read_cached_embedding(args.query_embedding_cache, args.embedding_model, question)
        rows.append(
            augment_row(
                row,
                index,
                vector,
                lambda text: len(tokenizer.encode(text, disallowed_special=())),
                max_windows=args.max_windows,
                max_extra_tokens=args.max_extra_tokens,
            )
        )
    if not rows:
        raise ValueError("retrieval contains no questions")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"windows": len(windows), "queries": len(rows), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
