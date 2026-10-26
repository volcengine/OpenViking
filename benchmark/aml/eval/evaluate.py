"""Optionally run the public AML Answer/Eval pipeline for one retrieval file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_AML_COMMIT = "1b8142bfe0f20f1c5218d6b554aa0012de34e504"
DEFAULT_ANSWER_CONCURRENCY = 32
DEFAULT_EVAL_CONCURRENCY = 32

_MIXED_OUTPUT_CONTEXT = re.compile(
    r"^(\s*)async with (httpx\.AsyncClient\(.*\) as client), "
    r"(.+\.open\(.*\) as \w+):\s*$"
)

# kind controls only the retrieval-to-pipeline input adapter. Answer prompts and
# scoring remain owned by the corresponding public AML pipeline.
PIPELINES = {
    "longmemeval_s": ("data/longmemeval-s/pipeline.py", "generic"),
    "locomo_refined": ("data/locomo-refined/pipeline.py", "generic"),
    "beam_100k": ("data/beam/pipeline.py", "generic"),
    "beam_1m": ("data/beam/pipeline.py", "generic"),
    "clbench_0_4k": ("data/clbench/pipeline.py", "clbench"),
    "clbench_16_32k": ("data/clbench/pipeline.py", "clbench"),
    "personamem_v2_32k": ("data/personamem/pipeline_v2.py", "personamem_v2"),
    "personamem_v2_128k": ("data/personamem/pipeline_v2.py", "personamem_v2"),
}


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot inspect AML checkout: {exc}") from exc
    return result.stdout.strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected an object")
        raw_id = row.get("id", row.get("idx"))
        if raw_id is None:
            raise ValueError(f"{path}:{line_number}: expected id or idx")
        ident = str(raw_id)
        if ident in seen:
            raise ValueError(f"{path}:{line_number}: duplicate id {ident!r}")
        seen.add(ident)
        rows.append(row)
    if not rows:
        raise ValueError(f"JSONL input is empty: {path}")
    return rows


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _row_id(row: dict[str, Any]) -> str:
    raw_id = row.get("id", row.get("idx"))
    if raw_id is None:
        raise ValueError("pipeline row has no id or idx")
    return str(raw_id)


def _pipeline_row_id(row: dict[str, Any], kind: str) -> str:
    if kind != "clbench":
        return _row_id(row)
    for key in ("idx", "id", "question_id", "task_id"):
        if row.get(key) is not None:
            return str(row[key])
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in ("task_id", "id"):
            if metadata.get(key) is not None:
                return str(metadata[key])
    raise ValueError("CLBench pipeline row has no supported identifier")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _resolve_unit(rows: Sequence[dict[str, Any]], requested: str | None) -> str:
    declared = {str(row["unit"]) for row in rows if row.get("unit") is not None}
    if requested is not None:
        if declared and declared != {requested}:
            raise ValueError(
                f"--unit {requested!r} does not match retrieval units: {sorted(declared)}"
            )
        unit = requested
    elif len(declared) == 1:
        unit = declared.pop()
    else:
        raise ValueError("retrieval must contain exactly one unit, or pass --unit")
    if unit not in PIPELINES:
        raise ValueError(f"no public Answer/Eval pipeline configured for unit {unit!r}")
    return unit


def _contexts(row: dict[str, Any]) -> list[str]:
    value = row.get("retrieved_context")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"record {row.get('id')!r} has invalid retrieved_context")
    return value


def _adapt_rows(
    rows: Sequence[dict[str, Any]], kind: str, *, persona_mode: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    if persona_mode is not None:
        if kind != "personamem_v2":
            raise ValueError("--persona-mode is only supported for PersonaMem v2")
        if persona_mode not in {"generative", "mcq"}:
            raise ValueError(f"unsupported PersonaMem mode: {persona_mode!r}")
    if kind == "generic":
        for row in rows:
            _contexts(row)
        return [dict(row) for row in rows], None

    adapted = []
    modes = set()
    for row in rows:
        item = dict(row)
        contexts = _contexts(row)
        if kind == "clbench":
            item["retrieval"] = {"selected": [{"text": content} for content in contexts]}
        elif kind == "personamem_v2":
            # Apply the default to older retrieval files as well; generation
            # requires an explicit mode choice.
            mode = persona_mode or "mcq"
            item["answer_mode"] = mode
            if mode == "mcq":
                correct = item.get("correct_answer")
                incorrect = item.get("incorrect_answers")
                if isinstance(incorrect, str):
                    try:
                        incorrect = json.loads(incorrect)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"PersonaMem row {_row_id(item)!r}: invalid MCQ options"
                        ) from exc
                if (
                    not isinstance(correct, str)
                    or not correct.strip()
                    or not isinstance(incorrect, list)
                    or len(incorrect) != 3
                    or any(
                        not isinstance(option, str) or not option.strip() for option in incorrect
                    )
                    or len({option.strip() for option in [correct, *incorrect]}) != 4
                ):
                    raise ValueError(
                        f"PersonaMem row {_row_id(item)!r}: MCQ requires a correct answer "
                        "and three distinct incorrect answers"
                    )
                item["incorrect_answers"] = list(incorrect)
            memory_text = "\n\n".join(
                f"[Retrieved memory {index}]\n{content}"
                for index, content in enumerate(contexts, start=1)
            )
            item["chat_history"] = [
                {
                    "role": "system",
                    "content": memory_text or "No relevant memories were retrieved.",
                }
            ]
            modes.add(mode)
        else:
            raise AssertionError(f"unknown pipeline adapter: {kind}")
        adapted.append(item)

    if kind == "personamem_v2":
        if not modes <= {"generative", "mcq"} or len(modes) != 1:
            raise ValueError(f"PersonaMem retrieval has unsupported mixed modes: {sorted(modes)}")
        return adapted, modes.pop()
    return adapted, None


def _verify_pipeline(
    repo: Path,
    relative_path: str,
    expected_commit: str,
) -> tuple[Path, dict[str, str]]:
    repo = repo.expanduser().resolve()
    pipeline = repo / relative_path
    if not pipeline.is_file():
        raise ValueError(f"AML pipeline not found: {pipeline}")
    actual_commit = _git(repo, "rev-parse", "HEAD")
    if actual_commit != expected_commit:
        raise ValueError(
            f"AML checkout commit mismatch: expected {expected_commit}, got {actual_commit}"
        )
    if _git(repo, "status", "--short", "--", relative_path):
        raise ValueError(f"AML pipeline has local modifications: {pipeline}")
    return pipeline, {
        "commit": actual_commit,
        "path": relative_path,
        "sha256": hashlib.sha256(pipeline.read_bytes()).hexdigest(),
    }


def _nest_synchronous_output_contexts(source: str) -> tuple[str, int]:
    """Make the pinned pipelines' regular output files valid inside async functions."""
    lines = source.splitlines()
    result = []
    replacements = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _MIXED_OUTPUT_CONTEXT.match(line)
        if match is None:
            result.append(line)
            index += 1
            continue

        indent, async_manager, sync_manager = match.groups()
        result.append(f"{indent}async with {async_manager}:")
        result.append(f"{indent}    with {sync_manager}:")
        replacements += 1
        index += 1
        while index < len(lines):
            body_line = lines[index]
            stripped = body_line.lstrip()
            if stripped and len(body_line) - len(stripped) <= len(indent):
                break
            result.append(f"    {body_line}" if stripped else body_line)
            index += 1

    return "\n".join(result) + "\n", replacements


def _runtime_pipeline(repo: Path, pipeline: Path, temporary: Path) -> Path:
    source = pipeline.read_text(encoding="utf-8")
    patched_source, replacements = _nest_synchronous_output_contexts(source)
    if replacements == 0:
        return pipeline
    try:
        compile(patched_source, str(pipeline), "exec")
    except SyntaxError as exc:
        raise RuntimeError(f"cannot apply AML pipeline output compatibility fix: {exc}") from exc

    runtime_root = temporary / "pipeline-runtime"
    runtime_pipeline = runtime_root / pipeline.relative_to(repo)
    runtime_pipeline.parent.mkdir(parents=True)
    runtime_pipeline.write_text(patched_source, encoding="utf-8")
    api_config = repo / "api_config.py"
    if not api_config.is_file():
        raise ValueError(f"AML API config not found: {api_config}")
    (runtime_root / "api_config.py").write_bytes(api_config.read_bytes())
    return runtime_pipeline


def _require_model_environment(*, needs_judge: bool) -> None:
    names = ["ANSWER_API_BASE", "ANSWER_API_KEY", "ANSWER_MODEL"]
    if needs_judge:
        names.extend(["JUDGE_API_BASE", "JUDGE_API_KEY", "JUDGE_MODEL"])
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError(f"missing model environment: {', '.join(missing)}")


def _run(repo: Path, arguments: Sequence[str]) -> None:
    environment = os.environ.copy()
    environment.setdefault("PYTHONHASHSEED", "0")
    try:
        result = subprocess.run(
            [sys.executable, *arguments],
            cwd=repo,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot start AML pipeline: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2_000:]
        raise RuntimeError(f"AML pipeline failed with exit {result.returncode}: {detail}")


def _partition_rows(rows: Sequence[dict[str, Any]], concurrency: int) -> list[list[dict[str, Any]]]:
    shard_count = min(len(rows), concurrency)
    if shard_count == 0:
        return []
    return [list(rows[index::shard_count]) for index in range(shard_count)]


def _run_parallel(
    repo: Path,
    stage: str,
    commands: Sequence[Sequence[str]],
    concurrency: int,
) -> None:
    if not commands:
        return
    with ThreadPoolExecutor(max_workers=min(concurrency, len(commands))) as executor:
        futures = {
            executor.submit(_run, repo, command): index for index, command in enumerate(commands)
        }
        for future in as_completed(futures):
            try:
                future.result()
            except RuntimeError as exc:
                for pending in futures:
                    pending.cancel()
                index = futures[future]
                raise RuntimeError(
                    f"AML {stage} shard {index + 1}/{len(commands)} failed: {exc}"
                ) from exc


def _merge_outputs(
    paths: Sequence[Path],
    expected_rows: Sequence[dict[str, Any]],
    stage: str,
    identify: Callable[[dict[str, Any]], str] = _row_id,
) -> list[dict[str, Any]]:
    expected_ids = [identify(row) for row in expected_rows]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError(f"AML {stage} input contains duplicate pipeline identifiers")
    merged: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            rows = _read_jsonl(path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"AML {stage} produced invalid output {path}: {exc}") from exc
        for row in rows:
            ident = identify(row)
            if ident in merged:
                raise RuntimeError(f"AML {stage} produced duplicate id {ident!r}")
            merged[ident] = row

    expected = set(expected_ids)
    actual = set(merged)
    if expected != actual:
        missing = sorted(expected - actual)[:10]
        unexpected = sorted(actual - expected)[:10]
        raise RuntimeError(
            f"AML {stage} output ID mismatch: missing={missing}, unexpected={unexpected}"
        )
    return [merged[ident] for ident in expected_ids]


ShardCommand = Callable[[Path, Path, Sequence[dict[str, Any]]], Sequence[str]]


def _run_sharded_stage(
    repo: Path,
    rows: Sequence[dict[str, Any]],
    destination: Path,
    temporary: Path,
    *,
    stage: str,
    concurrency: int,
    command: ShardCommand,
    identify: Callable[[dict[str, Any]], str] = _row_id,
) -> list[dict[str, Any]]:
    identifiers = [identify(row) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"AML {stage} input contains duplicate pipeline identifiers")
    stage_dir = temporary / stage
    stage_dir.mkdir()
    commands = []
    outputs = []
    for index, shard_rows in enumerate(_partition_rows(rows, concurrency)):
        shard_dir = stage_dir / f"{index:04d}"
        shard_dir.mkdir()
        input_path = shard_dir / "input.jsonl"
        output_path = shard_dir / "output.jsonl"
        _write_jsonl(input_path, shard_rows)
        commands.append(command(input_path, output_path, shard_rows))
        outputs.append(output_path)

    _run_parallel(repo, stage, commands, concurrency)
    merged = _merge_outputs(outputs, rows, stage, identify)
    _write_jsonl(destination, merged)
    return merged


def evaluate(
    retrieval_path: Path,
    output_dir: Path,
    *,
    aml_repo: Path,
    unit: str | None = None,
    aml_commit: str = DEFAULT_AML_COMMIT,
    answer_concurrency: int = DEFAULT_ANSWER_CONCURRENCY,
    eval_concurrency: int = DEFAULT_EVAL_CONCURRENCY,
    persona_mode: str | None = None,
) -> dict[str, Any]:
    """Dispatch one retrieval file to its public dataset pipeline."""
    retrieval_path = retrieval_path.expanduser().resolve()
    if not retrieval_path.is_file():
        raise ValueError(f"retrieval file does not exist: {retrieval_path}")
    if answer_concurrency < 1:
        raise ValueError("answer_concurrency must be positive")
    if eval_concurrency < 1:
        raise ValueError("eval_concurrency must be positive")
    rows = _read_jsonl(retrieval_path)
    unit = _resolve_unit(rows, unit)
    relative_path, kind = PIPELINES[unit]
    repo = aml_repo.expanduser().resolve()
    pipeline, pipeline_info = _verify_pipeline(repo, relative_path, aml_commit)
    adapted_rows, persona_mode = _adapt_rows(rows, kind, persona_mode=persona_mode)

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    answers_path = output_dir / "answers.jsonl"
    judgments_path = output_dir / "judgments.jsonl"
    summary_path = output_dir / "summary.json"
    existing = [path.name for path in (answers_path, judgments_path, summary_path) if path.exists()]
    if existing:
        raise ValueError(f"output already exists: {', '.join(existing)}")

    needs_judge = not (kind == "personamem_v2" and persona_mode == "mcq")
    _require_model_environment(needs_judge=needs_judge)

    def identify(row: dict[str, Any]) -> str:
        return _pipeline_row_id(row, kind)

    with tempfile.TemporaryDirectory(prefix=".aml-eval-", dir=output_dir) as temporary:
        temporary_path = Path(temporary)
        staged_answers_path = temporary_path / "answers.jsonl"
        staged_judgments_path = temporary_path / "judgments.jsonl"
        runtime_pipeline = _runtime_pipeline(repo, pipeline, temporary_path)

        def answer_command(
            input_path: Path,
            output_path: Path,
            _shard_rows: Sequence[dict[str, Any]],
        ) -> Sequence[str]:
            arguments = [
                str(runtime_pipeline),
                "answer",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
            if kind == "personamem_v2":
                arguments.extend(["--mode", persona_mode or "generative"])
            return arguments

        answer_rows = _run_sharded_stage(
            repo,
            adapted_rows,
            staged_answers_path,
            temporary_path,
            stage="answer",
            concurrency=answer_concurrency,
            command=answer_command,
            identify=identify,
        )
        answers_by_id = {identify(row): row for row in answer_rows}

        def judge_command(
            input_path: Path,
            output_path: Path,
            shard_rows: Sequence[dict[str, Any]],
        ) -> Sequence[str]:
            shard_answers = input_path.with_name("answers.jsonl")
            _write_jsonl(shard_answers, [answers_by_id[identify(row)] for row in shard_rows])
            if kind == "personamem_v2" and persona_mode == "mcq":
                return [
                    str(runtime_pipeline),
                    "evaluate-mcq",
                    "--answers",
                    str(shard_answers),
                    "--output",
                    str(output_path),
                ]
            return [
                str(runtime_pipeline),
                "evaluate-narrow" if kind == "personamem_v2" else "evaluate",
                "--input",
                str(input_path),
                "--answers",
                str(shard_answers),
                "--output",
                str(output_path),
            ]

        judgment_rows = _run_sharded_stage(
            repo,
            adapted_rows,
            staged_judgments_path,
            temporary_path,
            stage="eval",
            concurrency=eval_concurrency,
            command=judge_command,
            identify=identify,
        )
        staged_answers_path.replace(answers_path)
        staged_judgments_path.replace(judgments_path)

    summary = {
        "unit": unit,
        "retrieval": str(retrieval_path),
        "pipeline": pipeline_info,
        "answer_rows": len(answer_rows),
        "evaluation_rows": len(judgment_rows),
        "answers": str(answers_path),
        "judgments": str(judgments_path),
    }
    if kind == "personamem_v2":
        summary["persona_mode"] = persona_mode
        if persona_mode == "mcq":
            correct_count = sum(row["is_correct"] is True for row in judgment_rows)
            summary.update(
                scoring_protocol="personamem-v2-public-mcq",
                correct_count=correct_count,
                incorrect_count=len(judgment_rows) - correct_count,
                invalid_answer_count=sum(row["predicted_answer"] is None for row in judgment_rows),
                accuracy=correct_count / len(judgment_rows),
                shuffle_pythonhashseed=os.environ.get("PYTHONHASHSEED", "0"),
            )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--unit", choices=sorted(PIPELINES))
    parser.add_argument("--aml-repo", required=True, type=Path)
    parser.add_argument("--aml-commit", default=DEFAULT_AML_COMMIT)
    parser.add_argument(
        "--persona-mode",
        choices=["mcq", "generative"],
        help="PersonaMem Answer/scoring mode using existing retrieval (default: mcq)",
    )
    parser.add_argument(
        "--answer-concurrency",
        type=_positive_int,
        default=DEFAULT_ANSWER_CONCURRENCY,
        help=f"parallel Answer shards (default: {DEFAULT_ANSWER_CONCURRENCY})",
    )
    parser.add_argument(
        "--eval-concurrency",
        type=_positive_int,
        default=DEFAULT_EVAL_CONCURRENCY,
        help=f"parallel Eval/Judge shards (default: {DEFAULT_EVAL_CONCURRENCY})",
    )
    args = parser.parse_args(argv)
    try:
        summary = evaluate(
            args.retrieval,
            args.output_dir or args.retrieval.parent,
            aml_repo=args.aml_repo,
            unit=args.unit,
            aml_commit=args.aml_commit,
            answer_concurrency=args.answer_concurrency,
            eval_concurrency=args.eval_concurrency,
            persona_mode=args.persona_mode,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
