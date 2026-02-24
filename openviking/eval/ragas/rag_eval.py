#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
RAG Evaluation CLI Tool for OpenViking.

Usage:
    python -m openviking.eval.rag_eval --docs_dir ./docs --question_file ./questions.jsonl
    python -m openviking.eval.rag_eval --docs_dir ./docs --code_dir ./code --question_file ./questions.jsonl
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_questions(question_file: str) -> List[Dict[str, Any]]:
    """
    Load questions from JSONL file.

    Args:
        question_file: Path to JSONL file with questions

    Returns:
        List of question dictionaries
    """
    questions = []
    with open(question_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if "question" not in item:
                    logger.warning(f"Line {line_num}: Missing 'question' field")
                    continue
                questions.append(item)
            except json.JSONDecodeError as e:
                logger.warning(f"Line {line_num}: Invalid JSON - {e}")
    return questions


class RAGEvaluator:
    """
    RAG evaluator that uses OpenViking for retrieval and evaluation.
    """

    def __init__(
        self,
        docs_dirs: List[str],
        code_dirs: List[str],
        config_path: str = "./ov.conf",
        data_path: str = "./data",
        enable_recorder: bool = False,
    ):
        """
        Initialize the RAG evaluator.

        Args:
            docs_dirs: List of document directories or files
            code_dirs: List of code repository paths
            config_path: Path to OpenViking config file
            data_path: Path to OpenViking data directory
            enable_recorder: Whether to enable IO recording
        """
        self.docs_dirs = docs_dirs
        self.code_dirs = code_dirs
        self.config_path = config_path
        self.data_path = data_path
        self.enable_recorder = enable_recorder
        self._client = None
        self._initialized = False

        if enable_recorder:
            from openviking.eval.recorder import init_recorder
            init_recorder(enabled=True)
            logger.info("IO Recorder enabled")

    def _get_client(self):
        """Get or create OpenViking client."""
        if self._client is None:
            try:
                from openviking import OpenViking

                config_path = Path(self.config_path).expanduser()
                if config_path.exists():
                    os.environ["OPENVIKING_CONFIG_FILE"] = str(config_path)
                    logger.info(f"Using config file: {config_path}")

                if self.enable_recorder:
                    os.environ["OPENVIKING_ENABLE_RECORDER"] = "true"

                self._client = OpenViking(path=self.data_path)
            except Exception as e:
                logger.error(f"Failed to create OpenViking client: {e}")
                raise
        return self._client

    async def initialize(self):
        """Initialize the evaluator by adding resources."""
        if self._initialized:
            return

        client = self._get_client()

        for doc_path in self.docs_dirs:
            path = Path(doc_path).expanduser()
            if not path.exists():
                logger.warning(f"Document path does not exist: {path}")
                continue

            logger.info(f"Adding document: {path}")
            try:
                result = client.add_resource(
                    path=str(path),
                    wait=True,
                    timeout=300,
                )
                if result and "root_uri" in result:
                    logger.info(f"Added: {result['root_uri']}")
            except Exception as e:
                logger.error(f"Failed to add document {path}: {e}")

        for code_path in self.code_dirs:
            path = Path(code_path).expanduser()
            if not path.exists():
                logger.warning(f"Code path does not exist: {path}")
                continue

            logger.info(f"Adding code: {path}")
            try:
                result = client.add_resource(
                    path=str(path),
                    wait=True,
                    timeout=300,
                )
                if result and "root_uri" in result:
                    logger.info(f"Added: {result['root_uri']}")
            except Exception as e:
                logger.error(f"Failed to add code {path}: {e}")

        self._initialized = True

    async def retrieve(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        """
        Retrieve relevant contexts for a query.

        Args:
            query: The query string
            top_k: Number of results to retrieve

        Returns:
            Dict with contexts and timing info
        """
        client = self._get_client()
        start_time = time.time()

        try:
            result = client.search(query, limit=top_k)
            contexts = []

            if result:
                for ctx in result:
                    contexts.append({
                        "uri": getattr(ctx, "uri", ""),
                        "content": getattr(ctx, "abstract", "") or getattr(ctx, "overview", ""),
                        "score": getattr(ctx, "score", 0.0),
                    })

            retrieval_time = time.time() - start_time
            return {
                "contexts": contexts,
                "retrieval_time": retrieval_time,
            }
        except Exception as e:
            logger.error(f"Failed to retrieve for query '{query}': {e}")
            return {
                "contexts": [],
                "retrieval_time": time.time() - start_time,
            }

    async def evaluate(
        self,
        questions: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Evaluate RAG performance on a set of questions.

        Args:
            questions: List of question dictionaries
            top_k: Number of contexts to retrieve per query

        Returns:
            Evaluation results dictionary
        """
        await self.initialize()

        results = []
        total_questions = len(questions)
        total_retrieval_time = 0.0

        for i, q_item in enumerate(questions, 1):
            question = q_item["question"]
            logger.info(f"Processing question {i}/{total_questions}: {question[:50]}...")

            retrieve_result = await self.retrieve(question, top_k=top_k)
            contexts = retrieve_result["contexts"]
            retrieval_time = retrieve_result["retrieval_time"]
            total_retrieval_time += retrieval_time

            result = {
                "question": question,
                "contexts": contexts,
                "context_count": len(contexts),
                "ground_truth": q_item.get("answer", ""),
                "files": q_item.get("files", []),
                "retrieval_time": retrieval_time,
            }
            results.append(result)

        return {
            "total_questions": total_questions,
            "results": results,
            "metrics": self._calculate_metrics(results, total_retrieval_time),
        }

    def _calculate_metrics(
        self, results: List[Dict[str, Any]], total_retrieval_time: float
    ) -> Dict[str, Any]:
        """Calculate evaluation metrics."""
        total = len(results)
        if total == 0:
            return {}

        context_counts = [r["context_count"] for r in results]
        avg_contexts = sum(context_counts) / total if total > 0 else 0

        questions_with_contexts = sum(1 for c in context_counts if c > 0)
        retrieval_rate = questions_with_contexts / total if total > 0 else 0

        retrieval_times = [r["retrieval_time"] for r in results]
        avg_retrieval_time = sum(retrieval_times) / total if total > 0 else 0

        return {
            "total_questions": total,
            "avg_contexts_per_question": round(avg_contexts, 2),
            "questions_with_contexts": questions_with_contexts,
            "retrieval_success_rate": round(retrieval_rate, 2),
            "avg_retrieval_time_ms": round(avg_retrieval_time * 1000, 2),
            "total_retrieval_time_ms": round(total_retrieval_time * 1000, 2),
        }


def print_report(eval_results: Dict[str, Any]):
    """Print evaluation report to console."""
    print("\n" + "=" * 60)
    print("RAG Evaluation Report")
    print("=" * 60)

    metrics = eval_results.get("metrics", {})
    print("\nOverall Metrics:")
    print(f"  Total Questions: {metrics.get('total_questions', 0)}")
    print(f"  Avg Contexts/Question: {metrics.get('avg_contexts_per_question', 0)}")
    print(f"  Questions with Contexts: {metrics.get('questions_with_contexts', 0)}")
    print(f"  Retrieval Success Rate: {metrics.get('retrieval_success_rate', 0):.1%}")
    print(f"  Avg Retrieval Time: {metrics.get('avg_retrieval_time_ms', 0):.1f}ms")
    print(f"  Total Retrieval Time: {metrics.get('total_retrieval_time_ms', 0):.1f}ms")

    print("\nDetailed Results:")
    for i, result in enumerate(eval_results.get("results", []), 1):
        print(f"\n[Q{i}] {result['question'][:80]}...")
        print(f"  Contexts Retrieved: {result['context_count']}")
        print(f"  Retrieval Time: {result['retrieval_time']*1000:.1f}ms")
        if result['contexts']:
            for j, ctx in enumerate(result['contexts'][:2], 1):
                print(f"  [{j}] URI: {ctx['uri'][:60]}...")
                print(f"      Score: {ctx['score']:.3f}")

    print("\n" + "=" * 60)


def save_report(eval_results: Dict[str, Any], output_path: str):
    """Save evaluation report to JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(eval_results, f, ensure_ascii=False, indent=2)
    logger.info(f"Report saved to: {output_path}")


async def run_ragas_evaluation(eval_results: Dict[str, Any]):
    """Run RAGAS evaluation if available."""
    try:
        from openviking.eval.ragas.types import EvalDataset, EvalSample

        from . import RagasEvaluator

        print("\nRunning RAGAS evaluation...")
        ragas_eval = RagasEvaluator()

        samples = []
        for result in eval_results["results"]:
            sample = EvalSample(
                query=result["question"],
                context=[c["content"] for c in result["contexts"]],
                response="",
                ground_truth=result.get("ground_truth", ""),
            )
            samples.append(sample)

        dataset = EvalDataset(name="rag_eval", samples=samples)
        ragas_result = await ragas_eval.evaluate_dataset(dataset)

        print("\nRAGAS Metrics:")
        for metric, score in ragas_result.mean_scores.items():
            print(f"  {metric}: {score:.3f}")

        return ragas_result

    except ImportError as e:
        logger.error("RAGAS not installed.", exc_info=e)
        logger.info("   Install with: pip install ragas datasets")
        return None


async def main_async(args):
    """Main async function."""
    # if not args.docs_dir and not args.code_dir:
    #     logger.error("At least one --docs_dir or --code_dir must be specified")
    #     sys.exit(1)

    if not args.question_file:
        logger.error("--question_file is required")
        sys.exit(1)

    question_file = Path(args.question_file)
    if not question_file.exists():
        logger.error(f"Question file not found: {question_file}")
        sys.exit(1)

    print("Loading questions...")
    questions = load_questions(str(question_file))
    print(f"   Loaded {len(questions)} questions")

    evaluator = RAGEvaluator(
        docs_dirs=args.docs_dir,
        code_dirs=args.code_dir,
        config_path=args.config,
        data_path=args.data_path,
        enable_recorder=args.recorder,
    )

    print("\nRunning RAG evaluation...")
    eval_results = await evaluator.evaluate(
        questions=questions,
        top_k=args.top_k,
    )

    print_report(eval_results)

    if args.output:
        save_report(eval_results, args.output)

    if args.ragas:
        await run_ragas_evaluation(eval_results)

    if args.recorder:
        from openviking.eval.recorder import get_recorder
        from openviking.storage.viking_fs import get_viking_fs

        recorder = get_recorder()

        viking_fs = get_viking_fs()
        if hasattr(viking_fs.agfs, 'stop_recording'):
            viking_fs.agfs.stop_recording()

        stats = recorder.get_stats()
        print("\n" + "=" * 60)
        print("IO Recorder Statistics")
        print("=" * 60)
        print(f"Total Records: {stats['total_count']}")
        print(f"FS Operations: {stats['fs_count']}")
        print(f"VikingDB Operations: {stats['vikingdb_count']}")
        print(f"Total Latency: {stats['total_latency_ms']:.2f} ms")
        print(f"Errors: {stats['errors']}")
        if stats['operations']:
            print("\nOperations Breakdown:")
            for op, data in stats['operations'].items():
                avg_latency = data['total_latency_ms'] / data['count'] if data['count'] > 0 else 0
                print(f"  {op}: {data['count']} calls, avg {avg_latency:.2f} ms")
        print(f"\nRecord file: {recorder.record_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="RAG Evaluation Tool for OpenViking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Evaluate with documents
  python -m openviking.eval.rag_eval --docs_dir ./docs --question_file ./questions.jsonl

  # Evaluate with multiple document directories and code
  python -m openviking.eval.rag_eval --docs_dir ./docs1 --docs_dir ./docs2 --code_dir ./code --question_file ./questions.jsonl

  # With RAGAS metrics
  python -m openviking.eval.rag_eval --docs_dir ./docs --question_file ./questions.jsonl --ragas
        """,
    )

    parser.add_argument(
        "--docs_dir",
        action="append",
        default=[],
        help="Document directory or file path (can be specified multiple times)",
    )

    parser.add_argument(
        "--code_dir",
        action="append",
        default=[],
        help="Code repository path (can be specified multiple times)",
    )

    parser.add_argument(
        "--question_file",
        required=True,
        help="Path to questions file (JSONL format)",
    )

    parser.add_argument(
        "--config",
        default="./ov.conf",
        help="Path to OpenViking config file (default: ./ov.conf)",
    )

    parser.add_argument(
        "--data_path",
        default="./data",
        help="Path to OpenViking data directory (default: ./data)",
    )

    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="Number of contexts to retrieve per query (default: 5)",
    )

    parser.add_argument(
        "--output",
        help="Path to save evaluation results (JSON format)",
    )

    parser.add_argument(
        "--ragas",
        action="store_true",
        help="Run RAGAS evaluation (requires ragas package)",
    )

    parser.add_argument(
        "--recorder",
        action="store_true",
        help="Enable IO recording for storage layer evaluation",
    )

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
