#!/usr/bin/env python3
"""Run sampling for all datasets with specific parameters."""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from sample_dataset import sample_dataset


def main():
    input_dir = Path(__file__).parent.parent / "raw_data"
    output_dir = Path(__file__).parent.parent / "datasets"
    
    print("=" * 60)
    print("Running sampling for all datasets with custom parameters")
    print("=" * 60)
    
    success_count = 0
    total_count = 0
    
    # Locomo: 3 documents, 80 QAs, stratified
    total_count += 1
    print("\n" + "=" * 60)
    print("Sampling Locomo: 3 documents, 80 QAs")
    print("=" * 60)
    if sample_dataset(
        "Locomo",
        input_dir / "Locomo",
        output_dir / "Locomo",
        sample_size=80,
        num_docs=3,
        seed=42,
        sample_mode="stratified"
    ):
        success_count += 1
    
    # SyllabusQA: 7 documents, 90 QAs, stratified
    total_count += 1
    print("\n" + "=" * 60)
    print("Sampling SyllabusQA: 7 documents, 90 QAs")
    print("=" * 60)
    if sample_dataset(
        "SyllabusQA",
        input_dir / "SyllabusQA",
        output_dir / "SyllabusQA",
        sample_size=90,
        num_docs=7,
        seed=42,
        sample_mode="stratified"
    ):
        success_count += 1
    
    # Qasper: 8 documents, 60 QAs, stratified
    total_count += 1
    print("\n" + "=" * 60)
    print("Sampling Qasper: 8 documents, 60 QAs")
    print("=" * 60)
    if sample_dataset(
        "Qasper",
        input_dir / "Qasper",
        output_dir / "Qasper",
        sample_size=60,
        num_docs=8,
        seed=42,
        sample_mode="stratified"
    ):
        success_count += 1
    
    # FinanceBench: 3 documents, 12 QAs, stratified
    total_count += 1
    print("\n" + "=" * 60)
    print("Sampling FinanceBench: 3 documents, 12 QAs")
    print("=" * 60)
    if sample_dataset(
        "FinanceBench",
        input_dir / "FinanceBench",
        output_dir / "FinanceBench",
        sample_size=12,
        num_docs=3,
        seed=42,
        sample_mode="stratified"
    ):
        success_count += 1
    
    print("\n" + "=" * 60)
    print(f"Sampling complete: {success_count}/{total_count} successful")
    print("=" * 60)
    
    return 0 if success_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())
