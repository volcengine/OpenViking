#!/usr/bin/env python3
"""Build and query a WeChat archive index with OpenViking."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add repo root to sys.path for local development usage.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openviking as ov

from openviking.utils.wechat_archive import export_wechat_archive
from openviking_cli.utils.config import get_openviking_config


DEFAULT_SOURCE = "/home/nx/chat_archive"
DEFAULT_EXPORT_ROOT = "/home/nx/chat_archive/.openviking_export"
DEFAULT_TARGET = "viking://resources/wechat_archive"
DEFAULT_WORKSPACE = "/home/nx/.openviking-wechat-archive-live"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize a WeChat archive into Markdown, then index/search it with OpenViking. "
            "The script uses the model services already configured in ~/.openviking/ov.conf."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Rebuild export files and refresh the index")
    index_parser.add_argument("--source", default=DEFAULT_SOURCE, help="Original WeChat archive root")
    index_parser.add_argument(
        "--export-root",
        default=DEFAULT_EXPORT_ROOT,
        help="Generated Markdown export root",
    )
    index_parser.add_argument("--target", default=DEFAULT_TARGET, help="OpenViking target URI")
    index_parser.add_argument(
        "--reason",
        default="WeChat archive export for semantic retrieval",
        help="Reason passed into OpenViking add_resource",
    )
    index_parser.add_argument(
        "--watch-interval",
        type=float,
        default=0.0,
        help="Optional OpenViking watch interval in minutes for the generated export root",
    )
    index_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Wait timeout in seconds when --wait is enabled",
    )
    index_parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Wait for indexing to complete before returning",
    )
    index_parser.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help="Embedded workspace path used for this archive workflow",
    )
    index_parser.add_argument(
        "--http-url",
        default=None,
        help="Optional OpenViking HTTP server URL. When set, use HTTP mode instead of embedded mode.",
    )
    index_parser.add_argument(
        "--semantic-concurrency",
        type=int,
        default=2,
        help="Maximum concurrent semantic LLM tasks for this archive workflow",
    )
    index_parser.add_argument(
        "--embedding-concurrency",
        type=int,
        default=4,
        help="Maximum concurrent embedding requests for this archive workflow",
    )
    index_parser.add_argument(
        "--semantic-llm-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for one semantic LLM call before falling back",
    )
    index_parser.add_argument(
        "--embedding-text-source",
        choices=("summary_first", "summary_only", "content_only"),
        default="content_only",
        help="Text source used for file embeddings during WeChat archive indexing",
    )

    search_parser = subparsers.add_parser("search", help="Run semantic search against the archive")
    search_parser.add_argument("query", help="Semantic query text")
    search_parser.add_argument("--target", default=DEFAULT_TARGET, help="OpenViking target URI")
    search_parser.add_argument("--limit", type=int, default=5, help="Maximum number of results")
    search_parser.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help="Embedded workspace path used for this archive workflow",
    )
    search_parser.add_argument(
        "--http-url",
        default=None,
        help="Optional OpenViking HTTP server URL. When set, use HTTP mode instead of embedded mode.",
    )

    return parser


def run_index(args: argparse.Namespace) -> int:
    source_root = Path(args.source)
    export_root = Path(args.export_root)
    stats = export_wechat_archive(source_root, export_root)

    client = _open_client(args)
    try:
        request_kwargs = dict(
            path=str(export_root),
            to=args.target,
            reason=args.reason,
            instruction=(
                "Index this WeChat archive export for semantic retrieval. "
                "Prioritize chat topics, senders, message dates, and linked article context."
            ),
            wait=args.wait,
            timeout=args.timeout if args.wait else None,
        )
        if args.http_url:
            if args.watch_interval:
                print("watch_interval is only supported in embedded mode; ignored in HTTP mode.")
        else:
            request_kwargs["watch_interval"] = args.watch_interval
        result = client.add_resource(**request_kwargs)
    finally:
        client.close()

    print(f"Export root: {stats.output_root}")
    print(
        "Export stats:"
        f" chats={stats.chats}"
        f" message_files={stats.message_files}"
        f" messages={stats.messages}"
        f" linked_docs={stats.linked_docs}"
        f" generated_files={stats.generated_files}"
    )
    if stats.warnings:
        print("Warnings:")
        for warning in stats.warnings:
            print(f"  - {warning}")

    print(f"Indexed target: {result.get('root_uri') or args.target}")
    if not args.wait:
        print("Indexing was queued asynchronously. Re-run with --wait for synchronous completion.")
    queue_status = result.get("queue_status")
    if queue_status:
        print(f"Queue status: {queue_status}")

    return 0


def run_search(args: argparse.Namespace) -> int:
    client = _open_client(args)
    try:
        result = client.find(query=args.query, target_uri=args.target, limit=args.limit)
    finally:
        client.close()

    resources = getattr(result, "resources", []) if result is not None else []
    if not resources:
        print("No results found.")
        return 0

    for index, item in enumerate(resources, start=1):
        print(f"{index}. {item.uri}")
        print(f"   score={item.score:.4f}")
        abstract = (item.abstract or "").strip()
        if abstract:
            print(f"   abstract={_single_line(abstract)}")
    return 0


def _single_line(text: str, limit: int = 160) -> str:
    collapsed = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _open_client(args: argparse.Namespace):
    try:
        _prepare_index_config(args)
        if args.http_url:
            client = ov.SyncHTTPClient(url=args.http_url)
        else:
            client = ov.OpenViking(path=args.workspace) if args.workspace else ov.OpenViking()
        client.initialize()
    except RuntimeError as exc:
        message = str(exc)
        if "AGFS port" in message and "already in use" in message:
            raise SystemExit(
                "Embedded OpenViking is already running in another process. "
                "Wait for that job to finish, or use --http-url to reuse an existing HTTP server."
            ) from exc
        raise
    return client


def _prepare_index_config(args: argparse.Namespace) -> None:
    """Apply workflow-specific config overrides before client initialization."""
    if not hasattr(args, "semantic_concurrency"):
        return

    config = get_openviking_config()
    if args.semantic_concurrency > 0:
        config.vlm.max_concurrent = args.semantic_concurrency
    if args.embedding_concurrency > 0:
        config.embedding.max_concurrent = args.embedding_concurrency
    if args.semantic_llm_timeout > 0:
        config.semantic.llm_timeout_seconds = args.semantic_llm_timeout
    if args.embedding_text_source:
        config.embedding.text_source = args.embedding_text_source


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "index":
        return run_index(args)
    if args.command == "search":
        return run_search(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
