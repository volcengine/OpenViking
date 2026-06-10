import argparse
import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from openviking_cli.client.sync_http import SyncHTTPClient

try:
    from benchmark.locomo.openviking.locomo_prompts import get_answer_generation_prompt
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from locomo_prompts import get_answer_generation_prompt


def get_evidence_text(evidence_list: list, sample: dict) -> list[str]:
    """根据 evidence 列表获取原始对话文本

    evidence 格式: ['D1:3', 'D2:5'] -> session_1 第3条, session_2 第5条
    """
    if not evidence_list:
        return []

    conv = sample.get("conversation", {})
    results = []

    for ev in evidence_list:
        # 解析 D1:3 -> session_1, index 2
        try:
            parts = ev.split(":")
            session_num = int(parts[0][1:])  # D1 -> 1
            msg_index = int(parts[1]) - 1  # 3 -> index 2

            session_key = f"session_{session_num}"
            session_messages = conv.get(session_key, [])

            if msg_index < len(session_messages):
                msg = session_messages[msg_index]
                text = msg.get("text", "")
                speaker = msg.get("speaker", "")
                results.append(f"{speaker}: {text}")
            else:
                results.append(f"[{ev}: out of range]")
        except (ValueError, IndexError):
            results.append(f"[{ev}: invalid format]")

    return results


def parse_locomo_datetime(date_str: str) -> datetime | None:
    """解析 LoCoMo 时间格式，如 '1:56 pm on 8 May, 2023'"""
    try:
        # 移除时间部分，只保留日期 "8 May, 2023"
        if " on " in date_str:
            date_part = date_str.split(" on ")[-1]
            return datetime.strptime(date_part.strip(), "%d %B, %Y")
    except ValueError:
        pass
    return None


def get_sample_question_time(sample: dict) -> str | None:
    """从 sample 的 conversation 中提取最后一个有内容 session 的时间，返回 ISO 格式日期"""
    conversation = sample.get("conversation", {})

    # 找所有 session_N 字段（非 date_time）
    session_keys = [
        k for k in conversation.keys() if k.startswith("session_") and "date_time" not in k
    ]
    if not session_keys:
        return None

    # 按 session 编号排序，找到最后一个有内容的
    def get_session_num(key):
        try:
            return int(key.replace("session_", ""))
        except ValueError:
            return 0

    session_keys.sort(key=get_session_num, reverse=True)

    for session_key in session_keys:
        if conversation.get(session_key):  # 有内容
            # 找到对应的 date_time
            session_num = get_session_num(session_key)
            dt_key = f"session_{session_num}_date_time"
            date_str = conversation.get(dt_key)
            if date_str:
                dt = parse_locomo_datetime(date_str)
                if dt:
                    return dt.strftime("%Y-%m-%d")

    return None


def load_csv_qa(
    input_path: str, count: int | None = None, default_time: str | None = None
) -> list[dict]:
    """从CSV文件加载QA数据，取sample_id和question字段"""
    qa_list = []
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qa_list.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "category": "",
                    "evidence": [],
                    "question_time": default_time,
                }
            )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def load_locomo_qa(
    input_path: str,
    sample_index: int | None = None,
    count: int | None = None,
    default_time: str | None = None,
    question_index: int | None = None,
    invalid_questions: set | None = None,
    sample_indices: list[int] | None = None,
) -> list[dict]:
    """加载LoCoMo数据集的QA部分，支持JSON和CSV格式

    Args:
        invalid_questions: 无效题目问题内容集合，用于标记无效题目
    """
    if input_path.lower().endswith(".csv"):
        return load_csv_qa(input_path, count, default_time)

    # 原有JSON格式处理逻辑
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def normalize_sample(item: dict, index: int) -> dict:
        normalized = dict(item)
        normalized["original_sample_id"] = item.get("sample_id", "")
        normalized["sample_id"] = f"sample_{index}"
        return normalized

    def parse_sample_index(raw_sample_index: str | int) -> int:
        try:
            sample_index_text = str(raw_sample_index)
            if sample_index_text.startswith("sample_"):
                sample_index_text = sample_index_text.removeprefix("sample_")
            idx = int(sample_index_text)
        except ValueError as exc:
            raise ValueError(
                f"sample '{raw_sample_index}' is invalid; use a numeric index or sample_{{idx}}"
            ) from exc
        if idx < 0 or idx >= len(data):
            raise ValueError(f"sample index {idx} out of range (0-{len(data) - 1})")
        return idx

    qa_list = []
    # 支持数字索引或 sample_id (如 "sample_26")
    if sample_indices is not None:
        validated_indices = [parse_sample_index(idx) for idx in sample_indices]
        samples = [normalize_sample(data[idx], idx) for idx in validated_indices]
    elif sample_index is not None:
        idx = parse_sample_index(sample_index)
        samples = [normalize_sample(data[idx], idx)]
    else:
        samples = [normalize_sample(sample, idx) for idx, sample in enumerate(data)]

    for sample in samples:
        sample_id = sample.get("sample_id", "")
        question_time = get_sample_question_time(sample)
        qa_items = sample.get("qa", [])

        # 如果指定了 question_index，只返回那一个问题
        if question_index is not None:
            if question_index < 0 or question_index >= len(qa_items):
                raise ValueError(
                    f"question index {question_index} out of range (0-{len(qa_items) - 1})"
                )
            qa = qa_items[question_index]
            if qa.get("category") == 5:
                continue
            evidence_list = qa.get("evidence", [])
            question_id = f"{sample_id}_qa{question_index}"
            qa_list.append(
                {
                    "sample_id": sample_id,
                    "question_id": question_id,
                    "question_index": question_index,
                    "question": qa["question"],
                    "answer": qa["answer"],
                    "category": qa.get("category", ""),
                    "evidence": evidence_list,
                    "evidence_text": get_evidence_text(evidence_list, sample),
                    "question_time": question_time,
                    "is_invalid": qa["question"] in invalid_questions
                    if invalid_questions
                    else False,
                }
            )
        else:
            for q_idx, qa in enumerate(qa_items):
                if qa.get("category") == 5:
                    continue
                evidence_list = qa.get("evidence", [])
                question_id = f"{sample_id}_qa{q_idx}"
                qa_list.append(
                    {
                        "sample_id": sample_id,
                        "question_id": question_id,
                        "question_index": q_idx,
                        "question": qa["question"],
                        "answer": qa["answer"],
                        "category": qa.get("category", ""),
                        "evidence": evidence_list,
                        "evidence_text": get_evidence_text(evidence_list, sample),
                        "question_time": question_time,
                        "is_invalid": qa["question"] in invalid_questions
                        if invalid_questions
                        else False,
                    }
                )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT = 10
DEFAULT_SINGLE_SEARCH_RERANK_LIMIT = 10
DEFAULT_SINGLE_SEARCH_MAX_CONTEXT_CHARS = 4000
SINGLE_SEARCH_EXCLUDED_BASENAMES = {".abstract.md", ".overview.md"}


def get_token_encoding_name(model_name: str | None = None) -> str:
    try:
        import tiktoken

        if model_name:
            candidates = [model_name]
            if "/" in model_name:
                candidates.append(model_name.rsplit("/", 1)[-1])
            for candidate in candidates:
                try:
                    return tiktoken.encoding_for_model(candidate).name
                except KeyError:
                    continue
    except Exception:
        pass

    normalized = (model_name or "").lower()
    if normalized.startswith(("gpt-4o", "gpt-5", "o1", "o3", "o4")):
        return "o200k_base"
    if normalized.startswith(("gpt-4", "gpt-3.5")):
        return "cl100k_base"
    return "approx_chars_div_4"


def count_text_tokens(text: str, model_name: str | None = None) -> int:
    encoding_name = get_token_encoding_name(model_name)
    if encoding_name == "approx_chars_div_4":
        return max(0, len(text or "") // 4)

    try:
        import tiktoken

        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text or ""))
    except Exception:
        return max(0, len(text or "") // 4)


def _iter_search_contexts(search_result: Any) -> list[Any]:
    if search_result is None:
        return []
    if isinstance(search_result, list):
        return search_result

    contexts = []
    for attr in ("memories", "resources", "skills"):
        contexts.extend(getattr(search_result, attr, []) or [])
    if contexts:
        return contexts

    try:
        return list(search_result)
    except TypeError:
        return []


def is_single_search_excluded_uri(uri: str) -> bool:
    basename = str(uri or "").rstrip("/").rsplit("/", 1)[-1]
    return basename in SINGLE_SEARCH_EXCLUDED_BASENAMES


def select_single_search_contexts(
    search_result: Any,
    limit: int = DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT,
) -> list[dict[str, Any]]:
    selected = []
    for raw_rank, context in enumerate(_iter_search_contexts(search_result), start=1):
        if raw_rank > limit:
            break
        uri = getattr(context, "uri", "")
        if not uri or is_single_search_excluded_uri(uri):
            continue
        selected.append(
            {
                "raw_rank": raw_rank,
                "uri": uri,
                "score": getattr(context, "score", 0.0),
                "abstract": getattr(context, "abstract", ""),
                "created_at": getattr(context, "created_at", ""),
            }
        )
    return selected


def build_single_search_context_prompt(
    question: str,
    question_time: str | None,
    contexts: list[dict[str, Any]],
) -> str:
    search_results = build_single_search_prompt_search_results(contexts)
    return get_answer_generation_prompt(
        question=question,
        search_results=search_results,
        reference_date=question_time or "2023",
    )


def build_single_search_prompt_search_results(
    contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "memory": str(context.get("content", "")),
            "score": context.get("score", 0.0),
            "raw_rank": context.get("raw_rank"),
            "created_at": context.get("created_at", ""),
        }
        for context in contexts
    ]


def count_retrieved_memory_content_tokens(
    contexts: list[dict[str, Any]],
    model_name: str | None = None,
) -> tuple[int, int, str]:
    memory_texts = [str(context.get("content", "")) for context in contexts]
    tokenizer = get_token_encoding_name(model_name)
    return (
        sum(count_text_tokens(memory_text, model_name) for memory_text in memory_texts),
        sum(len(memory_text) for memory_text in memory_texts),
        tokenizer,
    )


def filter_contexts_by_char_budget(
    contexts: list[dict[str, Any]],
    max_chars: int = DEFAULT_SINGLE_SEARCH_MAX_CONTEXT_CHARS,
) -> tuple[list[dict[str, Any]], list[str], int]:
    if max_chars <= 0:
        return contexts, [], sum(len(str(context.get("content", ""))) for context in contexts)

    selected = []
    skipped_uris = []
    total_chars = 0
    for context in contexts:
        content = str(context.get("content", "") or "")
        content_chars = len(content)
        if total_chars + content_chars > max_chars:
            skipped_uris.append(str(context.get("uri", "")))
            continue
        selected.append(context)
        total_chars += content_chars
    return selected, skipped_uris, total_chars


def build_single_search_reranker() -> Any | None:
    from openviking.models.rerank import RerankClient
    from openviking_cli.utils.config import get_openviking_config

    rerank_config = get_openviking_config().rerank
    if not rerank_config or not rerank_config.is_available():
        return None
    return RerankClient.from_config(rerank_config)


def build_single_search_vlm() -> Any:
    from openviking.models.vlm import VLMFactory
    from openviking_cli.utils.config import get_openviking_config

    vlm_config = get_openviking_config().vlm
    return VLMFactory.create(vlm_config._build_vlm_config_dict())


def _single_search_rerank_document(context: dict[str, Any]) -> str:
    content = str(context.get("content", "") or "")
    if content and not content.startswith("[READ ERROR]"):
        return content
    return str(context.get("abstract", "") or "")


def rerank_single_search_contexts(
    question: str,
    contexts: list[dict[str, Any]],
    reranker: Any | None,
    limit: int = DEFAULT_SINGLE_SEARCH_RERANK_LIMIT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not reranker or len(contexts) <= 1:
        return contexts, [], ""

    documents = [_single_search_rerank_document(context) for context in contexts]
    try:
        scores = reranker.rerank_batch(question, documents)
    except Exception as exc:
        return contexts, [], f"{type(exc).__name__}: {exc}"

    if not scores or len(scores) != len(contexts):
        return (
            contexts,
            [],
            (f"invalid_score_count: expected {len(contexts)}, got {len(scores) if scores else 0}"),
        )

    ranked_contexts = []
    rerank_scores = []
    for context, score in zip(contexts, scores, strict=True):
        if not isinstance(score, (int, float)):
            return contexts, [], f"invalid_score_type: {type(score).__name__}"
        next_context = dict(context)
        next_context["rerank_score"] = float(score)
        ranked_contexts.append(next_context)
        rerank_scores.append(
            {
                "uri": str(context.get("uri", "")),
                "score": float(score),
                "raw_rank": context.get("raw_rank"),
            }
        )

    ranked_contexts.sort(
        key=lambda context: (
            context.get("rerank_score", 0.0),
            -int(context.get("raw_rank", 0) or 0),
        ),
        reverse=True,
    )
    selected_contexts = ranked_contexts[: max(1, limit)]
    return selected_contexts, rerank_scores, ""


def _response_text(response: Any) -> str:
    if hasattr(response, "content"):
        return str(response.content or "").strip()
    return str(response).strip()


def _token_usage_from_vlm(vlm: Any, response: Any = None) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict) and usage:
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
    if usage:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": int(
                getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0
            ),
        }

    if hasattr(vlm, "get_token_usage_summary"):
        summary = vlm.get_token_usage_summary()
        return {
            "prompt_tokens": int(summary.get("total_prompt_tokens", 0) or 0),
            "completion_tokens": int(summary.get("total_completion_tokens", 0) or 0),
            "total_tokens": int(summary.get("total_tokens", 0) or 0),
        }
    if hasattr(vlm, "get_token_usage"):
        usage_snapshot = vlm.get_token_usage()
        total_usage = usage_snapshot.get("total_usage", {}) if usage_snapshot else {}
        return {
            "prompt_tokens": int(total_usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(total_usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(total_usage.get("total_tokens", 0) or 0),
        }

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def run_vikingbot_chat(
    question: str,
    question_time: str | None = None,
    sample_id: str | None = None,
    question_id: str | None = None,
    openviking_url: str | None = None,
    single_search_context_limit: int = DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT,
    single_search_rerank_limit: int = DEFAULT_SINGLE_SEARCH_RERANK_LIMIT,
    timeout: int = 300,
    single_search_max_context_chars: int = DEFAULT_SINGLE_SEARCH_MAX_CONTEXT_CHARS,
) -> tuple[str, dict, float, int, list, list, str]:
    """执行单轮 search + rerank + answer，返回回答、token、耗时、迭代次数、工具和检索轨迹"""
    start_time = time.time()
    client = SyncHTTPClient(
        url=openviking_url,
        user=sample_id,
        timeout=timeout,
    )
    try:
        client.initialize()
        target_uri = f"viking://user/{sample_id or 'default'}/memories"
        search_result = client.find(
            question,
            target_uri=target_uri,
            limit=single_search_context_limit,
        )
        contexts = select_single_search_contexts(
            search_result,
            limit=single_search_context_limit,
        )
        retrieved_uris = [context["uri"] for context in contexts]

        for context in contexts:
            uri = context["uri"]
            try:
                context["content"] = client.read(uri, offset=0, limit=-1)
            except Exception as exc:
                context["content"] = f"[READ ERROR] {exc}"

        reranker = build_single_search_reranker()
        rerank_enabled = reranker is not None
        rerank_limit = single_search_rerank_limit
        contexts, rerank_scores, rerank_error = rerank_single_search_contexts(
            question=question,
            contexts=contexts,
            reranker=reranker,
            limit=rerank_limit,
        )
        contexts, skipped_context_uris, context_chars = filter_contexts_by_char_budget(
            contexts,
            max_chars=single_search_max_context_chars,
        )
        context_uris = [context["uri"] for context in contexts]

        prompt = build_single_search_context_prompt(question, question_time, contexts)
        vlm = build_single_search_vlm()
        memory_prompt_tokens, memory_chars, memory_tokenizer = (
            count_retrieved_memory_content_tokens(
                contexts,
                model_name=getattr(vlm, "model", None),
            )
        )
        raw_response = vlm.get_completion(prompt)
        response = _response_text(raw_response)
        token_usage = _token_usage_from_vlm(vlm, raw_response)
        token_usage["memory_prompt_tokens"] = memory_prompt_tokens
        token_usage["memory_chars"] = memory_chars
        token_usage["memory_tokenizer"] = memory_tokenizer
        time_cost = time.time() - start_time
        return (
            response,
            token_usage,
            time_cost,
            1,
            ["single_search", "read", "context_answer"],
            [
                {
                    "iteration": 1,
                    "retrieved_uris": retrieved_uris,
                    "context_uris": context_uris,
                    "rerank_enabled": rerank_enabled,
                    "rerank_limit": rerank_limit if rerank_enabled else 0,
                    "rerank_scores": rerank_scores,
                    "rerank_error": rerank_error,
                    "max_context_chars": single_search_max_context_chars,
                    "context_chars": context_chars,
                    "skipped_context_uris_by_char_limit": skipped_context_uris,
                }
            ],
            prompt,
        )
    except Exception as e:
        return (
            f"[SINGLE SEARCH ERROR] {str(e)}",
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "memory_prompt_tokens": 0,
                "memory_chars": 0,
                "memory_tokenizer": "approx_chars_div_4",
            },
            0,
            1,
            [],
            [
                {
                    "iteration": 1,
                    "retrieved_uris": [],
                    "context_uris": [],
                    "rerank_enabled": False,
                    "rerank_limit": 0,
                    "rerank_scores": [],
                }
            ],
            "",
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


def load_processed_questions(output_path: str) -> set:
    """加载已处理的问题集合（已禁用，每次重新运行）"""
    # 注意：去重逻辑已禁用，每次运行都会重新执行所有问题
    return set()


def result_row_key(row: dict[str, Any]) -> str:
    question_id = str(row.get("question_id", "") or "").strip()
    if question_id:
        return f"question_id:{question_id}"

    sample_id = str(row.get("sample_id", "") or "").strip()
    question_index = str(row.get("question_index", "") or "").strip()
    if sample_id or question_index:
        return f"sample_question_index:{sample_id}:{question_index}"

    return f"question:{str(row.get('question', '') or '').strip()}"


def parse_sample_indices(
    raw_samples: str | None, dataset_size: int | None = None
) -> list[int] | None:
    if raw_samples is None:
        return None

    indices: list[int] = []
    seen: set[int] = set()
    for raw_part in raw_samples.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("sample_"):
            part = part.removeprefix("sample_")
        try:
            idx = int(part)
        except ValueError as exc:
            raise ValueError(
                f"sample '{raw_part}' is invalid; use numeric indices or sample_{{idx}}"
            ) from exc
        if idx < 0:
            raise ValueError(f"sample index {idx} must be >= 0")
        if dataset_size is not None and idx >= dataset_size:
            raise ValueError(f"sample index {idx} out of range (0-{dataset_size - 1})")
        if idx not in seen:
            indices.append(idx)
            seen.add(idx)

    if not indices:
        raise ValueError("--samples must include at least one sample index")
    return indices


def main():
    # 基于脚本所在目录计算默认数据文件路径
    script_dir = Path(__file__).parent.resolve()
    default_input = str(script_dir / ".." / "data" / "locomo10.json")
    default_errors = str(script_dir / ".." / "data" / "errors.json")

    parser = argparse.ArgumentParser(description="VikingBot QA evaluation script")
    parser.add_argument(
        "input",
        nargs="?",
        default=default_input,
        help="Path to locomo10.json file",
    )
    parser.add_argument(
        "--output",
        default="./result/locomo_qa_result.csv",
        help="Path to output csv file, default: ./result/locomo_qa_result.csv",
    )
    parser.add_argument(
        "--errors",
        default=default_errors,
        help="Path to invalid questions JSON file",
    )
    parser.add_argument(
        "--sample",
        type=str,
        default=None,
        help="LoCoMo sample index (0-based) or sample_id (e.g., sample_26)",
    )
    parser.add_argument(
        "--samples",
        type=str,
        default=None,
        help="Comma-separated LoCoMo sample indices, e.g. 0,1 or sample_0,sample_1",
    )
    parser.add_argument(
        "--question-index",
        type=int,
        default=None,
        help="Question index (0-based) for single question testing",
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Number of QA questions to run, default all"
    )
    parser.add_argument(
        "--threads", type=int, default=40, help="Number of concurrent threads, default: 40"
    )
    parser.add_argument(
        "--openviking-url",
        default=None,
        help="OpenViking server URL, e.g. http://127.0.0.1:1934. Defaults to ovcli.conf.",
    )
    parser.add_argument(
        "--single-search-context-limit",
        type=int,
        default=DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT,
        help=(
            "Number of memory files to read into the single-search prompt, "
            f"default: {DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT}"
        ),
    )
    parser.add_argument(
        "--single-search-rerank-limit",
        type=int,
        default=DEFAULT_SINGLE_SEARCH_RERANK_LIMIT,
        help=(
            "Number of reranked memory files to keep in the single-search prompt, "
            f"default: {DEFAULT_SINGLE_SEARCH_RERANK_LIMIT}"
        ),
    )
    parser.add_argument(
        "--single-search-max-context-chars",
        type=int,
        default=DEFAULT_SINGLE_SEARCH_MAX_CONTEXT_CHARS,
        help=(
            "Maximum total memory content characters to concatenate into the answer prompt. "
            "Files that would exceed the budget are skipped whole; <=0 disables the budget. "
            f"default: {DEFAULT_SINGLE_SEARCH_MAX_CONTEXT_CHARS}"
        ),
    )
    parser.add_argument(
        "--debug-print-model-input",
        action="store_true",
        help="Write full model input prompt and retrieved URI trace into the CSV.",
    )
    parser.add_argument(
        "--update-mode",
        action="store_true",
        help="Update mode: if output file exists, update matching question_id rows instead of overwriting",
    )
    args = parser.parse_args()

    # 如果指定了 question-index，单 sample/全量调试保持旧行为；多 samples 模式保留每个 sample 的这道题。
    if args.question_index is not None and args.count is None and args.samples is None:
        args.count = 1
    if args.sample is not None and args.samples is not None:
        raise ValueError("Use either --sample or --samples, not both")

    # 确保输出目录存在
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # 加载无效题目集合（按问题内容匹配，因为 errors.json 索引可能与数据不匹配）
    invalid_questions = set()
    errors_path = os.path.expanduser(args.errors)
    if os.path.exists(errors_path):
        with open(errors_path, "r", encoding="utf-8") as f:
            errors_data = json.load(f)
        # 按问题内容建立集合
        if errors_data and isinstance(errors_data[0], dict):
            invalid_questions = {item["question"] for item in errors_data}
        else:
            invalid_questions = set(errors_data)
        print(f"Loaded {len(invalid_questions)} invalid questions from {errors_path}")
    else:
        print(f"No errors file found at {errors_path}, is_invalid will be False for all questions")

    # 加载QA数据（所有题目，包括无效题目，只标记 is_invalid）
    qa_list = load_locomo_qa(
        args.input,
        args.sample,
        args.count,
        question_index=args.question_index,
        invalid_questions=invalid_questions,
        sample_indices=parse_sample_indices(args.samples),
    )
    total = len(qa_list)

    # 过滤掉 category=5 的问题
    qa_list = [qa for qa in qa_list if str(qa.get("category")) != "5"]
    print(f"Filtered to {len(qa_list)} questions after removing category=5")

    # 加载已处理的问题
    processed_questions = load_processed_questions(args.output)
    remaining = total - len(processed_questions)
    print(
        f"Loaded {total} QA questions, {len(processed_questions)} already processed, {remaining} remaining"
    )

    fieldnames = [
        "sample_id",
        "question_id",
        "question_index",
        "result",
        "is_invalid",
        "question",
        "answer",
        "category",
        "question_time",
        "evidence",
        "evidence_text",
        "response",
        "model_input_prompt",
        "token_usage",
        "memory_prompt_tokens",
        "memory_chars",
        "memory_tokenizer",
        "time_cost",
        "iteration",
        "tools_used_names",
        "retrieved_uris_by_iteration",
    ]

    existing_rows = []
    existing_fieldnames = fieldnames
    if args.update_mode and os.path.exists(args.output):
        with open(args.output, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            existing_fieldnames = reader.fieldnames or fieldnames
        for fieldname in fieldnames:
            if fieldname not in existing_fieldnames:
                existing_fieldnames.append(fieldname)
    elif os.path.exists(args.output):
        os.remove(args.output)

    # 创建线程锁，确保多线程写文件安全
    write_lock = threading.Lock()

    # 存储处理后的新行
    new_rows = []
    processed_count = 0

    def save_results_locked():
        temp_file = f"{args.output}.tmp"
        with open(temp_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=existing_fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)
        os.replace(temp_file, args.output)

    # 过滤掉已经处理过的问题
    remaining_qa = [qa for qa in qa_list if result_row_key(qa) not in processed_questions]
    remaining_count = len(remaining_qa)
    print(
        f"Starting evaluation with {args.threads} concurrent threads, {remaining_count} questions to process"
    )

    def process_qa(qa_item, idx, total_count):
        """单个QA处理函数，供多线程调用"""
        question = qa_item["question"]
        answer = qa_item["answer"]
        question_time = qa_item.get("question_time")
        # 使用 question_id 作为 session_id，实现完全独立并行
        sample_id = qa_item.get("sample_id")
        question_id = qa_item.get("question_id")
        print(f"Processing {idx}/{total_count}: {question[:60]}...")
        if question_time:
            print(f"  [time context: {question_time}]")

        (
            response,
            token_usage,
            time_cost,
            iteration,
            tools_used_names,
            retrieved_uris_by_iteration,
            model_input_prompt,
        ) = run_vikingbot_chat(
            question=question,
            question_time=question_time,
            sample_id=sample_id,
            question_id=question_id,
            openviking_url=args.openviking_url,
            single_search_context_limit=args.single_search_context_limit,
            single_search_rerank_limit=args.single_search_rerank_limit,
            single_search_max_context_chars=args.single_search_max_context_chars,
        )

        row = {
            "sample_id": qa_item["sample_id"],
            "question_id": question_id,
            "question_index": qa_item.get("question_index", ""),
            "result": "",
            "question": question,
            "answer": answer,
            "category": qa_item.get("category", ""),
            "question_time": question_time or "",
            "evidence": json.dumps(qa_item.get("evidence", [])),
            "evidence_text": json.dumps(qa_item.get("evidence_text", [])),
            "response": response,
            "model_input_prompt": model_input_prompt if args.debug_print_model_input else "",
            "token_usage": json.dumps(token_usage, ensure_ascii=False),
            "memory_prompt_tokens": token_usage.get("memory_prompt_tokens", 0),
            "memory_chars": token_usage.get("memory_chars", 0),
            "memory_tokenizer": token_usage.get("memory_tokenizer", ""),
            "time_cost": round(time_cost, 2),
            "iteration": iteration,
            "tools_used_names": json.dumps(tools_used_names, ensure_ascii=False),
            "retrieved_uris_by_iteration": json.dumps(
                retrieved_uris_by_iteration, ensure_ascii=False
            )
            if args.debug_print_model_input
            else "",
            "is_invalid": qa_item.get("is_invalid", False),
        }

        # 线程安全的结果收集
        with write_lock:
            nonlocal processed_count
            new_rows.append(row)
            row_key = result_row_key(row)
            found = False
            for existing_row in existing_rows:
                if result_row_key(existing_row) == row_key:
                    existing_row.update(row)
                    found = True
                    break
            if not found:
                existing_rows.append(row)
            processed_questions.add(row_key)
            processed_count += 1
            save_results_locked()
            print(f"Completed {processed_count}/{total_count}, time cost: {round(time_cost, 2)}s")
        return True

    # 使用线程池处理：全局并行，每个 question 独立 session
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        # 提交所有任务
        futures = []
        for idx, qa_item in enumerate(remaining_qa, 1):
            futures.append(executor.submit(process_qa, qa_item, idx, remaining_count))

        # 等待所有任务完成
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Error processing QA item: {str(e)}")

    if args.update_mode:
        print(f"Updated {len(new_rows)} rows in {args.output}")
    else:
        print(f"Evaluation completed, results saved to {args.output}")


if __name__ == "__main__":
    main()
