import argparse
import csv
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from openviking_cli.client.sync_http import SyncHTTPClient

try:
    from benchmark.longmemeval.vikingbot.longmemeval_prompts import (
        get_answer_generation_prompt,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from longmemeval_prompts import get_answer_generation_prompt


LONGMEMEVAL_TIME_FORMAT = "%Y/%m/%d (%a) %H:%M"
DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT = 10
DEFAULT_SINGLE_SEARCH_RERANK_LIMIT = 10
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


def parse_longmemeval_datetime(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str.strip(), LONGMEMEVAL_TIME_FORMAT)
    except ValueError:
        return None


def build_sample_agent_id(sample_id: str | int) -> str:
    """Return the agent_id used for one sample eval."""
    digest = hashlib.md5(str(sample_id).encode("utf-8")).hexdigest()[:12]
    return f"lm_{digest}"


def build_sample_user_id(sample_id: str | int) -> str:
    """Return the user_id used for one sample eval."""
    digest = hashlib.md5(f"user:{sample_id}".encode("utf-8")).hexdigest()[:12]
    return f"lm_user_{digest}"


def load_csv_qa(input_path: str, count: int | None = None) -> list[dict]:
    qa_list = []
    with open(input_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qa_list.append(
                {
                    "sample_id": row.get("sample_id", row.get("question_id", "")),
                    "question": row.get("question", ""),
                    "answer": row.get("answer", ""),
                    "question_type": row.get("question_type", ""),
                    "question_time": row.get("question_time", ""),
                }
            )
    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


def load_longmemeval_qa(
    input_path: str,
    sample_index: int | None = None,
    count: int | None = None,
) -> list[dict]:
    if input_path.lower().endswith(".csv"):
        return load_csv_qa(input_path, count)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if sample_index is not None:
        if sample_index < 0 or sample_index >= len(data):
            raise ValueError(f"sample index {sample_index} out of range (0-{len(data) - 1})")
        samples = [data[sample_index]]
    else:
        samples = data

    qa_list = []
    for sample in samples:
        question_dt = parse_longmemeval_datetime(sample.get("question_date", ""))
        question_time = question_dt.strftime("%Y-%m-%d") if question_dt else ""
        qa_list.append(
            {
                "sample_id": sample.get("question_id", ""),
                "question": sample.get("question", ""),
                "answer": sample.get("answer", ""),
                "question_type": sample.get("question_type", ""),
                "question_time": question_time,
            }
        )

    if count is not None:
        qa_list = qa_list[:count]
    return qa_list


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


def is_single_search_excluded_before_rerank_uri(uri: str) -> bool:
    return "/preferences/" in str(uri or "")


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
            }
        )
    return selected


def build_single_search_context_prompt(
    question: str,
    question_type: str | None,
    question_time: str | None,
    contexts: list[dict[str, Any]],
) -> str:
    search_results = build_single_search_prompt_search_results(contexts)

    prompt = get_answer_generation_prompt(
        question=question,
        search_results=search_results,
        question_date=question_time or "unknown",
    )
    if question_type:
        return f"Question Type: {question_type}\n\n{prompt}"
    return prompt


def build_single_search_prompt_search_results(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "memory": str(context.get("content", "")),
            "score": context.get("score", 0.0),
            "raw_rank": context.get("raw_rank"),
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


def build_single_search_reranker() -> Any | None:
    from openviking.models.rerank import RerankClient
    from openviking_cli.utils.config import get_openviking_config

    rerank_config = get_openviking_config().rerank
    if not rerank_config or not rerank_config.is_available():
        return None
    return RerankClient.from_config(rerank_config)


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not reranker or len(contexts) <= 1:
        return contexts, []

    documents = [_single_search_rerank_document(context) for context in contexts]
    try:
        scores = reranker.rerank_batch(question, documents)
    except Exception:
        return contexts, []

    if not scores or len(scores) != len(contexts):
        return contexts, []

    ranked_contexts = []
    rerank_scores = []
    for context, score in zip(contexts, scores, strict=True):
        if not isinstance(score, (int, float)):
            return contexts, []
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
    return selected_contexts, rerank_scores


def build_single_search_vlm() -> Any:
    from openviking.models.vlm import VLMFactory
    from openviking_cli.utils.config import get_openviking_config

    vlm_config = get_openviking_config().vlm
    return VLMFactory.create(vlm_config._build_vlm_config_dict())


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

    if hasattr(vlm, "get_token_usage_summary"):
        summary = vlm.get_token_usage_summary()
        return {
            "prompt_tokens": int(summary.get("total_prompt_tokens", 0) or 0),
            "completion_tokens": int(summary.get("total_completion_tokens", 0) or 0),
            "total_tokens": int(summary.get("total_tokens", 0) or 0),
        }

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def run_single_search_context_answer(
    question: str,
    question_type: str | None = None,
    question_time: str | None = None,
    sender_id: str | None = None,
    session_id: str | None = None,
    timeout: int = 300,
    single_search_context_limit: int = DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT,
    debug_print_model_input: bool = False,
) -> tuple[str, dict, float, int, list, list, str]:
    start_time = time.time()
    client = SyncHTTPClient(
        agent_id=session_id,
        user=sender_id,
        timeout=timeout,
    )
    try:
        client.initialize()
        target_uri = f"viking://user/{sender_id or 'default'}/memories"
        search_result = client.find(
            question,
            target_uri=target_uri,
            limit=single_search_context_limit,
        )
        contexts = select_single_search_contexts(
            search_result,
            limit=single_search_context_limit,
        )
        contexts = [
            context
            for context in contexts
            if not is_single_search_excluded_before_rerank_uri(context.get("uri", ""))
        ]
        retrieved_uris = [context["uri"] for context in contexts]

        for context in contexts:
            uri = context["uri"]
            try:
                context["content"] = client.read(
                    uri,
                    offset=0,
                    limit=-1,
                )
            except Exception as exc:
                context["content"] = f"[READ ERROR] {exc}"

        reranker = build_single_search_reranker()
        rerank_enabled = reranker is not None
        rerank_limit = DEFAULT_SINGLE_SEARCH_RERANK_LIMIT
        contexts, rerank_scores = rerank_single_search_contexts(
            question=question,
            contexts=contexts,
            reranker=reranker,
            limit=rerank_limit,
        )
        context_uris = [context["uri"] for context in contexts]

        prompt = build_single_search_context_prompt(
            question=question,
            question_type=question_type,
            question_time=question_time,
            contexts=contexts,
        )
        vlm = build_single_search_vlm()
        memory_prompt_tokens, memory_chars, memory_tokenizer = (
            count_retrieved_memory_content_tokens(
                contexts,
                model_name=getattr(vlm, "model", None),
            )
        )
        if debug_print_model_input:
            print("\n===== SINGLE SEARCH MODEL INPUT BEGIN =====", flush=True)
            print(prompt, flush=True)
            print("===== SINGLE SEARCH MODEL INPUT END =====", flush=True)
            print("===== SINGLE SEARCH DEBUG =====", flush=True)
            print(f"context_count: {len(context_uris)}", flush=True)
            print(f"memory_content_tokens: {memory_prompt_tokens}", flush=True)
            print(f"memory_content_chars: {memory_chars}", flush=True)
            print(f"memory_tokenizer: {memory_tokenizer}", flush=True)
            print(f"rerank_enabled: {rerank_enabled}", flush=True)
            if rerank_enabled:
                print(f"rerank_limit: {rerank_limit}", flush=True)
            print("retrieved_uris:", flush=True)
            for uri in retrieved_uris:
                print(f"  {uri}", flush=True)
            print("context_uris:", flush=True)
            for uri in context_uris:
                print(f"  {uri}", flush=True)
            if rerank_scores:
                print("rerank_scores:", flush=True)
                for item in rerank_scores:
                    print(
                        f"  score={item['score']} raw_rank={item.get('raw_rank')} uri={item['uri']}",
                        flush=True,
                    )
            print("===== SINGLE SEARCH DEBUG END =====", flush=True)
        raw_response = vlm.get_completion(prompt)
        response = _response_text(raw_response)
        token_usage = _token_usage_from_vlm(vlm, raw_response)
        token_usage["memory_prompt_tokens"] = memory_prompt_tokens
        token_usage["memory_chars"] = memory_chars
        token_usage["memory_tokenizer"] = memory_tokenizer
        if debug_print_model_input:
            print("===== SINGLE SEARCH TOKEN USAGE =====", flush=True)
            print(json.dumps(token_usage, ensure_ascii=False, indent=2), flush=True)
            print("===== SINGLE SEARCH TOKEN USAGE END =====", flush=True)
        elapsed = time.time() - start_time
        return (
            response,
            token_usage,
            elapsed,
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
                }
            ],
            prompt if debug_print_model_input else "",
        )
    except Exception as exc:
        elapsed = time.time() - start_time
        return (
            f"[SINGLE SEARCH ERROR] {exc}",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            elapsed,
            1,
            ["single_search", "read", "context_answer"],
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


def main():
    parser = argparse.ArgumentParser(description="VikingBot LongMemEval evaluation script")
    parser.add_argument(
        "input",
        nargs="?",
        default="data/longmemeval_s_cleaned.json",
        help="Path to LongMemEval JSON file",
    )
    parser.add_argument(
        "--output",
        default="./result/longmemeval_qa_result.csv",
        help="Path to output csv file",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="LongMemEval sample index (0-based), default all samples",
    )
    parser.add_argument(
        "--count", type=int, default=None, help="Number of QA questions to run, default all"
    )
    parser.add_argument(
        "--threads", type=int, default=5, help="Number of concurrent threads, default: 5"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-question OpenViking client timeout in seconds, default: 300",
    )
    parser.add_argument(
        "--answer-mode",
        choices=["single-search-context"],
        default="single-search-context",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--debug-print-model-input",
        action="store_true",
        help="Print the full model input prompt and retrieved memory token stats.",
    )
    parser.add_argument(
        "--single-search-context-limit",
        type=int,
        default=DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT,
        help=(
            "Number of memory files to read into the single-search-context prompt, "
            f"default: {DEFAULT_SINGLE_SEARCH_CONTEXT_LIMIT}"
        ),
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    qa_list = load_longmemeval_qa(args.input, args.sample, args.count)
    total = len(qa_list)
    print(f"Loaded {total} QA questions")

    fieldnames = [
        "sample_id",
        "question",
        "answer",
        "question_type",
        "question_time",
        "response",
        "model_input_prompt",
        "token_usage",
        "time_cost",
        "iteration",
        "tools_used_names",
        "retrieved_uris_by_iteration",
        "result",
    ]
    write_lock = threading.Lock()
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        f.flush()

        processed_count = 0
        print(
            f"Starting evaluation with {args.threads} concurrent threads, {total} questions to process"
        )

        def process_qa(qa_item, idx, total_count):
            nonlocal processed_count
            question = qa_item["question"]
            answer = qa_item["answer"]
            sample_id = qa_item["sample_id"]
            question_time = qa_item.get("question_time")
            print(f"Processing {idx}/{total_count}: {question[:60]}...")
            if question_time:
                print(f"  [time context: {question_time}]")

            sender_id = build_sample_user_id(sample_id)
            session_id = build_sample_agent_id(sample_id)
            (
                response,
                token_usage,
                time_cost,
                iteration,
                tools_used_names,
                retrieved_uris_by_iteration,
                model_input_prompt,
            ) = run_single_search_context_answer(
                question,
                qa_item.get("question_type"),
                question_time,
                sender_id=sender_id,
                session_id=session_id,
                timeout=args.timeout,
                single_search_context_limit=args.single_search_context_limit,
                debug_print_model_input=args.debug_print_model_input,
            )
            row = {
                "sample_id": sample_id,
                "question": question,
                "answer": answer,
                "question_type": qa_item.get("question_type", ""),
                "question_time": question_time or "",
                "response": response,
                "model_input_prompt": model_input_prompt,
                "token_usage": json.dumps(token_usage, ensure_ascii=False),
                "time_cost": round(time_cost, 2),
                "iteration": iteration,
                "tools_used_names": json.dumps(tools_used_names, ensure_ascii=False),
                "retrieved_uris_by_iteration": json.dumps(
                    retrieved_uris_by_iteration, ensure_ascii=False
                ),
                "result": "",
            }

            with write_lock:
                writer.writerow(row)
                f.flush()
                processed_count += 1
                print(f"Completed {processed_count}/{total}, time cost: {round(time_cost, 2)}s")
            return True

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = []
            for idx, qa_item in enumerate(qa_list, 1):
                futures.append(executor.submit(process_qa, qa_item, idx, total))

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Error processing QA item: {str(e)}")

    print(f"Evaluation completed, results saved to {output_path}")


if __name__ == "__main__":
    main()
