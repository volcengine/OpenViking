#!/usr/bin/env python3
"""
Unified dataset preparation script.
Orchestrates download and sampling for end-to-end data preparation.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.path.append(str(Path(__file__).parent))

from download_dataset import download_dataset, DATASET_SOURCES as DOWNLOAD_SOURCES
from sample_dataset import sample_dataset, DATASET_SAMPLERS


def prepare_dataset(
    dataset_name: str,
    download_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    skip_download: bool = False,
    skip_sampling: bool = False,
    force_download: bool = False,
    use_full: bool = False,
    sample_mode: str = "stratified"
) -> bool:
    """Prepare a single dataset end-to-end."""
    print("\n" + "=" * 80)
    print(f"Preparing dataset: {dataset_name}")
    print("=" * 80)
    
    success = True
    
    # Step 1: Download
    if not skip_download:
        print("\n[Step 1/2] Downloading dataset...")
        download_success = download_dataset(
            dataset_name,
            download_dir,
            force=force_download,
            verify=True
        )
        if not download_success:
            print(f"❌ Failed to download {dataset_name}")
            success = False
    else:
        print("\n[Step 1/2] Skipping download (--skip-download)")
    
    # Step 2: Sample
    if not skip_sampling and success:
        print("\n[Step 2/2] Sampling dataset...")
        input_dir = download_dir / dataset_name
        dataset_output_dir = output_dir / dataset_name
        
        actual_sample_size = None if use_full else sample_size
        actual_num_docs = None if use_full else num_docs
        
        sample_success = sample_dataset(
            dataset_name,
            input_dir,
            dataset_output_dir,
            actual_sample_size,
            actual_num_docs,
            seed,
            sample_mode
        )
        if not sample_success:
            print(f"❌ Failed to sample {dataset_name}")
            success = False
    elif skip_sampling:
        print("\n[Step 2/2] Skipping sampling (--skip-sampling)")
    
    return success


def main():
    parser = argparse.ArgumentParser(
        description="Unified dataset preparation for RAG benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Prepare all datasets with default settings
  python prepare_dataset.py
  
  # Prepare only Locomo dataset with 50 samples
  python prepare_dataset.py --dataset Locomo --sample-size 50
  
  # Prepare all datasets using full data (no sampling)
  python prepare_dataset.py --full
  
  # Skip download, only sample existing datasets
  python prepare_dataset.py --skip-download
  
  # Skip sampling, only download datasets
  python prepare_dataset.py --skip-sampling
        """
    )
    
    # Dataset selection
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        choices=list(DOWNLOAD_SOURCES.keys()) + ["all"],
        default="all",
        help="Dataset to prepare (default: all)"
    )
    
    # Directories
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path(__file__).parent.parent / "raw_data",
        help="Directory to download datasets to (default: raw_data/)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path(__file__).parent.parent / "datasets",
        help="Directory for final prepared datasets (default: datasets/)"
    )
    
    # Sampling options
    parser.add_argument(
        "--sample-size", "-n",
        type=int,
        default=None,
        help="Number of samples to use (default: use full dataset)"
    )
    parser.add_argument(
        "--num-docs",
        type=int,
        default=None,
        help="Number of documents to sample (for document-level sampling)"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use full dataset (no sampling, overrides --sample-size)"
    )
    parser.add_argument(
        "--sample-mode",
        type=str,
        choices=["random", "stratified"],
        default="random",
        help="Sampling mode: 'random' (default) for random sampling, 'stratified' for stratified sampling by category"
    )
    
    # Skip options
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download step (use existing data)"
    )
    parser.add_argument(
        "--skip-sampling",
        action="store_true",
        help="Skip sampling step"
    )
    
    # Force options
    parser.add_argument(
        "--force-download", "-f",
        action="store_true",
        help="Force re-download even if dataset exists"
    )
    
    args = parser.parse_args()
    
    # Validate dataset choices
    available_datasets = set(DOWNLOAD_SOURCES.keys()) & set(DATASET_SAMPLERS.keys())
    if args.dataset != "all" and args.dataset not in available_datasets:
        print(f"Error: Dataset '{args.dataset}' not available")
        print(f"Available datasets: {', '.join(sorted(available_datasets))}")
        return 1
    
    # Handle --full flag - use full dataset, no sampling
    if args.full:
        args.sample_size = None
        args.num_docs = None
    
    # Resolve paths
    download_dir = args.download_dir.resolve()
    output_dir = args.output_dir.resolve()
    
    # Determine datasets to process
    datasets = (
        sorted(available_datasets) 
        if args.dataset == "all" 
        else [args.dataset]
    )
    
    # Print configuration
    print("=" * 80)
    print("RAG Benchmark - Unified Dataset Preparation")
    print("=" * 80)
    print(f"Download directory: {download_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Sample size: {args.sample_size if args.sample_size else 'full'}")
    print(f"Number of docs: {args.num_docs if args.num_docs else 'not set'}")
    print(f"Sample mode: {args.sample_mode}")
    print(f"Random seed: {args.seed}")
    print(f"Datasets: {', '.join(datasets)}")
    print(f"Skip download: {args.skip_download}")
    print(f"Skip sampling: {args.skip_sampling}")
    print(f"Force download: {args.force_download}")
    print("=" * 80)
    
    # Prepare datasets
    success_count = 0
    for dataset in datasets:
        if prepare_dataset(
            dataset,
            download_dir,
            output_dir,
            args.sample_size,
            args.num_docs,
            args.seed,
            args.skip_download,
            args.skip_sampling,
            args.force_download,
            args.full,
            args.sample_mode
        ):
            success_count += 1
    
    # Final summary
    print("\n" + "=" * 80)
    print("Preparation Complete")
    print("=" * 80)
    print(f"Success: {success_count}/{len(datasets)} datasets")
    
    if success_count == len(datasets):
        print("\n✅ All datasets prepared successfully!")
        print(f"\nPrepared datasets are in: {output_dir}")
        print("\nYou can now use these datasets with the RAG benchmark.")
        print("\nUpdate your config file's 'dataset_path' path to point to the prepared dataset.")
        return 0
    else:
        print(f"\n❌ {len(datasets) - success_count} dataset(s) failed to prepare")
        return 1


if __name__ == "__main__":
    sys.exit(main())
