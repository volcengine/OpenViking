"""Prepare PersonaMem-v2 text benchmark histories without system persona profiles.

Each size follows its exact benchmark CSV link. Raw persona records and gold
conversation snippets are never used to assemble the Add messages.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / "result/aml_data"
POLICY = "text-chat-only-drop-system-v1"
SIZES = ("32k", "128k")
BENCHMARK = "benchmark/text/benchmark.csv"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_file(root: Path, relative: str, expected: dict[str, str]) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ValueError(f"Invalid source path: {relative}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Source escapes dataset directory: {relative}")
    if relative not in expected or sha256(path) != expected[relative]:
        raise ValueError(f"Source differs from verified download: {relative}")
    return path


def literal(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return ast.literal_eval(value)


def chat_messages(chat: Any) -> tuple[list[dict[str, str]], list[int], list[int]]:
    if not isinstance(chat, list) or not chat:
        raise ValueError("Expected a non-empty chat_history array")
    messages = []
    removed = []
    blank = []
    for index, message in enumerate(chat):
        if not isinstance(message, dict):
            raise ValueError(f"Invalid message at source index {index}")
        role = message.get("role")
        if role == "system":
            removed.append(index)
            continue
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ValueError(f"Unsupported text message at source index {index}")
        if not content.strip():
            blank.append(index)
            continue
        # Preserve text, role and array order exactly. The source has no real
        # timestamps; do not manufacture dates or infer session boundaries.
        messages.append({"role": role, "content": content})
    if not messages:
        raise ValueError("No conversation messages remain after dropping system")
    return messages, removed, blank


def build_prepared(
    root: Path,
    rows: list[dict[str, str]],
    size: str,
    revision: str,
    expected: dict[str, str],
) -> dict[str, Any]:
    if size not in SIZES:
        raise ValueError(f"Unsupported text history size: {size}")
    unit = f"personamem_v2_{size}"
    column = f"chat_history_{size}_link"
    benchmark = source_file(root, BENCHMARK, expected)
    histories: dict[tuple[str, str], dict[str, Any]] = {}
    provenance: dict[tuple[str, str], dict[str, Any]] = {}
    cases = []
    for index, row in enumerate(rows):
        persona_id = row["persona_id"]
        relative = row[column]
        if not relative.startswith(f"data/chat_history_{size}/"):
            raise ValueError(f"Row {index}: wrong text history directory for {column}")
        identity = (persona_id, relative)
        if identity not in histories:
            path = source_file(root, relative, expected)
            source = read_json(path)
            metadata = source["metadata"]
            if str(metadata["persona_id"]) != persona_id:
                raise ValueError(f"Row {index}: history persona does not match CSV")
            if metadata.get("input_filename") != row["raw_persona_file"]:
                raise ValueError(f"Row {index}: raw persona reference does not match history")
            messages, removed, blank = chat_messages(source["chat_history"])
            history_key = f"personamem-v2-{persona_id}-{path.stem}"
            histories[identity] = {
                "unit": unit,
                "history_key": history_key,
                "sessions": [{"session_id": f"persona-{persona_id}", "messages": messages}],
            }
            provenance[identity] = {
                "source_path": str(path.resolve()),
                "source_relative_path": relative,
                "source_url": f"https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2/resolve/{revision}/{relative}",
                "revision": revision,
                "sha256": expected[relative],
                "history_link_column": column,
                "raw_persona_file": row["raw_persona_file"],
                "history_scope": f"complete linked public text {size} history excluding system messages",
                "preprocessing_policy": POLICY,
                "dropped_system_message_indices": removed,
                "dropped_blank_message_indices": blank,
                "source_counts": {
                    "sessions": 1,
                    "messages_including_system": len(source["chat_history"]),
                    "retained_messages": len(messages),
                    "source_reported_tokens_including_system": metadata.get("final_token_count"),
                    "source_reported_irrelevant_tokens": metadata.get("num_irrelevant_tokens", 0),
                },
                "transformations": [
                    "drop system and blank messages; preserve remaining role/content/order exactly"
                ],
                "timestamp_policy": "absent in source; preserve sequence without synthetic dates",
                "aml_equivalence": "public-source proxy; private AML retrieval-to-chat assembly unavailable",
            }
        if provenance[identity]["raw_persona_file"] != row["raw_persona_file"]:
            raise ValueError(f"Row {index}: conflicting raw persona reference")
        query = literal(row["user_query"])
        incorrect = literal(row["incorrect_answers"])
        if (
            not isinstance(query, dict)
            or query.get("role") != "user"
            or not isinstance(query.get("content"), str)
            or not query["content"].strip()
        ):
            raise ValueError(f"Row {index}: invalid user_query")
        if not isinstance(incorrect, list) or any(not isinstance(a, str) for a in incorrect):
            raise ValueError(f"Row {index}: invalid incorrect_answers")
        # Evaluation labels stay on cases. They are not part of history.messages.
        extra = {
            key: row[key]
            for key in (
                "preference",
                "sensitive_info",
                "who",
                "updated",
                "prev_pref",
                "topic_query",
                "topic_preference",
                "conversation_scenario",
                "pref_type",
            )
            if key in row
        }
        extra.update(
            persona_id=persona_id,
            user_query=query,
            correct_answer=row["correct_answer"],
            incorrect_answers=incorrect,
            answer_mode="mcq",
        )
        cases.append(
            {
                "unit": unit,
                "case_id": f"personamem-v2-row-{index}-persona-{persona_id}",
                "history_key": histories[identity]["history_key"],
                "question": query["content"],
                "gold_answer": row["correct_answer"],
                "options": [],
                "extra": extra,
                "provenance": {**provenance[identity], "benchmark_row_offset": index},
            }
        )
    dataset = {
        "unit": unit,
        "profile": "full",
        "revision": revision,
        "source": {
            "path": str(benchmark.resolve()),
            "bytes": benchmark.stat().st_size,
            "sha256": expected[BENCHMARK],
        },
        "source_rows": len(rows),
        "eligible_cases": len(cases),
        "selected_cases": len(cases),
        "selection": {"method": "all-text-benchmark-rows-in-source-order"},
        "history_link_column": column,
        "referenced_histories": len(histories),
        "loaded_histories": len(histories),
        "preprocessing_policy": POLICY,
        "answer_mode": "mcq",
        "dropped_system_messages": sum(
            len(p["dropped_system_message_indices"]) for p in provenance.values()
        ),
        "dropped_blank_messages": sum(
            len(p["dropped_blank_message_indices"]) for p in provenance.values()
        ),
        "retained_messages": sum(
            p["source_counts"]["retained_messages"] for p in provenance.values()
        ),
        "timestamp_policy": "absent in source; preserve sequence without synthetic dates",
        "aml_equivalence": "public-source proxy; private AML retrieval-to-chat assembly unavailable",
    }
    return {
        "schema_version": 2,
        "profile": "full",
        "unit": unit,
        "datasets": [dataset],
        "histories": list(histories.values()),
        "cases": cases,
    }


def refresh_smoke(smoke: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    unit = prepared["unit"]
    cases = {case["case_id"]: case for case in prepared["cases"]}
    histories = {h["history_key"]: h["sessions"] for h in prepared["histories"]}
    updated = []
    for case in smoke["cases"]:
        if case["unit"] != unit:
            updated.append(case)
            continue
        replacement = cases[case["case_id"]]
        updated.append({**replacement, "histories": histories[replacement["history_key"]]})
    metadata = {
        **prepared["datasets"][0],
        "profile": "smoke",
        "selected_cases": sum(c["unit"] == unit for c in updated),
    }
    return {
        **smoke,
        "cases": updated,
        "datasets": [metadata if d.get("unit") == unit else d for d in smoke["datasets"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DATA_ROOT / "datasets/personamem_v2")
    parser.add_argument(
        "--verification", type=Path, default=DATA_ROOT / "personamem_v2_download/verification.json"
    )
    parser.add_argument("--output-dir", type=Path, default=DATA_ROOT / "prepared/full")
    parser.add_argument(
        "--smoke-file",
        type=Path,
        help="Also refresh existing 32K smoke cases, preserving other units",
    )
    args = parser.parse_args()
    root = args.source_dir.resolve()
    verification = read_json(args.verification)
    if not verification["success"]:
        raise ValueError("Source download verification did not succeed")
    expected = {entry["path"]: entry["sha256"] for entry in verification["files"]}
    benchmark = source_file(root, BENCHMARK, expected)
    with benchmark.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Text benchmark is empty")
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stage = destination.parent / f".personamem-text-{stamp}"
    stage.mkdir()
    outputs = []
    artifacts = []
    for size in SIZES:
        payload = build_prepared(root, rows, size, verification["revision"], expected)
        name = f"personamem_v2_{size}.json"
        target = destination / name
        staged = stage / name
        dump_json(staged, payload)
        outputs.append((staged, target))
        artifacts.append(
            {
                "unit": payload["unit"],
                "profile": "full",
                "cases": len(payload["cases"]),
                "histories": len(payload["histories"]),
                "bytes": staged.stat().st_size,
                "sha256": sha256(staged),
                "path": str(target),
                "dataset": payload["datasets"][0],
            }
        )
        if size == "32k" and args.smoke_file:
            smoke = refresh_smoke(read_json(args.smoke_file), payload)
            staged_smoke = stage / "persona_cl.json"
            dump_json(staged_smoke, smoke)
            outputs.append((staged_smoke, args.smoke_file.resolve()))
        print(
            json.dumps(
                {
                    "size": size,
                    "cases": len(payload["cases"]),
                    **{
                        k: payload["datasets"][0][k]
                        for k in [
                            "loaded_histories",
                            "retained_messages",
                            "dropped_system_messages",
                        ]
                    },
                }
            ),
            flush=True,
        )
        del payload
    manifest_path = destination / "manifest.json"
    manifest = (
        read_json(manifest_path)
        if manifest_path.exists()
        else {"schema_version": 1, "artifacts": []}
    )
    replaced = {a["unit"] for a in artifacts}
    manifest["artifacts"] = [
        a for a in manifest["artifacts"] if a["unit"] not in replaced
    ] + artifacts
    staged_manifest = stage / "manifest.json"
    dump_json(staged_manifest, manifest)
    outputs.append((staged_manifest, manifest_path))
    backup = destination.parent / "backups" / f"personamem-before-chat-only-{stamp}"
    backup.mkdir(parents=True)
    backups = []
    for _, target in outputs:
        if target.exists():
            saved = backup / target.name
            shutil.copy2(target, saved)
            backups.append({"original": str(target), "backup": str(saved), "sha256": sha256(saved)})
    dump_json(backup / "backup-manifest.json", {"files": backups})
    for staged, target in outputs:
        staged.replace(target)
    stage.rmdir()
    report = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "preprocessing_policy": POLICY,
        "revision": verification["revision"],
        "scope": "text benchmark 32K and 128K",
        "backup_directory": str(backup),
        "artifacts": artifacts,
        "normalizer": str(Path(__file__).resolve()),
        "normalizer_sha256": sha256(Path(__file__)),
        "ov_import_started": False,
    }
    dump_json(destination / "personamem_text_preparation.json", report)
    print(
        json.dumps({"prepared": [a["path"] for a in artifacts], "backup": str(backup)}), flush=True
    )


if __name__ == "__main__":
    main()
