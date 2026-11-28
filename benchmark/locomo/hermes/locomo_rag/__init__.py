"""Raw-history vector search tool for the Hermes LoCoMo benchmark."""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen

TOOL_NAME = "search_locomo_history"
INDEX_VERSION = 1
TOP_K = 5


def embedding_model() -> str:
    return os.environ["LOCOMO_RAG_EMBEDDING_MODEL"]


def _embedding_request(url: str, inputs: list, **options) -> dict:
    headers = {"Content-Type": "application/json"}
    if key := os.getenv("LOCOMO_RAG_EMBEDDING_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    payload = json.dumps({"model": embedding_model(), "input": inputs, **options}).encode()
    request = Request(url, data=payload, headers=headers)
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def _embedding_tokens(body: dict) -> int | None:
    usage = body.get("usage") or {}
    return usage.get("prompt_tokens", usage.get("total_tokens"))


def embed(texts: list[str], *, workers: int = 4) -> tuple[list[list[float]], int | None]:
    url = os.environ["LOCOMO_RAG_EMBEDDING_URL"].rstrip("/")
    if url.endswith("/embeddings/multimodal"):
        # ModelArk's multimodal API returns one vector per request.
        def embed_text(text: str) -> dict:
            return _embedding_request(url, [{"type": "text", "text": text}], dimensions=1024)

        if len(texts) == 1:
            responses = [embed_text(texts[0])]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                responses = list(pool.map(embed_text, texts))
        embeddings = [body["data"]["embedding"] for body in responses]
        counts = [_embedding_tokens(body) for body in responses]
        tokens = sum(counts) if all(count is not None for count in counts) else None
    else:
        body = _embedding_request(f"{url}/embeddings", texts)
        items = sorted(body["data"], key=lambda item: item["index"])
        if [item["index"] for item in items] != list(range(len(texts))):
            raise ValueError("Embedding endpoint returned an unexpected batch")
        embeddings = [item["embedding"] for item in items]
        tokens = _embedding_tokens(body)

    vectors = []
    for vector in embeddings:
        norm = math.hypot(*vector)
        if not norm or not math.isfinite(norm):
            raise ValueError("Embedding endpoint returned an invalid vector")
        vectors.append([value / norm for value in vector])
    return vectors, tokens


@lru_cache(maxsize=1)
def _read_index(path: Path, revision: tuple[int, int]) -> dict:
    index = json.loads(path.read_text(encoding="utf-8"))
    if index.get("version") != INDEX_VERSION:
        raise ValueError("Unsupported LoCoMo RAG index version")
    return index


def read_index(path: Path) -> dict:
    stat = path.stat()
    return _read_index(path.resolve(), (stat.st_ino, stat.st_mtime_ns))


def register(ctx) -> None:
    def search(params: dict, **_kwargs) -> str:
        scope = params.get("scope")
        query = params.get("query")
        if not isinstance(scope, str) or not isinstance(query, str) or not query.strip():
            return json.dumps({"error": "A non-empty scope and query are required"})

        index = read_index(Path(os.environ["LOCOMO_RAG_INDEX"]))
        sample = next(
            (sample for sample in index["samples"].values() if sample["scope"] == scope), None
        )
        if sample is None:
            return json.dumps({"error": "Unknown history scope"})
        if index["embedding_model"] != embedding_model():
            return json.dumps({"error": "Gateway embedding model does not match the index"})

        vectors, tokens = embed([query.strip()])
        query_vector = vectors[0]
        # Euclidean distance over unit vectors yields cosine-similarity ranking.
        ranked = sorted(sample["chunks"], key=lambda chunk: math.dist(query_vector, chunk["vector"]))
        return json.dumps(
            {
                "scope": scope,
                "embedding_tokens": tokens,
                "hits": [{"id": chunk["id"], "text": chunk["text"]} for chunk in ranked[:TOP_K]],
            },
            ensure_ascii=False,
        )

    ctx.register_tool(
        name=TOOL_NAME,
        toolset="locomo-rag",
        schema={
            "name": TOOL_NAME,
            "description": "Search the assigned LoCoMo history for verbatim conversation passages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "History scope from the instructions"},
                    "query": {"type": "string", "description": "What to look for in prior conversations"},
                },
                "required": ["scope", "query"],
                "additionalProperties": False,
            },
        },
        handler=search,
    )
