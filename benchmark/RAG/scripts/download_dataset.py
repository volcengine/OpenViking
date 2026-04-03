#!/usr/bin/env python3
"""
Download datasets from public sources.
Supports URL downloads from GitHub, S3, etc.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from tqdm import tqdm

sys.path.append(str(Path(__file__).parent.parent))

DATASET_SOURCES = {
    # 示例配置：取消注释并根据需要修改
    #
    "Locomo": {
        "source_type": "url",
        "url": "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
        "checksum": "",
        "files": [
            "locomo10.json"
        ]
    },
    #
    "SyllabusQA": {
        "source_type": "url",
        "url": "https://github.com/umass-ml4ed/SyllabusQA/archive/refs/heads/main.zip",
        "checksum": "",
        "files": [
            "data/dataset_split/train.csv",
            "data/dataset_split/val.csv",
            "data/dataset_split/test.csv",
            "syllabi/syllabi_redacted/word/"
        ],
        "extract_subdir": "SyllabusQA-main"
    },
    #
    "Qasper": {
        "source_type": "url",
        "urls": [
            "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz",
            "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz"
        ],
        "checksum": "",
        "files": [
            "qasper-train-v0.3.json",
            "qasper-dev-v0.3.json",
            "qasper-test-v0.3.json"
        ]
    },
    #
    "FinanceBench": {
        "source_type": "url",
        "url": "https://github.com/patronus-ai/financebench/archive/refs/heads/main.zip",
        "checksum": "",
        "files": [
            "data/financebench_open_source.jsonl",
            "data/financebench_document_information.jsonl",
            "pdfs/"
        ],
        "extract_subdir": "financebench-main"
    },
}

def calculate_checksum(file_path: Path, algorithm: str = "sha256") -> str:
    """Calculate checksum of a file."""
    hash_obj = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def download_file(url: str, dest_path: Path, chunk_size: int = 8192) -> bool:
    """Download a file with progress bar."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get("content-length", 0))
        
        with open(dest_path, "wb") as f, tqdm(
            desc=f"Downloading {dest_path.name}",
            total=total_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk))
        return True
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        if dest_path.exists():
            dest_path.unlink()
        return False


def extract_archive(archive_path: Path, extract_to: Path, extract_subdir: Optional[str] = None) -> bool:
    """Extract archive file (zip, tar.gz, etc.)."""
    import zipfile
    import tarfile
    
    try:
        temp_extract_dir = extract_to / ".temp_extract"
        temp_extract_dir.mkdir(parents=True, exist_ok=True)
        
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zip_ref:
                zip_ref.extractall(temp_extract_dir)
        elif archive_path.suffix in [".tar", ".gz", ".tgz"]:
            with tarfile.open(archive_path, "r:*") as tar_ref:
                tar_ref.extractall(temp_extract_dir)
        else:
            print(f"Unsupported archive format: {archive_path.suffix}")
            shutil.rmtree(temp_extract_dir)
            return False
        
        if extract_subdir:
            source_dir = temp_extract_dir / extract_subdir
            if source_dir.exists() and source_dir.is_dir():
                for item in source_dir.iterdir():
                    dest_item = extract_to / item.name
                    if dest_item.exists():
                        if dest_item.is_dir():
                            shutil.rmtree(dest_item)
                        else:
                            dest_item.unlink()
                    shutil.move(str(item), str(dest_item))
            else:
                print(f"Warning: Subdirectory {extract_subdir} not found in archive")
        else:
            for item in temp_extract_dir.iterdir():
                dest_item = extract_to / item.name
                if dest_item.exists():
                    if dest_item.is_dir():
                        shutil.rmtree(dest_item)
                    else:
                        dest_item.unlink()
                shutil.move(str(item), str(dest_item))
        
        shutil.rmtree(temp_extract_dir)
        return True
    except Exception as e:
        print(f"Error extracting {archive_path}: {e}")
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        return False


def verify_dataset(dataset_name: str, dataset_dir: Path) -> bool:
    """Verify that all required files exist for a dataset."""
    if dataset_name not in DATASET_SOURCES:
        print(f"Unknown dataset: {dataset_name}")
        return False
    
    source = DATASET_SOURCES[dataset_name]
    missing_files = []
    
    for file_path in source["files"]:
        full_path = dataset_dir / file_path
        # Check if path exists (either file or directory)
        if not full_path.exists():
            missing_files.append(file_path)
    
    if missing_files:
        print(f"Missing files for {dataset_name}: {missing_files}")
        return False
    
    print(f"✓ {dataset_name} verified successfully")
    return True


def is_archive_file(file_path: Path) -> bool:
    """Check if a file is an archive based on extension."""
    archive_extensions = [".zip", ".tar", ".tar.gz", ".tgz", ".gz"]
    return any(str(file_path).lower().endswith(ext) for ext in archive_extensions)


def download_from_url(
    source: Dict,
    output_dir: Path,
    dataset_name: str,
    force: bool = False,
    verify: bool = True
) -> bool:
    """Download dataset from URL. Supports both archives and single files.
    Supports single url or multiple urls via urls field.
    """
    dataset_dir = output_dir / dataset_name
    
    if dataset_dir.exists() and not force:
        print(f"{dataset_name} already exists at {dataset_dir}, skipping download")
        if verify:
            return verify_dataset(dataset_name, dataset_dir)
        return True
    
    print(f"Downloading {dataset_name}...")
    
    # Support single url or multiple urls
    urls = source.get("urls", [source.get("url")]) if source.get("urls") else [source.get("url")]
    
    success = True
    for url in urls:
        if not url:
            continue
            
        parsed_url = urlparse(url)
        file_name = Path(parsed_url.path).name
        downloaded_path = output_dir / file_name
        
        if not download_file(url, downloaded_path):
            success = False
            continue
        
        if "checksum" in source and source["checksum"]:
            algo, expected_checksum = source["checksum"].split(":", 1)
            actual_checksum = calculate_checksum(downloaded_path, algo)
            if actual_checksum != expected_checksum:
                print(f"Checksum mismatch for {dataset_name}")
                print(f"Expected: {expected_checksum}")
                print(f"Actual: {actual_checksum}")
                downloaded_path.unlink()
                success = False
                continue
            print(f"✓ Checksum verified for {dataset_name}")
        
        if is_archive_file(downloaded_path):
            extract_subdir = source.get("extract_subdir")
            if not extract_archive(downloaded_path, dataset_dir, extract_subdir):
                downloaded_path.unlink()
                success = False
                continue
            downloaded_path.unlink()
        else:
            dataset_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dataset_dir / file_name
            shutil.move(str(downloaded_path), str(dest_path))
            print(f"✓ Saved single file to {dest_path}")
    
    if verify and not verify_dataset(dataset_name, dataset_dir):
        return False
    
    if success:
        print(f"✓ {dataset_name} downloaded successfully to {dataset_dir}")
    return success


def download_dataset(
    dataset_name: str,
    output_dir: Path,
    force: bool = False,
    verify: bool = True
) -> bool:
    """Download a single dataset."""
    if dataset_name not in DATASET_SOURCES:
        print(f"Unknown dataset: {dataset_name}")
        return False
    
    source = DATASET_SOURCES[dataset_name]
    dataset_dir = output_dir / dataset_name
    
    if dataset_dir.exists() and not force:
        print(f"{dataset_name} already exists at {dataset_dir}, skipping download")
        if verify:
            return verify_dataset(dataset_name, dataset_dir)
        return True
    
    success = download_from_url(source, output_dir, dataset_name, force, verify)
    
    if success and verify:
        return verify_dataset(dataset_name, dataset_dir)
    
    return success


def main():
    # Check if any datasets are configured
    configured_datasets = [k for k in DATASET_SOURCES.keys() if not k.startswith('#')]
    
    if not configured_datasets:
        print("=" * 80)
        print("No datasets configured!")
        print("=" * 80)
        print()
        print("Please edit DATASET_SOURCES in download_dataset.py")
        print("and uncomment the example configurations or add your own.")
        print()
        print("See README_DATASET_CONFIG.md for detailed instructions.")
        print("=" * 80)
        return 1
    
    parser = argparse.ArgumentParser(
        description="Download datasets for RAG benchmark"
    )
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        choices=configured_datasets + ["all"],
        default="all",
        help="Dataset to download (default: all)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path(__file__).parent.parent / "raw_data",
        help="Output directory (default: raw_data/)"
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-download even if dataset exists"
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip dataset verification"
    )
    
    args = parser.parse_args()
    
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    datasets = (
        configured_datasets 
        if args.dataset == "all" 
        else [args.dataset]
    )
    
    print(f"Downloading datasets to: {output_dir}")
    print(f"Datasets: {', '.join(datasets)}")
    print()
    
    success_count = 0
    for dataset in datasets:
        if download_dataset(
            dataset, 
            output_dir, 
            args.force, 
            not args.no_verify
        ):
            success_count += 1
        print()
    
    print(f"Download complete: {success_count}/{len(datasets)} successful")
    return 0 if success_count == len(datasets) else 1


if __name__ == "__main__":
    sys.exit(main())
