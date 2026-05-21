from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

HERMES_USAGE_KEYS = [
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "api_call_count",
    "tool_call_count",
]


def require_suite(value: str) -> str:
    if value not in {"baseline", "e2e", "preingest"}:
        raise argparse.ArgumentTypeError("suite must be one of: baseline, e2e, preingest")
    return value


def result_dir_name(suite: str) -> str:
    if suite == "baseline":
        return "result_baseline"
    if suite == "e2e":
        return "result_e2e"
    return "result_preingest"


def summary_title(suite: str) -> str:
    if suite == "baseline":
        return "Hermes Baseline"
    if suite == "e2e":
        return "Hermes OpenViking E2E"
    return "Hermes + OpenViking (pre-ingest)"


def format_optional_int(value: int | None) -> str:
    return "unavailable" if value is None else f"{value:,}"


def format_optional_float(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:,.2f}"


def read_int(row: dict, key: str) -> int:
    try:
        return int(float(row.get(key, 0) or 0))
    except (ValueError, TypeError):
        return 0


def read_float(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except (ValueError, TypeError):
        return 0.0


def read_first_int(row: dict, keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in row and row.get(key) not in {None, ""}:
            return read_int(row, key)
    return 0


@dataclass
class HermesUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    api_call_count: int = 0
    tool_call_count: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    @property
    def cache_tokens(self) -> int:
        return self.cache_read_tokens + self.cache_write_tokens

    @property
    def no_cache_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_mapping(cls, values: dict[str, int]) -> "HermesUsage":
        return cls(**{key: int(values.get(key, 0) or 0) for key in HERMES_USAGE_KEYS})


@dataclass
class HermesUsageSummary:
    usage: HermesUsage
    source: str
    matched_sessions: int
    expected_sessions: int
    authoritative: bool


@dataclass
class OpenVikingUsage:
    embedding_input_tokens: int = 0
    embedding_output_tokens: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0

    @property
    def embedding_tokens(self) -> int:
        return self.embedding_input_tokens + self.embedding_output_tokens

    @property
    def has_tokens(self) -> bool:
        return any(
            [
                self.embedding_input_tokens,
                self.embedding_output_tokens,
                self.llm_input_tokens,
                self.llm_output_tokens,
            ]
        )


def read_true_token_csv(csv_path: Path) -> tuple[int, int, int, int]:
    if not csv_path.exists():
        return 0, 0, 0, 0
    totals = [0, 0, 0, 0]
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            totals[0] += read_int(row, "embedding_input_tokens")
            totals[1] += read_int(row, "embedding_output_tokens")
            totals[2] += read_int(row, "vlm_llm_input_tokens")
            totals[3] += read_int(row, "vlm_llm_output_tokens")
    return totals[0], totals[1], totals[2], totals[3]


def resolve_hermes_state_db(value: str | None) -> Path | None:
    if value:
        path = Path(value).expanduser()
        return path if path.exists() else None
    hermes_home = os.getenv("HERMES_HOME", "").strip()
    if hermes_home:
        path = Path(hermes_home).expanduser() / "state.db"
        return path if path.exists() else None
    return None


def read_hermes_sessions(state_db: Path | None) -> dict[str, dict[str, int]]:
    if state_db is None:
        return {}
    try:
        conn = sqlite3.connect(state_db)
    except sqlite3.Error:
        return {}
    try:
        available = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "id" not in available:
            return {}
        columns = ["id"] + [key for key in HERMES_USAGE_KEYS if key in available]
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM sessions").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    sessions: dict[str, dict[str, int]] = {}
    for row in rows:
        session_id = str(row[0])
        usage = dict.fromkeys(HERMES_USAGE_KEYS, 0)
        for idx, key in enumerate(columns[1:], start=1):
            try:
                usage[key] = int(row[idx] or 0)
            except (TypeError, ValueError):
                usage[key] = 0
        sessions[session_id] = usage
    return sessions


def sum_hermes_usage(
    state_sessions: dict[str, dict[str, int]], session_ids: list[str]
) -> dict[str, int]:
    totals = dict.fromkeys(HERMES_USAGE_KEYS, 0)
    seen = set()
    for session_id in session_ids:
        if session_id in seen:
            continue
        seen.add(session_id)
        usage = state_sessions.get(session_id)
        if not usage:
            continue
        for key in HERMES_USAGE_KEYS:
            totals[key] += int(usage.get(key, 0) or 0)
    return totals


def hermes_total_tokens(usage: dict[str, int]) -> int:
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + usage.get("cache_read_tokens", 0)
        + usage.get("cache_write_tokens", 0)
    )


def read_csv_rows(csv_path: str | Path) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def valid_qa_rows(input_path: str | Path) -> list[dict]:
    return [row for row in read_csv_rows(input_path) if str(row.get("category", "")) != "5"]


def qa_state_session_ids(rows: list[dict], state_sessions: dict[str, dict[str, int]]) -> list[str]:
    matched = []
    for row in rows:
        for key in ("hermes_session_id", "conversation"):
            session_id = row.get(key, "")
            if session_id in state_sessions:
                matched.append(session_id)
                break
    return matched


def summarize_score(rows: list[dict]) -> dict:
    correct = 0
    wrong = 0
    category_rows = defaultdict(int)
    category_graded = defaultdict(int)
    category_correct = defaultdict(int)
    total_qa_latency = 0.0

    for row in rows:
        category = str(row.get("category", ""))
        category_rows[category] += 1
        if str(row.get("result", "")).upper() == "CORRECT":
            correct += 1
            category_correct[category] += 1
            category_graded[category] += 1
        elif str(row.get("result", "")).upper() == "WRONG":
            wrong += 1
            category_graded[category] += 1
        total_qa_latency += read_float(row, "qa_latency_sec")

    graded = correct + wrong
    category_stats = {}
    for category, row_count in category_rows.items():
        cat_graded = category_graded[category]
        cat_correct = category_correct[category]
        category_stats[category] = {
            "rows": row_count,
            "graded": cat_graded,
            "correct": cat_correct,
            "accuracy": cat_correct / cat_graded if cat_graded else 0.0,
        }
    return {
        "rows": len(rows),
        "graded": graded,
        "correct": correct,
        "wrong": wrong,
        "ungraded": len(rows) - graded,
        "accuracy": correct / graded if graded else 0.0,
        "avg_qa_latency": total_qa_latency / len(rows) if rows else 0.0,
        "categories": category_stats,
    }


def sum_qa_csv_usage(rows: list[dict]) -> HermesUsage:
    return HermesUsage(
        input_tokens=sum(read_int(row, "qa_input_tokens") for row in rows),
        output_tokens=sum(read_int(row, "qa_output_tokens") for row in rows),
        cache_read_tokens=sum(read_int(row, "qa_cache_read_tokens") for row in rows),
        cache_write_tokens=sum(read_int(row, "qa_cache_write_tokens") for row in rows),
    )


def summarize_qa_hermes_usage(
    rows: list[dict], state_sessions: dict[str, dict[str, int]]
) -> HermesUsageSummary:
    matched_ids = qa_state_session_ids(rows, state_sessions)
    matched_count = len(set(matched_ids))
    if rows and matched_count == len(rows):
        return HermesUsageSummary(
            usage=HermesUsage.from_mapping(sum_hermes_usage(state_sessions, matched_ids)),
            source="state.db",
            matched_sessions=matched_count,
            expected_sessions=len(rows),
            authoritative=True,
        )
    return HermesUsageSummary(
        usage=sum_qa_csv_usage(rows),
        source="qa_results.csv fallback",
        matched_sessions=matched_count,
        expected_sessions=len(rows),
        authoritative=False,
    )


def import_state_session_ids(
    rows: list[dict],
    suite: str,
    state_sessions: dict[str, dict[str, int]],
) -> list[str]:
    if suite == "e2e":
        row_ids = []
        for row in rows:
            for key in ("session_id", "conversation"):
                session_id = row.get(key, "")
                if session_id in state_sessions:
                    row_ids.append(session_id)
                    break
        if row_ids:
            return row_ids
        ids = [
            session_id
            for session_id in state_sessions
            if session_id.startswith("locomo-e2e-") and not session_id.startswith("locomo-e2e-qa-")
        ]
        if ids:
            return sorted(ids)
    if suite == "baseline":
        ids = [
            session_id
            for session_id in state_sessions
            if session_id.startswith("locomo-native-")
            and not session_id.startswith("locomo-native-qa-")
        ]
        if ids:
            return sorted(ids)
    return [
        row.get("conversation", "") for row in rows if row.get("conversation", "") in state_sessions
    ]


def sum_import_csv_usage(rows: list[dict], suite: str) -> HermesUsage:
    if suite == "baseline":
        total_tokens = sum(read_int(row, "total_tokens") for row in rows)
        input_tokens = sum(read_first_int(row, ("input_tokens",)) for row in rows)
        output_tokens = sum(read_first_int(row, ("output_tokens",)) for row in rows)
        cache_read = sum(read_first_int(row, ("cache_read", "cache_read_tokens")) for row in rows)
        cache_write = sum(
            read_first_int(row, ("cache_write", "cache_write_tokens")) for row in rows
        )
        if input_tokens or output_tokens or cache_read or cache_write:
            return HermesUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            )
        return HermesUsage(input_tokens=total_tokens)

    if suite == "e2e":
        return HermesUsage(
            input_tokens=sum(read_int(row, "input_tokens") for row in rows),
            output_tokens=sum(read_int(row, "output_tokens") for row in rows),
            cache_read_tokens=sum(
                read_first_int(row, ("cache_read", "cache_read_tokens")) for row in rows
            ),
            cache_write_tokens=sum(
                read_first_int(row, ("cache_write", "cache_write_tokens")) for row in rows
            ),
        )

    return HermesUsage()


def summarize_import_hermes_usage(
    import_csv: Path, suite: str, state_sessions: dict[str, dict[str, int]]
) -> HermesUsageSummary | None:
    if suite == "preingest" or not import_csv.exists():
        return None

    rows = read_csv_rows(import_csv)
    if not rows:
        return None

    matched_ids = import_state_session_ids(rows, suite, state_sessions)
    matched_count = len(set(matched_ids))
    state_is_complete = bool(matched_ids) and (suite != "e2e" or matched_count == len(rows))
    if state_is_complete:
        expected_count = len(rows) if suite == "e2e" else matched_count
        return HermesUsageSummary(
            usage=HermesUsage.from_mapping(sum_hermes_usage(state_sessions, matched_ids)),
            source="state.db",
            matched_sessions=matched_count,
            expected_sessions=expected_count,
            authoritative=True,
        )

    return HermesUsageSummary(
        usage=sum_import_csv_usage(rows, suite),
        source="import_success.csv fallback",
        matched_sessions=0,
        expected_sessions=len(rows),
        authoritative=False,
    )


def read_openviking_usage(result_dir: Path) -> tuple[OpenVikingUsage, OpenVikingUsage]:
    import_emb_in, import_emb_out, import_llm_in, import_llm_out = read_true_token_csv(
        result_dir / "import_true_tokens.csv"
    )
    eval_emb_in, eval_emb_out, eval_llm_in, eval_llm_out = read_true_token_csv(
        result_dir / "eval_true_tokens.csv"
    )
    return (
        OpenVikingUsage(import_emb_in, import_emb_out, import_llm_in, import_llm_out),
        OpenVikingUsage(eval_emb_in, eval_emb_out, eval_llm_in, eval_llm_out),
    )


def format_usage_pair(label: str, qa_value: int, import_value: int | None) -> str:
    if import_value is None:
        return f"  {label}: {qa_value:,} (QA)"
    return f"  {label}: {qa_value:,} (QA), {import_value:,} (Ingest)"


def format_source(summary: HermesUsageSummary) -> str:
    if summary.source == "state.db":
        return (
            f"{summary.source}, matched {summary.matched_sessions:,}/"
            f"{summary.expected_sessions:,} sessions"
        )
    if summary.matched_sessions:
        return (
            f"{summary.source}, state.db matched {summary.matched_sessions:,}/"
            f"{summary.expected_sessions:,} sessions"
        )
    return summary.source


def format_score_lines(score: dict) -> list[str]:
    lines = [
        "Score",
        (f"  overall_accuracy: {score['accuracy']:.2%} ({score['correct']:,}/{score['graded']:,})"),
    ]
    if score["ungraded"]:
        lines.append(f"  ungraded_rows: {score['ungraded']:,}")

    lines.append("  category_accuracy:")
    for category in sorted(
        score["categories"].keys(),
        key=lambda value: int(value) if value.isdigit() else value,
    ):
        item = score["categories"][category]
        lines.append(
            f"    category {category}: {item['correct']:,}/{item['graded']:,} "
            f"({item['accuracy']:.2%})"
        )
        ungraded = item["rows"] - item["graded"]
        if ungraded:
            lines.append(f"      ungraded_rows: {ungraded:,}")
    return lines


def format_summary(
    suite: str,
    score: dict,
    qa_usage: HermesUsageSummary,
    import_usage: HermesUsageSummary | None,
    ov_import: OpenVikingUsage,
    ov_eval: OpenVikingUsage,
) -> list[str]:
    import_hermes = import_usage.usage if import_usage is not None else None
    llm_calls = qa_usage.usage.api_call_count if qa_usage.authoritative else None
    llm_calls_per_qa = (
        llm_calls / qa_usage.expected_sessions
        if llm_calls is not None and qa_usage.expected_sessions
        else None
    )

    lines = [
        f"=== {summary_title(suite)} Summary ===",
        "",
        *format_score_lines(score),
        "",
        "Hermes Usage",
        f"  qa_source: {format_source(qa_usage)}",
    ]
    if import_usage is not None:
        lines.append(f"  ingest_source: {format_source(import_usage)}")
    lines.extend(
        [
            format_usage_pair(
                "total_tokens",
                qa_usage.usage.total_tokens,
                import_hermes.total_tokens if import_hermes is not None else None,
            ),
            format_usage_pair(
                "cache_tokens",
                qa_usage.usage.cache_tokens,
                import_hermes.cache_tokens if import_hermes is not None else None,
            ),
            format_usage_pair(
                "no_cache_tokens",
                qa_usage.usage.no_cache_tokens,
                import_hermes.no_cache_tokens if import_hermes is not None else None,
            ),
            "",
            "OpenViking Usage",
        ]
    )

    if ov_import.has_tokens or ov_eval.has_tokens:
        lines.extend(
            [
                f"  embedding: {ov_import.embedding_tokens:,} (Ingest), {ov_eval.embedding_tokens:,} (QA)",
                f"  llm_input: {ov_import.llm_input_tokens:,} (Ingest), {ov_eval.llm_input_tokens:,} (QA)",
                f"  llm_output: {ov_import.llm_output_tokens:,} (Ingest), {ov_eval.llm_output_tokens:,} (QA)",
            ]
        )
    else:
        lines.append("  unavailable")

    lines.extend(
        [
            "",
            "Runtime",
            f"  avg_seconds_per_qa: {score['avg_qa_latency']:.4f}",
            f"  hermes_qa_llm_calls: {format_optional_int(llm_calls)} total",
            f"  hermes_qa_llm_calls_per_qa: {format_optional_float(llm_calls_per_qa)}",
        ]
    )
    return lines


def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    parser = argparse.ArgumentParser(description="Summarize shared Hermes LoCoMo judge results")
    parser.add_argument("--suite", type=require_suite, default="baseline", help="Benchmark suite")
    parser.add_argument("--input", default=None, help="Path to graded CSV")
    parser.add_argument("--import-csv", default=None, help="Path to import_success.csv")
    parser.add_argument(
        "--hermes-state-db",
        default=None,
        help="Path to Hermes state.db for authoritative token/cache accounting",
    )
    args = parser.parse_args()

    default_result_dir = script_dir / result_dir_name(args.suite)
    if args.input is None:
        args.input = str(default_result_dir / "qa_results.csv")
    result_dir = Path(args.input).expanduser().resolve().parent
    if args.import_csv is None:
        args.import_csv = str(result_dir / "import_success.csv")

    state_db = resolve_hermes_state_db(args.hermes_state_db)
    state_sessions = read_hermes_sessions(state_db)

    if not os.path.exists(args.input):
        print(f"Warning: QA result file not found: {args.input}")
        return

    rows = valid_qa_rows(args.input)
    score = summarize_score(rows)
    qa_usage = summarize_qa_hermes_usage(rows, state_sessions)
    import_usage = summarize_import_hermes_usage(Path(args.import_csv), args.suite, state_sessions)
    ov_import, ov_eval = read_openviking_usage(result_dir)
    output_lines = format_summary(args.suite, score, qa_usage, import_usage, ov_import, ov_eval)

    for line in output_lines:
        print(line)


if __name__ == "__main__":
    main()
