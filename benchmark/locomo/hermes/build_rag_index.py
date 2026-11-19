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


def raw_chunks(conversation: dict) -> list[dict]:
    chunks = []
    for session_key in _session_keys(conversation):
        messages = conversation[session_key]
        header = _session_header_lines(
            conversation, session_key, conversation.get(f"{session_key}_date_time", "")
        )
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
    args = parser.parse_args()

    source = args.input.read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    model = embedding_model()
    path = args.index.resolve()
    if path.exists() and not args.force:
        index = read_index(path)
        if index["dataset_sha256"] != source_hash or index["embedding_model"] != model:
            parser.error("Existing index uses a different dataset or model. Choose a new index path.")
        print(f"Reusing LoCoMo RAG index at {path}")
        return

    samples = {}
    for item in json.loads(source):
        sample_id = item["sample_id"]
        chunks = raw_chunks(item["conversation"])
        if not chunks or sample_id in samples:
            parser.error(f"Empty history or duplicate sample ID: {sample_id}")
        samples[sample_id] = {"scope": secrets.token_hex(8), "chunks": chunks}
    if not samples:
        parser.error("LoCoMo dataset has no samples")

    chunks = [chunk for sample in samples.values() for chunk in sample["chunks"]]
    dimensions = set()
    usage = []
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        vectors, tokens = embed([chunk["text"] for chunk in batch])
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
