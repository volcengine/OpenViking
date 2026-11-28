"""Embed raw LoCoMo transcripts for the Hermes agentic RAG suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

from import_to_native import _message_lines, _session_header_lines, _session_keys
from locomo_rag import INDEX_VERSION, embed, embedding_model, read_index

WINDOW_SIZE = 4
STRIDE = 3
BATCH_SIZE = 32
CHAR_OVERLAP = 64


def raw_chunks(conversation: dict, chunk_chars: int | None = None) -> list[dict]:
    chunks = []
    for session_key in _session_keys(conversation):
        messages = conversation[session_key]
        header = _session_header_lines(
            conversation, session_key, conversation.get(f"{session_key}_date_time", "")
        )
        if chunk_chars is not None:
            prefix = "\n".join(header) + "\n"
            transcript = "\n".join(line for message in messages for line in _message_lines(message))
            body_chars = chunk_chars - len(prefix)
            if body_chars <= 0:
                raise ValueError(f"--chunk-chars leaves no room for transcript in {session_key}")
            overlap = min(CHAR_OVERLAP, body_chars // 4)
            for start in range(0, len(transcript), body_chars - overlap):
                end = min(start + body_chars, len(transcript))
                chunks.append(
                    {
                        "id": f"{session_key}:chars:{start + 1}-{end}",
                        "text": prefix + transcript[start:end],
                    }
                )
                if end == len(transcript):
                    break
            continue

        for start in range(0, len(messages), STRIDE):
            end = min(start + WINDOW_SIZE, len(messages))
            lines = [*header]
            for message in messages[start:end]:
                lines.extend(_message_lines(message))
            chunks.append({"id": f"{session_key}:{start + 1}-{end}", "text": "\n".join(lines)})
            if end == len(messages):
                break
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Path to LoCoMo JSON")
    parser.add_argument("--index", type=Path, required=True, help="Path shared with the Hermes plugin")
    parser.add_argument("--force", action="store_true", help="Replace the existing index")
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=os.getenv("LOCOMO_RAG_CHUNK_CHARS") or None,
        help="Maximum Unicode characters per chunk, including session metadata",
    )
    parser.add_argument(
        "--embedding-workers",
        type=int,
        default=os.getenv("LOCOMO_RAG_EMBEDDING_WORKERS") or 4,
        help="Concurrent ModelArk embedding requests during indexing",
    )
    args = parser.parse_args()
    if args.chunk_chars is not None and args.chunk_chars <= 0:
        parser.error("--chunk-chars must be positive")
    if args.embedding_workers <= 0:
        parser.error("--embedding-workers must be positive")

    source = args.input.read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    model = embedding_model()
    path = args.index.resolve()
    if path.exists() and not args.force:
        index = read_index(path)
        if (
            index["dataset_sha256"] != source_hash
            or index["embedding_model"] != model
            or index.get("chunk_chars") != args.chunk_chars
        ):
            parser.error("Existing index uses a different dataset, model, or chunk size.")
        print(f"Reusing LoCoMo RAG index at {path}")
        return

    samples = {}
    for item in json.loads(source):
        sample_id = item["sample_id"]
        chunks = raw_chunks(item["conversation"], args.chunk_chars)
        if not chunks or sample_id in samples:
            parser.error(f"Empty history or duplicate sample ID: {sample_id}")
        samples[sample_id] = {"scope": secrets.token_hex(8), "chunks": chunks}
    if not samples:
        parser.error("LoCoMo dataset has no samples")

    chunks = [chunk for sample in samples.values() for chunk in sample["chunks"]]
    dimensions = set()
    usage = []
    batch_size = BATCH_SIZE
    if os.environ["LOCOMO_RAG_EMBEDDING_URL"].rstrip("/").endswith("/embeddings/multimodal"):
        batch_size = max(batch_size, args.embedding_workers * 4)
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors, tokens = embed([chunk["text"] for chunk in batch], workers=args.embedding_workers)
        usage.append(tokens)
        for chunk, vector in zip(batch, vectors, strict=True):
            dimensions.add(len(vector))
            chunk["vector"] = vector
    if len(dimensions) != 1:
        raise ValueError("Embedding endpoint returned inconsistent dimensions")

    index = {
        "version": INDEX_VERSION,
        "dataset_sha256": source_hash,
        "embedding_model": model,
        "chunk_chars": args.chunk_chars,
        "embedding_tokens": sum(usage) if all(tokens is not None for tokens in usage) else None,
        "samples": samples,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        temporary_path = Path(output.name)
        try:
            json.dump(index, output, ensure_ascii=False, separators=(",", ":"))
            output.close()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    print(f"Indexed {len(chunks)} raw history chunks for {len(samples)} samples at {path}")
    print(f"Index embedding tokens: {index['embedding_tokens']}")


if __name__ == "__main__":
    main()
