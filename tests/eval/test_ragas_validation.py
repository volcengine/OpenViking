#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Validation script for eval module using local_doc_example_glm5.jsonl.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def load_jsonl(file_path: str) -> List[Dict[str, Any]]:
    """Load JSONL file and return list of dicts."""
    data = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                data.append(item)
            except json.JSONDecodeError as e:
                print(f"❌ Line {line_num}: Invalid JSON - {e}")
    return data


def validate_item(item: Dict[str, Any], index: int) -> List[str]:
    """Validate a single item from JSONL."""
    errors = []

    if "question" not in item:
        errors.append(f"Item {index}: Missing 'question' field")

    if "files" not in item:
        errors.append(f"Item {index}: Missing 'files' field")
    elif not isinstance(item["files"], list):
        errors.append(f"Item {index}: 'files' should be a list")
    else:
        for i, file_ref in enumerate(item["files"]):
            if not isinstance(file_ref, str):
                errors.append(f"Item {index}: files[{i}] should be a string")
            elif ":" not in file_ref:
                errors.append(f"Item {index}: files[{i}] should contain ':' for line range")

    if "answer" not in item:
        errors.append(f"Item {index}: Missing 'answer' field")

    return errors


def test_eval_types():
    """Test EvalSample and EvalDataset types."""
    print("\n📦 Testing EvalSample and EvalDataset types...")
    jsonl_path = Path.cwd() / "openviking" / "eval" / "datasets" / "local_doc_example_glm5.jsonl"
    data = load_jsonl(jsonl_path)

    from openviking.eval.ragas.types import EvalDataset, EvalSample

    samples = []
    for _i, item in enumerate(data[:3]):
        sample = EvalSample(
            query=item.get("question", ""),
            context=[item.get("answer", "")[:200]],
            response=item.get("answer", "")[:100],
            ground_truth=item.get("answer", "")[:100],
            meta={"source": "validation", "files": item.get("files", [])},
        )
        samples.append(sample)

    dataset = EvalDataset(name="validation_test", samples=samples)

    print(f"  ✅ Created {len(samples)} EvalSample instances")
    print(f"  ✅ Created EvalDataset with {len(dataset)} samples")

    assert len(dataset) == len(samples), "Dataset length mismatch"
    assert dataset.name == "validation_test", "Dataset name mismatch"

    print("  ✅ All type tests passed")


def test_evaluator_initialization():
    """Test RagasEvaluator initialization."""
    print("\n🔧 Testing RagasEvaluator initialization...")

    try:
        from openviking.eval import RagasEvaluator

        evaluator = RagasEvaluator()
        print("  ✅ RagasEvaluator initialized successfully")
        print(f"  ✅ Metrics: {[m.name for m in evaluator.metrics]}")
    except ImportError as e:
        print(f"  ⚠️  RAGAS not installed: {e}")
        print("  ℹ️  Install with: pip install ragas datasets")
        return False

    return True


def test_pipeline_initialization():
    """Test RAGQueryPipeline initialization."""
    print("\n🔧 Testing RAGQueryPipeline initialization...")

    from openviking.eval.ragas.pipeline import RAGQueryPipeline

    pipeline = RAGQueryPipeline(config_path="./test.conf", data_path="./test_data/test_ragas")

    assert pipeline.config_path == "./test.conf"
    assert pipeline.data_path == "./test_data/test_ragas"
    assert pipeline._client is None

    print("  ✅ RAGQueryPipeline initialized successfully")
    print(f"  ✅ Config path: {pipeline.config_path}")
    print(f"  ✅ Data path: {pipeline.data_path}")


def test_question_loader():
    """Test question loading from JSONL."""
    print("\n📄 Testing question loader...")

    jsonl_path = Path.cwd() / "openviking" / "eval" / "datasets" / "local_doc_example_glm5.jsonl"

    data = load_jsonl(jsonl_path)
    print(f"  ✅ Loaded {len(data)} questions from JSONL")

    errors = []
    for i, item in enumerate(data):
        errors.extend(validate_item(item, i))

    if errors:
        print(f"  ❌ Found {len(errors)} validation errors:")
        for error in errors[:5]:
            print(f"    - {error}")
    else:
        print(f"  ✅ All {len(data)} items validated successfully")


def main():
    print("=" * 60)
    print("🧪 OpenViking Eval Module Validation")
    print("=" * 60)

    jsonl_path = (
        "/Users/bytedance/workspace/github/OpenViking/openviking/eval/local_doc_example_glm5.jsonl"
    )

    if not Path(jsonl_path).exists():
        print(f"❌ File not found: {jsonl_path}")
        sys.exit(1)

    print(f"\n📂 Loading: {jsonl_path}")

    data = load_jsonl(jsonl_path)
    print(f"✅ Loaded {len(data)} items")

    all_errors = []
    for i, item in enumerate(data):
        all_errors.extend(validate_item(item, i))

    if all_errors:
        print(f"\n❌ Found {len(all_errors)} validation errors")
        for error in all_errors[:10]:
            print(f"  - {error}")
        if len(all_errors) > 10:
            print(f"  ... and {len(all_errors) - 10} more errors")
    else:
        print(f"\n✅ All {len(data)} items validated successfully")

    try:
        test_eval_types(data)
    except Exception as e:
        print(f"  ❌ Eval types test failed: {e}")
        all_errors.append(f"Eval types test: {e}")

    try:
        test_pipeline_initialization()
    except Exception as e:
        print(f"  ❌ Pipeline test failed: {e}")
        all_errors.append(f"Pipeline test: {e}")

    try:
        test_question_loader()
    except Exception as e:
        print(f"  ❌ Question loader test failed: {e}")
        all_errors.append(f"Question loader test: {e}")

    try:
        test_evaluator_initialization()
    except Exception as e:
        print(f"  ❌ Evaluator test failed: {e}")
        all_errors.append(f"Evaluator test: {e}")

    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ Validation completed with {len(all_errors)} errors")
        sys.exit(1)
    else:
        print("✅ All validations passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
