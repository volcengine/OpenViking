#!/usr/bin/env python3
"""Three-round blind rubric judge for the Journey to the West Wiki A/B run."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

DEFAULT_ENV_FILE = Path.home() / ".openviking_benchmark_env"
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "ep-20260514141842-c7s2n"
REVIEW_ROUNDS = 3
CORRECT_THRESHOLD = 8


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_secret(message: str, secret: str) -> str:
    if secret:
        return message.replace(secret, "[REDACTED]")
    return message


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def load_question_set(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = load_json(path)
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise ValueError("question file must contain a questions array")
    indexed: dict[str, dict[str, Any]] = {}
    for question in questions:
        required = {"id", "question", "gold_answer", "rubric", "critical_errors"}
        missing = required - set(question)
        if missing:
            raise ValueError(f"question missing fields {sorted(missing)}")
        question_id = str(question["id"])
        if question_id in indexed:
            raise ValueError(f"duplicate question id: {question_id}")
        rubric = question["rubric"]
        if not isinstance(rubric, list) or not rubric:
            raise ValueError(f"question {question_id} has no rubric")
        if sum(int(item["points"]) for item in rubric) != 10:
            raise ValueError(f"question {question_id} rubric must total 10 points")
        indexed[question_id] = question
    return payload, indexed


def index_runs(payload: dict[str, Any], expected_exp: str) -> dict[str, dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("evaluation input must contain a results array")
    indexed: dict[str, dict[str, Any]] = {}
    for run in results:
        if str(run.get("exp", "")).upper() != expected_exp:
            continue
        question_id = str(run.get("question_id", ""))
        if not question_id:
            raise ValueError(f"{expected_exp} result missing question_id")
        if question_id in indexed:
            raise ValueError(f"duplicate {expected_exp} result for {question_id}")
        indexed[question_id] = run
    return indexed


def anonymous_mapping(question_id: str, round_index: int) -> dict[str, str]:
    """Hash fixes the base order; alternating rounds rotates the labels."""
    arms = ["A", "B"]
    if hashlib.sha256(question_id.encode("utf-8")).digest()[0] & 1:
        arms.reverse()
    if round_index % 2 == 1:
        arms.reverse()
    return {"answer_1": arms[0], "answer_2": arms[1]}


def blind_payload(
    question: dict[str, Any],
    mapping: dict[str, str],
    answer_a: str,
    answer_b: str,
) -> dict[str, Any]:
    answers = {"A": answer_a, "B": answer_b}
    return {
        "question": question["question"],
        "gold_answer": question["gold_answer"],
        "rubric": question["rubric"],
        "critical_errors": [
            {"index": index, "description": description}
            for index, description in enumerate(question["critical_errors"], 1)
        ],
        "anonymous_answers": {label: answers[arm] for label, arm in mapping.items()},
    }


SYSTEM_PROMPT = """You are a strict benchmark grader. Grade two anonymous answers independently.
Use only the supplied question, gold answer, rubric, pre-registered critical errors, and
anonymous answer text. Award each rubric criterion an integer from zero through that
criterion's maximum. Do not infer or discuss which system produced an answer. A critical
error may be reported only by its supplied integer index and only when the answer actually
commits that error. Return JSON only and follow the requested schema exactly."""


def judge_prompt(payload: dict[str, Any], repair_errors: list[str] | None = None) -> str:
    schema = {
        "answers": {
            "answer_1": {
                "rubric_awards": [{"criterion_index": 1, "awarded": 0, "reason": "brief reason"}],
                "score": 0,
                "critical_error_indices": [],
                "reasoning": "brief overall assessment",
            },
            "answer_2": {
                "rubric_awards": [{"criterion_index": 1, "awarded": 0, "reason": "brief reason"}],
                "score": 0,
                "critical_error_indices": [],
                "reasoning": "brief overall assessment",
            },
        }
    }
    repair = ""
    if repair_errors:
        repair = (
            "\nThe prior attempt violated the output protocol. Correct these issues without "
            f"changing the grading task: {json.dumps(repair_errors, ensure_ascii=False)}\n"
        )
    return (
        "Grade the following blind evaluation packet. rubric_awards must contain exactly "
        "one entry for every criterion, in index order. score must exactly equal the sum "
        "of awarded values. critical_error_indices must contain only registered 1-based "
        f"indices.{repair}\nPacket:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "Required JSON shape (expand rubric_awards to all criteria):\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )


def extract_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response contains no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("judge response must be a JSON object")
    return value


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_grade(
    raw: dict[str, Any], question: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    answers = raw.get("answers")
    labels = ("answer_1", "answer_2")
    if not isinstance(answers, dict):
        return None, ["answers must be an object"]
    if set(answers) != set(labels):
        errors.append("answers must have exactly answer_1 and answer_2")

    normalized: dict[str, Any] = {}
    rubric = question["rubric"]
    critical_count = len(question["critical_errors"])
    for label in labels:
        grade = answers.get(label)
        if not isinstance(grade, dict):
            errors.append(f"{label} must be an object")
            continue
        awards = grade.get("rubric_awards")
        normalized_awards: list[dict[str, Any]] = []
        if not isinstance(awards, list) or len(awards) != len(rubric):
            errors.append(f"{label}.rubric_awards must have {len(rubric)} entries")
        else:
            for expected_index, (award, criterion) in enumerate(
                zip(awards, rubric, strict=True), 1
            ):
                if not isinstance(award, dict):
                    errors.append(f"{label} criterion {expected_index} must be an object")
                    continue
                criterion_index = award.get("criterion_index")
                awarded = award.get("awarded")
                maximum = int(criterion["points"])
                if criterion_index != expected_index:
                    errors.append(
                        f"{label} criterion index {criterion_index!r} != {expected_index}"
                    )
                if not is_int(awarded) or not 0 <= awarded <= maximum:
                    errors.append(
                        f"{label} criterion {expected_index} awarded must be integer 0..{maximum}"
                    )
                normalized_awards.append(
                    {
                        "criterion_index": expected_index,
                        "awarded": awarded,
                        "max_points": maximum,
                        "reason": str(award.get("reason", "")),
                    }
                )

        score = grade.get("score")
        if not is_int(score):
            errors.append(f"{label}.score must be an integer")
        if len(normalized_awards) == len(rubric) and all(
            is_int(item["awarded"]) for item in normalized_awards
        ):
            calculated = sum(item["awarded"] for item in normalized_awards)
            if score != calculated:
                errors.append(f"{label}.score {score!r} != awarded sum {calculated}")

        critical = grade.get("critical_error_indices")
        if not isinstance(critical, list):
            errors.append(f"{label}.critical_error_indices must be an array")
            critical = []
        elif any(not is_int(index) or not 1 <= index <= critical_count for index in critical):
            errors.append(f"{label}.critical_error_indices contains an unregistered index")
        elif len(set(critical)) != len(critical):
            errors.append(f"{label}.critical_error_indices contains duplicates")

        normalized[label] = {
            "rubric_awards": normalized_awards,
            "score": score,
            "critical_error_indices": critical,
            "reasoning": str(grade.get("reasoning", "")),
        }

    return ({"answers": normalized} if not errors else None), errors


def usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", prompt + completion) or 0)
    return {"input_tokens": prompt, "output_tokens": completion, "total_tokens": total}


def add_usage(total: dict[str, int], current: dict[str, int]) -> None:
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] += int(current.get(key, 0))


async def judge_round(
    client: AsyncOpenAI,
    model: str,
    question: dict[str, Any],
    answer_a: str,
    answer_b: str,
    round_index: int,
) -> dict[str, Any]:
    mapping = anonymous_mapping(str(question["id"]), round_index)
    packet = blind_payload(question, mapping, answer_a, answer_b)
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    protocol_errors: list[str] = []

    for attempt in (1, 2):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": judge_prompt(packet, protocol_errors if attempt == 2 else None),
                    },
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=120,
            )
        except Exception as exc:
            return {
                "round": round_index + 1,
                "label_mapping": mapping,
                "valid": False,
                "attempts": attempt,
                "grades": None,
                "protocol_errors": protocol_errors,
                "network_error": redact_secret(f"{type(exc).__name__}: {exc}", str(client.api_key)),
                "judge_token_usage": token_usage,
            }

        add_usage(token_usage, usage_dict(response))
        content = response.choices[0].message.content or ""
        try:
            parsed = extract_json(content)
            normalized, errors = validate_grade(parsed, question)
        except (json.JSONDecodeError, ValueError) as exc:
            normalized, errors = None, [str(exc)]
        if normalized is not None:
            return {
                "round": round_index + 1,
                "label_mapping": mapping,
                "valid": True,
                "attempts": attempt,
                "grades": normalized["answers"],
                "protocol_errors": protocol_errors,
                "network_error": None,
                "judge_token_usage": token_usage,
            }
        protocol_errors.extend(f"attempt {attempt}: {error}" for error in errors)

    return {
        "round": round_index + 1,
        "label_mapping": mapping,
        "valid": False,
        "attempts": 2,
        "grades": None,
        "protocol_errors": protocol_errors,
        "network_error": None,
        "judge_token_usage": token_usage,
    }


def aggregate_rounds(question: dict[str, Any], rounds: list[dict[str, Any]]) -> dict[str, Any]:
    protocol_failure = any(not item["valid"] for item in rounds)
    network_failure = any(bool(item.get("network_error")) for item in rounds)
    if protocol_failure:
        return {
            "judge_protocol_error": True,
            "judge_network_error": network_failure,
            "A": None,
            "B": None,
        }

    by_arm: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for item in rounds:
        for label, arm in item["label_mapping"].items():
            by_arm[arm].append(item["grades"][label])

    aggregated: dict[str, Any] = {
        "judge_protocol_error": False,
        "judge_network_error": False,
    }
    for arm, grades in by_arm.items():
        scores = [int(grade["score"]) for grade in grades]
        critical_error_rounds = sum(bool(grade["critical_error_indices"]) for grade in grades)
        criterion_medians = []
        for index, rubric_item in enumerate(question["rubric"]):
            awarded = [int(grade["rubric_awards"][index]["awarded"]) for grade in grades]
            criterion_medians.append(
                {
                    "criterion_index": index + 1,
                    "awarded_median": int(statistics.median(awarded)),
                    "max_points": int(rubric_item["points"]),
                }
            )
        median_score = int(statistics.median(scores))
        aggregated[arm] = {
            "round_scores": scores,
            "median_score": median_score,
            "criterion_medians": criterion_medians,
            "critical_error_rounds": critical_error_rounds,
            "rounds_without_critical_error": REVIEW_ROUNDS - critical_error_rounds,
            "correct": median_score >= CORRECT_THRESHOLD and critical_error_rounds <= 1,
        }
    return aggregated


async def judge_question(
    client: AsyncOpenAI,
    semaphore: asyncio.Semaphore,
    model: str,
    question: dict[str, Any],
    run_a: dict[str, Any],
    run_b: dict[str, Any],
) -> dict[str, Any]:
    async with semaphore:
        answer_a = str(run_a.get("response", run_a.get("answer", "")))
        answer_b = str(run_b.get("response", run_b.get("answer", "")))
        rounds = [
            await judge_round(client, model, question, answer_a, answer_b, index)
            for index in range(REVIEW_ROUNDS)
        ]
    aggregate = aggregate_rounds(question, rounds)
    return {
        "question_id": question["id"],
        "rounds": rounds,
        "aggregate": aggregate,
    }


def error_present(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value)
    return bool(value)


def run_cost_summary(runs: list[dict[str, Any]], correct_count: int) -> dict[str, Any]:
    wiki_uris = [uri for run in runs for uri in run.get("wiki_uris_read", [])]
    resource_uris = [uri for run in runs for uri in run.get("resource_uris_read", [])]
    input_tokens = sum(int(run.get("model_input_tokens", 0) or 0) for run in runs)
    output_tokens = sum(int(run.get("model_output_tokens", 0) or 0) for run in runs)
    total_tokens = sum(int(run.get("model_total_tokens", 0) or 0) for run in runs)
    latencies = [float(run.get("latency_seconds", 0) or 0) for run in runs]
    return {
        "wiki_reads": {
            "multi_read_calls": sum(int(run.get("wiki_read_calls", 0) or 0) for run in runs),
            "successful_uri_reads": sum(
                int(run.get("wiki_successful_reads", len(run.get("wiki_uris_read", []))) or 0)
                for run in runs
            ),
            "unique_files_sum_per_question": sum(
                int(run.get("unique_wiki_files", 0) or 0) for run in runs
            ),
            "unique_files_global": len(set(wiki_uris)),
        },
        "resource_reads": {
            "multi_read_calls": sum(int(run.get("resource_read_calls", 0) or 0) for run in runs),
            "successful_uri_reads": sum(
                int(
                    run.get(
                        "resource_successful_reads",
                        len(run.get("resource_uris_read", [])),
                    )
                    or 0
                )
                for run in runs
            ),
            "unique_files_sum_per_question": sum(
                int(run.get("unique_resource_files", 0) or 0) for run in runs
            ),
            "unique_files_global": len(set(resource_uris)),
        },
        "fallback_count": sum(bool(run.get("fallback_used")) for run in runs),
        "fallback_rate": (
            sum(bool(run.get("fallback_used")) for run in runs) / len(runs) if runs else None
        ),
        "model_tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "tokens_per_correct_answer": (total_tokens / correct_count if correct_count else None),
        },
        "latency_seconds": {
            "total": round(sum(latencies), 6),
            "mean": round(statistics.mean(latencies), 6) if latencies else None,
        },
        "scope_violations": sum(bool(run.get("scope_violation")) for run in runs),
        "protocol_failures": sum(error_present(run.get("protocol_error")) for run in runs),
        "network_failures": sum(error_present(run.get("network_error")) for run in runs),
    }


def summarize(
    judged: list[dict[str, Any]],
    runs_a: dict[str, dict[str, Any]],
    runs_b: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    valid = [item for item in judged if not item["aggregate"]["judge_protocol_error"]]
    arms: dict[str, Any] = {}
    for arm, runs in (("A", runs_a), ("B", runs_b)):
        scores = [item["aggregate"][arm]["median_score"] for item in valid]
        correct = sum(bool(item["aggregate"][arm]["correct"]) for item in valid)
        awarded = sum(
            criterion["awarded_median"]
            for item in valid
            for criterion in item["aggregate"][arm]["criterion_medians"]
        )
        possible = sum(
            criterion["max_points"]
            for item in valid
            for criterion in item["aggregate"][arm]["criterion_medians"]
        )
        ordered_runs = [runs[item["question_id"]] for item in judged]
        arms[arm] = {
            "mean_score": statistics.mean(scores) if scores else None,
            "correct_count": correct,
            "accuracy": correct / len(valid) if valid else None,
            "judged_count": len(valid),
            "atomic_rubric": {
                "awarded": awarded,
                "possible": possible,
                "score_rate": awarded / possible if possible else None,
            },
            **run_cost_summary(ordered_runs, correct),
        }

    wins = ties = losses = 0
    differences: list[int] = []
    for item in valid:
        difference = item["aggregate"]["A"]["median_score"] - item["aggregate"]["B"]["median_score"]
        differences.append(difference)
        if difference > 0:
            wins += 1
        elif difference < 0:
            losses += 1
        else:
            ties += 1

    judge_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for item in judged:
        for round_result in item["rounds"]:
            add_usage(judge_usage, round_result["judge_token_usage"])
    return {
        "paired_questions": len(judged),
        "fully_valid_judgements": len(valid),
        "judge_protocol_failures": sum(
            item["aggregate"]["judge_protocol_error"] for item in judged
        ),
        "judge_network_failures": sum(item["aggregate"]["judge_network_error"] for item in judged),
        "arms": arms,
        "paired_outcome_for_A": {"wins": wins, "ties": ties, "losses": losses},
        "mean_score_difference_A_minus_B": (statistics.mean(differences) if differences else None),
        "judge_tokens": judge_usage,
        "note": "judge_tokens are reported separately and excluded from A/B run cost",
    }


async def run_judge(args: argparse.Namespace) -> dict[str, Any]:
    question_path = Path(args.questions).expanduser().resolve()
    exp_a_path = Path(args.exp_a).expanduser().resolve()
    exp_b_path = Path(args.exp_b).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"output exists; pass --overwrite: {output_path}")

    question_set, questions = load_question_set(question_path)
    payload_a = load_json(exp_a_path)
    payload_b = load_json(exp_b_path)
    runs_a = index_runs(payload_a, "A")
    runs_b = index_runs(payload_b, "B")
    unknown = (set(runs_a) | set(runs_b)) - set(questions)
    if unknown:
        raise ValueError(f"results contain unknown question ids: {sorted(unknown)}")
    missing_a = sorted(set(runs_b) - set(runs_a))
    missing_b = sorted(set(runs_a) - set(runs_b))
    if missing_a or missing_b:
        raise ValueError(f"unpaired results; missing A={missing_a}, missing B={missing_b}")
    paired_ids = [question_id for question_id in questions if question_id in runs_a]
    if not paired_ids:
        raise ValueError("no paired A/B results found")

    load_dotenv(DEFAULT_ENV_FILE, override=False)
    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(f"ARK_API_KEY is required (default env file: {DEFAULT_ENV_FILE})")
    client = AsyncOpenAI(base_url=args.base_url, api_key=api_key)
    semaphore = asyncio.Semaphore(args.parallel)
    started_at = utc_now()
    try:
        judged = await asyncio.gather(
            *(
                judge_question(
                    client,
                    semaphore,
                    args.model,
                    questions[question_id],
                    runs_a[question_id],
                    runs_b[question_id],
                )
                for question_id in paired_ids
            )
        )
    finally:
        await client.close()
    payload = {
        "schema_version": "openviking_wiki_ab_judge_v1",
        "judge_started_at": started_at,
        "judge_finished_at": utc_now(),
        "questions": str(question_path),
        "questions_sha256": sha256_file(question_path),
        "exp_a": str(exp_a_path),
        "exp_a_sha256": sha256_file(exp_a_path),
        "exp_b": str(exp_b_path),
        "exp_b_sha256": sha256_file(exp_b_path),
        "judge_model": args.model,
        "judge_base_url": args.base_url,
        "review_rounds": REVIEW_ROUNDS,
        "correct_rule": {
            "median_score_at_least": CORRECT_THRESHOLD,
            "minimum_rounds_without_critical_error": 2,
            "all_rounds_must_be_protocol_valid": True,
        },
        "results": judged,
        "summary": summarize(judged, runs_a, runs_b),
        "question_schema_version": question_set.get("schema_version"),
    }
    atomic_write_json(output_path, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Three-round blind judge for Wiki A/B results.")
    parser.add_argument("--questions", required=True, help="Frozen question JSON path")
    parser.add_argument("--exp-a", required=True, help="Experiment A result JSON")
    parser.add_argument("--exp-b", required=True, help="Experiment B result JSON")
    parser.add_argument("--output", required=True, help="Judge output JSON")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    try:
        payload = asyncio.run(run_judge(args))
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"Wrote {len(payload['results'])} paired judgements to "
        f"{Path(args.output).expanduser().resolve()}"
    )


if __name__ == "__main__":
    main()
