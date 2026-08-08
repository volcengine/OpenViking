#!/usr/bin/env python3
"""Minimal Chroma + OpenViking integration example."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "sample_docs"


def load_documents() -> list[dict[str, str]]:
    docs = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        docs.append(
            {
                "id": path.stem,
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            }
        )
    return docs


def build_client(mode: str, workspace: str | None, url: str | None):
    import openviking as ov

    if mode == "http":
        client = ov.SyncHTTPClient(url=url or "http://localhost:1933")
    else:
        client = ov.OpenViking(path=workspace or "./data/chroma-ov-demo")
    client.initialize()
    return client


def build_chroma_collection(collection_name: str = "openviking-static-demo"):
    try:
        import chromadb
    except ImportError as exc:  # pragma: no cover - runtime dependency path
        raise RuntimeError(
            "chromadb is not installed. Run: pip install -r examples/chroma-openviking/requirements.txt"
        ) from exc

    client = chromadb.Client()
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(collection_name)
    docs = load_documents()
    collection.add(
        ids=[doc["id"] for doc in docs],
        documents=[doc["content"] for doc in docs],
        metadatas=[{"path": doc["path"]} for doc in docs],
    )
    return collection


def seed_openviking_session(client) -> str:
    session_info = client.create_session()
    session_id = session_info["session_id"]
    client.add_message(
        session_id,
        "user",
        "Our team prefers HTTP server mode for shared deployments, and we had one retrieval incident last week.",
    )
    client.add_message(
        session_id,
        "assistant",
        "Noted. I will remember the preference for HTTP mode and the recent retrieval incident context.",
    )
    return session_id


def run_short_context_demo(client, collection, session_id: str) -> dict:
    query = "How should we deploy OpenViking for shared multi-session workloads?"
    chroma_result = collection.query(query_texts=[query], n_results=2)
    ov_result = client.search(
        query=query,
        target_uri="viking://resources",
        session_id=session_id,
        limit=3,
    )
    return {
        "query": query,
        "chroma_documents": chroma_result.get("documents", [[]])[0],
        "chroma_metadatas": chroma_result.get("metadatas", [[]])[0],
        "openviking_search": serialize_resources(getattr(ov_result, "resources", [])),
    }


def run_long_memory_demo(client, session_id: str) -> dict:
    client.add_message(
        session_id,
        "user",
        "Please remember that archive memories should only contain durable facts after explicit commit.",
    )
    client.add_message(
        session_id,
        "assistant",
        "Understood. Durable facts should survive into long-term memory only after explicit commit.",
    )
    commit_result = client.commit_session(session_id)
    memory_result = client.find(
        query="durable facts after explicit commit",
        target_uri="viking://user/memories/",
        limit=5,
    )
    return {
        "commit_result": commit_result,
        "memory_hits": serialize_resources(getattr(memory_result, "resources", [])),
    }


def serialize_resources(resources) -> list[dict]:
    serialized = []
    for item in resources or []:
        serialized.append(
            {
                "uri": getattr(item, "uri", ""),
                "score": getattr(item, "score", None),
                "abstract": getattr(item, "abstract", "") or "",
                "overview": getattr(item, "overview", "") or "",
            }
        )
    return serialized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["embedded", "http"], default="embedded")
    parser.add_argument("--workspace", help="Embedded-mode workspace path")
    parser.add_argument("--url", help="HTTP server base URL")
    parser.add_argument("--flow", choices=["all", "short", "long"], default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = build_client(args.mode, args.workspace, args.url)
        collection = build_chroma_collection()
        session_id = seed_openviking_session(client)

        result = {"session_id": session_id}
        if args.flow in {"all", "short"}:
            result["short_context"] = run_short_context_demo(client, collection, session_id)
        if args.flow in {"all", "long"}:
            result["long_memory"] = run_long_memory_demo(client, session_id)

        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
