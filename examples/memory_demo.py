"""Memory skill test demo covering T1-T10 with real OpenViking pipeline.

This script runs real memory extraction/dedup/merge against OpenViking, prints
human-readable traces, and outputs pass/fail for each test case.

Usage:
  export OPENVIKING_CONFIG_FILE=ov.conf
  python examples/memory_test_demo.py --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from openviking.message.part import TextPart
from openviking.session.memory_deduplicator import MemoryDeduplicator
from openviking.sync_client import SyncOpenViking


@dataclass
class Turn:
    session_key: str
    user_text: str
    assistant_text: str = "收到。"


@dataclass
class QueryCheck:
    query: str
    require_groups: List[List[str]] = field(default_factory=list)
    forbidden_groups: List[List[str]] = field(default_factory=list)
    min_hits: int = 1


@dataclass
class CaseSpec:
    case_id: str
    title: str
    turns: List[Turn]
    checks: List[QueryCheck]
    expected_categories: Set[str] = field(default_factory=set)
    expect_merge_action: bool = False
    expect_delete_action: bool = False
    expect_skip_decision: bool = False
    expect_none_decision: bool = False
    expect_no_new_memory: bool = False
    expect_merge_from_session_key: str = ""
    max_created_files: Optional[int] = None


@dataclass
class Hit:
    query: str
    uri: str
    abstract: str
    content: str
    score: Optional[float]
    target_uri: str


@dataclass
class DedupRecord:
    round_name: str
    source_session: str
    category: str
    decision: str
    candidate_abstract: str
    actions: List[Dict[str, str]]


@dataclass
class CaseResult:
    case_id: str
    title: str
    passed: bool
    reasons: List[str]
    created: List[str]
    deleted: List[str]
    changed: List[str]


class DedupRecorder:
    """Collect runtime dedup decisions without modifying production modules."""

    def __init__(self) -> None:
        self.records: List[DedupRecord] = []
        self.current_round: str = ""
        self._original: Optional[Callable[..., Any]] = None

    def install(self) -> None:
        if self._original is not None:
            return

        self._original = MemoryDeduplicator.deduplicate
        recorder = self

        async def _wrapped(self_dedup, candidate):
            result = await recorder._original(self_dedup, candidate)
            recorder.records.append(
                DedupRecord(
                    round_name=recorder.current_round,
                    source_session=candidate.source_session,
                    category=candidate.category.value,
                    decision=result.decision.value,
                    candidate_abstract=candidate.abstract,
                    actions=[
                        {
                            "decision": action.decision.value,
                            "uri": action.memory.uri,
                            "reason": action.reason,
                        }
                        for action in (result.actions or [])
                    ],
                )
            )
            return result

        MemoryDeduplicator.deduplicate = _wrapped

    def uninstall(self) -> None:
        if self._original is not None:
            MemoryDeduplicator.deduplicate = self._original
            self._original = None


def _print_section(title: str, body: str = "") -> None:
    print("\n" + "=" * 90)
    print(title)
    if body:
        print("-" * 90)
        print(body)


def _safe_list(items: Iterable[Any]) -> List[Any]:
    try:
        return list(items)
    except Exception:
        return []


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _collect_memory_snapshot(client: SyncOpenViking) -> Dict[str, str]:
    """Snapshot all memory files as uri -> content hash."""
    snapshot: Dict[str, str] = {}
    for root in ["viking://user/memories", "viking://agent/memories"]:
        try:
            entries = client.ls(root, recursive=True, simple=False)
        except Exception:
            continue

        for item in entries:
            if item.get("isDir"):
                continue
            uri = str(item.get("uri", ""))
            if not uri.endswith(".md"):
                continue
            if "/." in uri:
                continue
            try:
                content = client.read(uri)
                snapshot[uri] = _hash_text(content)
            except Exception:
                snapshot[uri] = "<read-failed>"
    return snapshot


def _snapshot_diff(
    before: Dict[str, str],
    after: Dict[str, str],
) -> Tuple[List[str], List[str], List[str]]:
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    changed = sorted(uri for uri in (set(before) & set(after)) if before[uri] != after[uri])
    return created, deleted, changed


def _search_hits(client: SyncOpenViking, query: str, limit: int) -> List[Hit]:
    """Search both user/agent memory roots and merge hits by uri."""
    merged: Dict[str, Hit] = {}
    for target_uri in ["viking://user/memories", "viking://agent/memories"]:
        try:
            result = client.find(query, target_uri=target_uri, limit=limit)
        except Exception:
            continue

        for mem in _safe_list(getattr(result, "memories", [])):
            uri = getattr(mem, "uri", "") or ""
            if not uri:
                continue
            hit = Hit(
                query=query,
                uri=uri,
                abstract=getattr(mem, "abstract", "") or "",
                content="",
                score=_safe_float(getattr(mem, "score", None)),
                target_uri=target_uri,
            )
            try:
                hit.content = client.read(uri)
            except Exception:
                hit.content = ""
            old = merged.get(uri)
            if old is None or (hit.score or -1.0) > (old.score or -1.0):
                merged[uri] = hit

    return sorted(merged.values(), key=lambda item: item.score or -1.0, reverse=True)


def _format_hits(hits: List[Hit], max_items: int = 8) -> str:
    if not hits:
        return "(no hit)"
    lines: List[str] = []
    for idx, hit in enumerate(hits[:max_items], 1):
        score_text = "n/a" if hit.score is None else f"{hit.score:.4f}"
        content_preview = hit.content.replace("\n", " ").strip()
        if len(content_preview) > 120:
            content_preview = content_preview[:117] + "..."
        lines.append(
            f"{idx}. score={score_text} | {hit.abstract} | {hit.uri}\n   content={content_preview}"
        )
    return "\n".join(lines)


def _joined_hit_texts(hits: List[Hit]) -> List[str]:
    return [f"{hit.abstract} {hit.content} {hit.uri}".lower() for hit in hits]


def _group_satisfied_anywhere(group: List[str], texts: List[str]) -> bool:
    if not group:
        return True
    options = [opt.lower() for opt in group]
    return any(any(opt in text for text in texts) for opt in options)


def _group_fully_matched_in_single_hit(group: List[str], texts: List[str]) -> bool:
    if not group:
        return False
    options = [opt.lower() for opt in group]
    for text in texts:
        if all(opt in text for opt in options):
            return True
    return False


def _evaluate_query_check(check: QueryCheck, hits: List[Hit]) -> List[str]:
    reasons: List[str] = []
    texts = _joined_hit_texts(hits)

    if len(hits) < check.min_hits:
        reasons.append(f"query '{check.query}' hit count {len(hits)} < expected {check.min_hits}")

    for group in check.require_groups:
        if not _group_satisfied_anywhere(group, texts):
            reasons.append(f"query '{check.query}' missing required group: {' | '.join(group)}")

    for group in check.forbidden_groups:
        if _group_fully_matched_in_single_hit(group, texts):
            reasons.append(f"query '{check.query}' matched forbidden group: {' + '.join(group)}")
    return reasons


def _format_records(records: List[DedupRecord]) -> str:
    if not records:
        return "(no dedup record in this case)"
    lines: List[str] = []
    for idx, rec in enumerate(records, 1):
        lines.append(
            f"{idx}. session={rec.source_session} category={rec.category} "
            f"decision={rec.decision} abstract={rec.candidate_abstract}"
        )
        for action in rec.actions:
            lines.append(f"   - action={action['decision']} uri={action['uri']}")
    return "\n".join(lines)


def _build_cases() -> List[CaseSpec]:
    return [
        CaseSpec(
            case_id="T1",
            title="Profile - Basic Identity",
            turns=[
                Turn(
                    session_key="profile",
                    user_text="我叫张明，在字节跳动做后端开发，base北京。",
                )
            ],
            checks=[
                QueryCheck(
                    query="张明是谁",
                    require_groups=[["张明"], ["字节跳动"], ["后端"], ["北京"]],
                )
            ],
        ),
        CaseSpec(
            case_id="T2",
            title="Profile - Incremental Update (Merge)",
            turns=[
                Turn(session_key="profile", user_text="我叫张明，做后端开发。"),
                Turn(session_key="profile", user_text="最近转岗了，现在做 infra。"),
            ],
            checks=[
                QueryCheck(
                    query="张明做什么工作",
                    require_groups=[["张明"], ["infra", "基础设施", "基础架构"]],
                )
            ],
            max_created_files=0,
        ),
        CaseSpec(
            case_id="T3",
            title="Preferences",
            turns=[
                Turn(
                    session_key="prefs",
                    user_text=(
                        "写代码的时候我习惯用 vim + tmux，不喜欢 IDE。"
                        "回复我的时候用中文就好，技术术语保持英文。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="用户开发工具偏好",
                    require_groups=[["vim"], ["tmux"], ["ide"]],
                ),
                QueryCheck(
                    query="回复语言偏好",
                    require_groups=[["中文"], ["english", "英文"]],
                ),
            ],
        ),
        CaseSpec(
            case_id="T4",
            title="Entities - People and Projects",
            turns=[
                Turn(
                    session_key="entities",
                    user_text=(
                        "我们组的 tech lead 是 Kevin，他主推用 Go 重写网关。"
                        "目前在做 Project Atlas，是一个内部 API 网关平台。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="Kevin",
                    require_groups=[["kevin"], ["tech lead", "技术负责人"], ["go"]],
                ),
                QueryCheck(
                    query="Project Atlas",
                    require_groups=[["atlas"], ["api"], ["网关", "gateway"]],
                ),
            ],
        ),
        CaseSpec(
            case_id="T5",
            title="Events - Decision Point",
            turns=[
                Turn(
                    session_key="events",
                    user_text=(
                        "今天和老板聊了，决定放弃 Python 方案，全面转 Go。"
                        "主要原因是性能瓶颈和团队技术栈统一。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="为什么选 Go",
                    require_groups=[
                        ["go"],
                        ["python"],
                        ["性能", "performance"],
                        ["技术栈", "stack"],
                    ],
                )
            ],
        ),
        CaseSpec(
            case_id="T6",
            title="Cases - Problem to Solution",
            turns=[
                Turn(
                    session_key="cases",
                    user_text=(
                        "我们的 gRPC 服务偶尔出现 deadline exceeded，大概每天几十次。"
                        "查了 trace 发现是下游 Redis 偶尔 latency spike。"
                        "试了连接池调大没用，最后发现是 Redis cluster 有个慢节点。"
                        "把那个节点摘掉换了新实例就好了。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="gRPC deadline exceeded 怎么解决",
                    require_groups=[
                        ["grpc"],
                        ["deadline exceeded"],
                        ["redis"],
                        ["慢节点", "slow node"],
                        ["替换", "换了", "replace", "摘掉", "摘除", "更换", "新实例"],
                    ],
                )
            ],
        ),
        CaseSpec(
            case_id="T7",
            title="Patterns - Reusable Practice",
            turns=[
                Turn(
                    session_key="patterns",
                    user_text=(
                        "我发现做 code review 有个好办法。"
                        "先看测试理解意图，再看 diff，最后跑一遍确认。"
                        "这样比直接看 diff 效率高很多，漏的也少。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="code review 方法",
                    require_groups=[
                        ["code review", "代码评审"],
                        ["测试", "test"],
                        ["diff"],
                        ["运行", "确认", "run"],
                    ],
                )
            ],
        ),
        CaseSpec(
            case_id="T8",
            title="Patterns - Merge Existing Across Sessions",
            turns=[
                Turn(session_key="t8_a", user_text="部署前一定要先跑 smoke test。"),
                Turn(
                    session_key="t8_b",
                    user_text="部署前除了 smoke test，还要检查 config diff。",
                ),
            ],
            checks=[
                QueryCheck(
                    query="部署前检查",
                    require_groups=[["smoke"], ["config diff", "配置", "config"]],
                )
            ],
            expect_merge_action=True,
            expect_none_decision=True,
            expect_merge_from_session_key="t8_b",
        ),
        CaseSpec(
            case_id="T9",
            title="Complex Multi-Round - Mixed Categories",
            turns=[
                Turn(
                    session_key="mixed",
                    user_text=(
                        "我在做一个 RAG 系统的 chunk 策略优化。"
                        "现在用的是固定 512 token 切分，效果不好。"
                        "我试了 semantic chunking，用 embedding similarity 找分割点。"
                        "同事 Lisa 建议试 late chunking，她在另一个项目上效果不错。"
                        "最后我们决定用 semantic chunking + overlap 50 token 的方案。"
                        "关键 insight 是：chunk boundary 要对齐语义边界，不能硬切。"
                        "以后做 RAG 都应该先评估 chunk 质量再调 retrieval。"
                    ),
                )
            ],
            checks=[
                QueryCheck(
                    query="RAG chunking 怎么做",
                    require_groups=[
                        [
                            "semantic",
                            "semantic chunking",
                            "语义",
                            "语义切分",
                            "embedding",
                            "相似度",
                        ],
                        ["overlap", "50"],
                        ["512"],
                    ],
                ),
                QueryCheck(
                    query="Lisa",
                    require_groups=[["lisa"], ["late chunking", "chunking"]],
                ),
                QueryCheck(
                    query="chunk 优化经验",
                    require_groups=[
                        ["chunk"],
                        ["质量", "quality", "边界", "semantic"],
                        ["retrieval", "检索", "调优", "优化流程"],
                    ],
                ),
            ],
            expected_categories=set(),
        ),
        CaseSpec(
            case_id="T10",
            title="Noise Resistance - Should Not Store",
            turns=[
                Turn(
                    session_key="noise",
                    user_text="今天天气不错。帮我写个 hello world。谢谢，挺好的。",
                )
            ],
            checks=[
                QueryCheck(
                    query="天气",
                    forbidden_groups=[["天气"]],
                    min_hits=0,
                ),
                QueryCheck(
                    query="hello world",
                    forbidden_groups=[["hello", "world"]],
                    min_hits=0,
                ),
            ],
        ),
    ]


def _get_or_create_session(
    client: SyncOpenViking,
    cache: Dict[str, Any],
    key: str,
) -> Any:
    sess = cache.get(key)
    if sess is not None:
        return sess
    session_id = client.create_session()["session_id"]
    sess = client.session(session_id)
    cache[key] = sess
    cache[f"__id__{key}"] = session_id
    return sess


def _session_id(cache: Dict[str, Any], key: str) -> str:
    return str(cache.get(f"__id__{key}", ""))


def _evaluate_case(
    case: CaseSpec,
    hits_by_query: Dict[str, List[Hit]],
    records: List[DedupRecord],
    created: List[str],
    deleted: List[str],
    changed: List[str],
    session_cache: Dict[str, Any],
) -> List[str]:
    reasons: List[str] = []

    for check in case.checks:
        reasons.extend(_evaluate_query_check(check, hits_by_query.get(check.query, [])))

    if case.expected_categories:
        observed_categories = {record.category for record in records}
        missing_categories = sorted(case.expected_categories - observed_categories)
        if missing_categories:
            reasons.append(
                "missing expected categories in dedup records: " + ", ".join(missing_categories)
            )

    if case.expect_merge_action and not any(
        action.get("decision") == "merge" for record in records for action in record.actions
    ):
        reasons.append("expected merge action, but none observed")

    if case.expect_delete_action and not any(
        action.get("decision") == "delete" for record in records for action in record.actions
    ):
        reasons.append("expected delete action, but none observed")

    if case.expect_skip_decision and not any(record.decision == "skip" for record in records):
        reasons.append("expected decision=skip, but not observed")

    if case.expect_none_decision and not any(record.decision == "none" for record in records):
        reasons.append("expected decision=none, but not observed")

    if case.expect_merge_from_session_key:
        expected_sid = _session_id(session_cache, case.expect_merge_from_session_key)
        if expected_sid:
            merge_from_expected = any(
                record.source_session == expected_sid
                and any(action.get("decision") == "merge" for action in record.actions)
                for record in records
            )
            if not merge_from_expected:
                reasons.append(
                    "expected merge from session "
                    + case.expect_merge_from_session_key
                    + ", but not observed"
                )

    if case.expect_no_new_memory and (created or deleted or changed):
        reasons.append(
            "expected no memory mutation, but snapshot changed "
            + f"(created={len(created)} deleted={len(deleted)} changed={len(changed)})"
        )

    if case.max_created_files is not None and len(created) > case.max_created_files:
        reasons.append(f"created file count {len(created)} exceeds max {case.max_created_files}")

    return reasons


def _decision_coverage(records: List[DedupRecord]) -> Dict[str, bool]:
    return {
        "merge_action": any(
            action.get("decision") == "merge" for record in records for action in record.actions
        ),
        "delete_action": any(
            action.get("decision") == "delete" for record in records for action in record.actions
        ),
        "decision_none": any(record.decision == "none" for record in records),
        "decision_skip": any(record.decision == "skip" for record in records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenViking memory skill T1-T10 test demo")
    parser.add_argument(
        "--path",
        default="./ov_data_memory_test_demo",
        help="Demo storage path. This script clears it at startup.",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=60.0,
        help="Queue wait timeout in seconds.",
    )
    parser.add_argument("--limit", type=int, default=8, help="Top-k retrieval limit per query.")
    parser.add_argument(
        "--json-report",
        default="",
        help="Optional output path for a JSON report.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
        help="Print per-case trace logs.",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENVIKING_CONFIG_FILE"):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cfg = os.path.join(repo_root, "ov.conf")
        if os.path.exists(cfg):
            os.environ["OPENVIKING_CONFIG_FILE"] = cfg

    data_path = Path(args.path)
    if data_path.exists():
        shutil.rmtree(data_path)
    data_path.mkdir(parents=True, exist_ok=True)

    recorder = DedupRecorder()
    recorder.install()

    client = SyncOpenViking(path=str(data_path))
    client.initialize()

    cases = _build_cases()
    session_cache: Dict[str, Any] = {}
    case_results: List[CaseResult] = []

    try:
        client.is_healthy()

        for case in cases:
            before_snapshot = _collect_memory_snapshot(client)
            record_start = len(recorder.records)
            commit_results: List[Dict[str, Any]] = []

            for turn in case.turns:
                session = _get_or_create_session(client, session_cache, turn.session_key)
                recorder.current_round = f"{case.case_id}:{case.title}"
                session.add_message("user", parts=[TextPart(text=turn.user_text)])
                session.add_message("assistant", parts=[TextPart(text=turn.assistant_text)])
                commit_results.append(session.commit())
                try:
                    client.wait_processed(timeout=args.wait_timeout)
                except Exception:
                    pass

            after_snapshot = _collect_memory_snapshot(client)
            created, deleted, changed = _snapshot_diff(before_snapshot, after_snapshot)
            case_records = recorder.records[record_start:]

            hits_by_query: Dict[str, List[Hit]] = {}
            for check in case.checks:
                hits_by_query[check.query] = _search_hits(client, check.query, args.limit)

            reasons = _evaluate_case(
                case,
                hits_by_query,
                case_records,
                created,
                deleted,
                changed,
                session_cache,
            )
            passed = len(reasons) == 0
            case_results.append(
                CaseResult(
                    case_id=case.case_id,
                    title=case.title,
                    passed=passed,
                    reasons=reasons,
                    created=created,
                    deleted=deleted,
                    changed=changed,
                )
            )

            if args.verbose:
                session_lines = [
                    f"session_key={turn.session_key} session_id={_session_id(session_cache, turn.session_key)}"
                    for turn in case.turns
                ]
                _print_section(
                    f"{case.case_id} {case.title} - commits",
                    body="\n".join(session_lines + [f"commit={item}" for item in commit_results]),
                )
                _print_section(f"{case.case_id} dedup trace", body=_format_records(case_records))
                _print_section(
                    f"{case.case_id} memory diff",
                    body="\n".join(
                        [f"created={len(created)} deleted={len(deleted)} changed={len(changed)}"]
                        + [f"+ {uri}" for uri in created]
                        + [f"- {uri}" for uri in deleted]
                        + [f"~ {uri}" for uri in changed]
                    ),
                )
                for query, hits in hits_by_query.items():
                    _print_section(f"{case.case_id} find: {query}", body=_format_hits(hits))
                _print_section(
                    f"{case.case_id} result: {'PASS' if passed else 'FAIL'}",
                    body=(
                        "All checks passed"
                        if passed
                        else "\n".join(f"- {item}" for item in reasons)
                    ),
                )

        passed_count = sum(1 for item in case_results if item.passed)
        failed_count = len(case_results) - passed_count
        coverage = _decision_coverage(recorder.records)

        summary_lines = [
            f"Total: {len(case_results)}",
            f"Passed: {passed_count}",
            f"Failed: {failed_count}",
            "",
            "Decision coverage:",
            f"- merge_action: {'YES' if coverage['merge_action'] else 'NO'}",
            f"- delete_action: {'YES' if coverage['delete_action'] else 'NO'}",
            f"- decision_none: {'YES' if coverage['decision_none'] else 'NO'}",
            f"- decision_skip: {'YES' if coverage['decision_skip'] else 'NO'}",
        ]

        failed_cases = [item for item in case_results if not item.passed]
        if failed_cases:
            summary_lines.append("")
            summary_lines.append("Failed cases:")
            for item in failed_cases:
                summary_lines.append(f"- {item.case_id} {item.title}")
                for reason in item.reasons:
                    summary_lines.append(f"  * {reason}")

        _print_section("Final Report", body="\n".join(summary_lines))

        if args.json_report:
            report = {
                "summary": {
                    "total": len(case_results),
                    "passed": passed_count,
                    "failed": failed_count,
                    "coverage": coverage,
                },
                "cases": [asdict(item) for item in case_results],
            }
            report_path = Path(args.json_report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _print_section("JSON report", body=str(report_path))

        return 0 if failed_count == 0 else 1
    finally:
        recorder.uninstall()
        try:
            client.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
