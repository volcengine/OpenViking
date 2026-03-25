#!/usr/bin/env python3
"""
Sample datasets to create subsets with configurable size.
Supports both full dataset and sampled subsets with seed-based reproducibility.
"""

import argparse
import json
import os
import random
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

sys.path.append(str(Path(__file__).parent.parent))


def load_json_data(file_path: Path) -> Any:
    """Load JSON data from file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl_data(file_path: Path) -> List[Dict]:
    """Load JSONL data from file."""
    data = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def save_json_data(data: Any, file_path: Path) -> None:
    """Save JSON data to file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_jsonl_data(data: List[Dict], file_path: Path) -> None:
    """Save JSONL data to file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def calculate_category_targets(
    sample_size: int,
    categories: List[str],
    print_info: bool = True
) -> Tuple[Dict[str, int], bool]:
    """
    Calculate category targets for stratified sampling.
    
    Args:
        sample_size: Total number of samples to take
        categories: List of category names
        print_info: Whether to print information
    
    Returns:
        Tuple of (category_targets dict, should_fallback_to_random bool)
    """
    num_categories = len(categories)
    if num_categories == 0:
        return {}, True
    
    base_per_category = sample_size // num_categories
    remainder = sample_size % num_categories
    
    if base_per_category == 0:
        if print_info:
            print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
            print("Falling back to random sampling")
        return {}, True
    
    category_targets = {}
    for i, cat in enumerate(categories):
        category_targets[cat] = base_per_category + (1 if i < remainder else 0)
    
    if remainder > 0 and print_info:
        print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
        print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
    
    if print_info:
        print("Category targets:")
        for cat in categories:
            print(f"  {cat}: {category_targets[cat]} QAs")
    
    return category_targets, False


def stratified_sample_with_reallocation(
    sample_size: int,
    category_qas: Dict[str, List[Any]],
    seed: int,
    print_info: bool = True
) -> List[Any]:
    """
    Perform stratified sampling with remainder reallocation.
    
    Args:
        sample_size: Total number of samples to take
        category_qas: Dictionary mapping category to list of QAs
        seed: Random seed
        print_info: Whether to print information
    
    Returns:
        List of sampled QAs
    """
    random.seed(seed)
    categories = sorted(category_qas.keys())
    
    category_targets, should_fallback = calculate_category_targets(sample_size, categories, print_info)
    
    if should_fallback:
        all_qas = []
        for qas in category_qas.values():
            all_qas.extend(qas)
        random.seed(seed)
        if len(all_qas) > sample_size:
            return random.sample(all_qas, sample_size)
        return all_qas
    
    sampled_items = []
    remaining_quota = sample_size
    
    category_actual = {}
    for cat in categories:
        if cat not in category_targets or category_targets[cat] <= 0:
            category_actual[cat] = 0
            continue
        
        cat_qas = category_qas[cat].copy()
        random.shuffle(cat_qas)
        sample_count = min(len(cat_qas), category_targets[cat])
        category_actual[cat] = sample_count
        remaining_quota -= sample_count
        sampled_items.extend(cat_qas[:sample_count])
    
    if remaining_quota > 0 and print_info:
        print(f"Reallocating remaining {remaining_quota} QA(s) to categories with available QAs")
    
    category_available = {}
    for cat in categories:
        if cat in category_qas:
            total_available = len(category_qas[cat])
            used = category_actual.get(cat, 0)
            category_available[cat] = total_available - used
    
    while remaining_quota > 0:
        allocated_this_round = 0
        for cat in categories:
            if remaining_quota <= 0:
                break
            if category_available.get(cat, 0) > 0:
                cat_qas = category_qas[cat].copy()
                random.shuffle(cat_qas)
                for qa in cat_qas:
                    if qa not in sampled_items:
                        sampled_items.append(qa)
                        category_actual[cat] += 1
                        category_available[cat] -= 1
                        remaining_quota -= 1
                        allocated_this_round += 1
                        break
        
        if allocated_this_round == 0:
            if print_info:
                print(f"Warning: No more QAs available to sample. Stopping with {remaining_quota} unallocated.")
            break
    
    if print_info:
        print("Actual category counts after reallocation:")
        for cat in categories:
            print(f"  {cat}: {category_actual.get(cat, 0)} QAs")
    
    return sampled_items


def random_sample_qas(
    sample_size: int,
    all_qas: List[Any],
    seed: int
) -> List[Any]:
    """
    Perform random sampling of QAs.
    
    Args:
        sample_size: Number of samples to take
        all_qas: List of all available QAs
        seed: Random seed
    
    Returns:
        List of sampled QAs
    """
    random.seed(seed)
    if len(all_qas) > sample_size:
        return random.sample(all_qas, sample_size)
    return all_qas


def sample_docs_stratified(
    sample_size: int,
    category_qas: Dict[str, List[Tuple[Any, Any]]],
    doc_category_qas: Dict[Any, Dict[str, List[Any]]],
    all_doc_ids: List[Any],
    seed: int,
    print_info: bool = True
) -> List[Any]:
    """
    Sample documents using stratified sampling by selecting complete documents.
    
    Args:
        sample_size: Target number of QAs
        category_qas: Dict mapping category to list of (doc_id, qa) tuples
        doc_category_qas: Dict mapping doc_id to dict of category to QAs
        all_doc_ids: List of all document IDs
        seed: Random seed
        print_info: Whether to print information
    
    Returns:
        List of selected document IDs
    """
    random.seed(seed)
    categories = sorted(category_qas.keys())
    
    category_targets, should_fallback = calculate_category_targets(sample_size, categories, print_info)
    
    if should_fallback:
        return sample_docs_random(sample_size, doc_category_qas, all_doc_ids, seed, print_info)
    
    selected_docs = []
    selected_qas_by_cat = {cat: 0 for cat in categories}
    doc_used = {doc_id: False for doc_id in all_doc_ids}
    
    for cat in categories:
        target = category_targets[cat]
        if target == 0:
            continue
        
        cat_qas = category_qas[cat].copy()
        random.shuffle(cat_qas)
        
        for doc_id, qa in cat_qas:
            if doc_used[doc_id]:
                continue
            
            doc_cat_qas = doc_category_qas[doc_id]
            new_count = selected_qas_by_cat[cat] + len(doc_cat_qas.get(cat, []))
            if new_count > target:
                continue
            
            selected_docs.append(doc_id)
            doc_used[doc_id] = True
            
            for c, qs in doc_cat_qas.items():
                selected_qas_by_cat[c] += len(qs)
            
            if selected_qas_by_cat[cat] >= target:
                break
    
    total_selected = sum(selected_qas_by_cat.values())
    if print_info:
        print(f"Sampled {len(selected_docs)} documents with {total_selected} QAs")
        for cat in categories:
            print(f"  {cat}: {selected_qas_by_cat[cat]} QAs (target: {category_targets[cat]})")
    
    return selected_docs


def sample_docs_random(
    sample_size: int,
    doc_qas_count: Dict[Any, int],
    all_doc_ids: List[Any],
    seed: int,
    print_info: bool = True
) -> List[Any]:
    """
    Sample documents using random sampling by selecting complete documents.
    
    Args:
        sample_size: Target number of QAs
        doc_qas_count: Dict mapping doc_id to number of valid QAs
        all_doc_ids: List of all document IDs
        seed: Random seed
        print_info: Whether to print information
    
    Returns:
        List of selected document IDs
    """
    random.seed(seed)
    shuffled_docs = all_doc_ids.copy()
    random.shuffle(shuffled_docs)
    
    selected_docs = []
    selected_qas_count = 0
    
    for doc_id in shuffled_docs:
        doc_qas = doc_qas_count.get(doc_id, 0)
        
        if doc_qas == 0:
            continue
        
        if selected_qas_count + doc_qas <= sample_size or not selected_docs:
            selected_docs.append(doc_id)
            selected_qas_count += doc_qas
        else:
            if selected_qas_count >= sample_size:
                break
    
    if print_info:
        print(f"Sampled {len(selected_docs)} documents with {selected_qas_count} QAs (seed={seed})")
    
    return selected_docs


def sample_locomo(
    input_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    sample_mode: str = "random"
) -> Dict[str, Any]:
    """Sample Locomo dataset with stratified sampling support."""
    input_file = input_dir / "locomo10.json"
    if not input_file.exists():
        raise FileNotFoundError(f"locomo10.json not found at {input_file}")
    
    data = load_json_data(input_file)
    if not isinstance(data, list):
        data = [data]
    
    original_num_docs = len(data)
    print(f"Locomo original size: {original_num_docs} documents")
    
    category_qas = {}
    doc_category_qas = []
    for doc in data:
        doc_cat_qas = {}
        if "qa" in doc:
            for q in doc["qa"]:
                cat = str(q.get("category"))
                if cat != "5":
                    if cat not in category_qas:
                        category_qas[cat] = []
                    category_qas[cat].append((doc, q))
                    if cat not in doc_cat_qas:
                        doc_cat_qas[cat] = []
                    doc_cat_qas[cat].append(q)
        doc_category_qas.append(doc_cat_qas)
    
    total_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    print(f"Total QAs (excluding category 5): {total_qas}")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  Category {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        selected_docs = data
        print("Using full Locomo dataset")
    else:
        if num_docs is not None:
            if num_docs >= original_num_docs:
                selected_docs = data
                print("Using all documents")
            else:
                random.seed(seed)
                selected_docs = random.sample(data, num_docs)
                print(f"Sampled {len(selected_docs)} documents (seed={seed})")
            
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                selected_doc_category_qas = {}
                selected_doc_indices = [data.index(doc) for doc in selected_docs]
                
                for doc_idx in selected_doc_indices:
                    doc = data[doc_idx]
                    doc_cat_qas = doc_category_qas[doc_idx]
                    for cat, qs in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for q in qs:
                            selected_doc_category_qas[cat].append((doc_idx, q))
                
                if sample_mode == "stratified":
                    sampled_q_tuples = stratified_sample_with_reallocation(
                        sample_size, selected_doc_category_qas, seed
                    )
                    
                    keep_q_indices = set()
                    for doc_idx, q in sampled_q_tuples:
                        doc = data[doc_idx]
                        for q_idx, qa_item in enumerate(doc.get("qa", [])):
                            if qa_item == q:
                                keep_q_indices.add((doc_idx, q_idx))
                                break
                    
                    for doc in selected_docs:
                        doc_idx = data.index(doc)
                        new_qas = []
                        for q_idx, q in enumerate(doc.get("qa", [])):
                            if (doc_idx, q_idx) in keep_q_indices or str(q.get("category")) == "5":
                                new_qas.append(q)
                        doc["qa"] = new_qas
                
                if sample_mode == "random":
                    all_valid_q_indices = []
                    for doc_idx_in_selected, doc in enumerate(selected_docs):
                        doc_idx = data.index(doc)
                        doc_cat_qas = doc_category_qas[doc_idx]
                        for cat, qs in doc_cat_qas.items():
                            for q in qs:
                                for q_idx, qa_item in enumerate(doc.get("qa", [])):
                                    if qa_item == q:
                                        all_valid_q_indices.append((doc_idx_in_selected, q_idx))
                                        break
                    
                    sampled_q_indices = random_sample_qas(sample_size, all_valid_q_indices, seed)
                    keep_q_indices = set(sampled_q_indices)
                    
                    for doc_idx_in_selected, doc in enumerate(selected_docs):
                        new_qas = []
                        for q_idx, q in enumerate(doc.get("qa", [])):
                            if (doc_idx_in_selected, q_idx) in keep_q_indices or str(q.get("category")) == "5":
                                new_qas.append(q)
                        doc["qa"] = new_qas
        else:
            doc_qas_count = {}
            for doc_idx, doc_cat_qas in enumerate(doc_category_qas):
                count = 0
                for qs in doc_cat_qas.values():
                    count += len(qs)
                doc_qas_count[doc_idx] = count
            
            if sample_mode == "stratified":
                print(f"Using stratified sampling (seed={seed})")
                category_qas_with_indices = {}
                for cat, qas in category_qas.items():
                    category_qas_with_indices[cat] = []
                    for doc, q in qas:
                        doc_idx = data.index(doc)
                        category_qas_with_indices[cat].append((doc_idx, q))
                
                doc_category_qas_dict = {i: d for i, d in enumerate(doc_category_qas)}
                all_doc_indices = list(range(len(data)))
                
                selected_doc_indices = sample_docs_stratified(
                    sample_size, category_qas_with_indices, doc_category_qas_dict, all_doc_indices, seed
                )
                selected_docs = [data[i] for i in selected_doc_indices]
            
            if sample_mode == "random":
                print(f"Using random sampling (seed={seed})")
                all_doc_indices = list(range(len(data)))
                selected_doc_indices = sample_docs_random(
                    sample_size, doc_qas_count, all_doc_indices, seed
                )
                selected_docs = [data[i] for i in selected_doc_indices]
    
    output_data = selected_docs
    output_file = output_dir / "locomo10.json"
    save_json_data(output_data, output_file)
    
    sampled_qas = 0
    for doc in selected_docs:
        if "qa" in doc:
            for q in doc["qa"]:
                if str(q.get("category")) != "5":
                    sampled_qas += 1
    
    metadata = {
        "dataset": "Locomo",
        "original_num_docs": original_num_docs,
        "original_total_qas": total_qas,
        "sampled_num_docs": len(selected_docs),
        "sampled_total_qas": sampled_qas,
        "sample_size": sample_size,
        "num_docs": num_docs,
        "seed": seed,
        "sample_mode": sample_mode,
        "is_full": is_full,
        "note": "Category 5 questions are excluded from QA count"
    }
    
    return metadata


def sample_syllabusqa(
    input_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    sample_mode: str = "random"
) -> Dict[str, Any]:
    """Sample SyllabusQA dataset with stratified sampling support."""
    from collections import defaultdict
    import csv
    
    dataset_split_dir = input_dir / "data" / "dataset_split"
    if not dataset_split_dir.exists():
        raise FileNotFoundError(f"data/dataset_split not found at {dataset_split_dir}")
    
    all_data = []
    csv_files = ["train.csv", "val.csv", "test.csv"]
    for csv_file in csv_files:
        file_path = dataset_split_dir / csv_file
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                file_data = list(reader)
                for item in file_data:
                    item["_source_file"] = csv_file
                all_data.extend(file_data)
    
    doc_groups = defaultdict(list)
    for item in all_data:
        syllabus_name = item.get("syllabus_name", "unknown")
        doc_groups[syllabus_name].append(item)
    
    category_qas = {}
    doc_category_qas = {}
    for doc_name, items in doc_groups.items():
        doc_cat_qas = {}
        for item in items:
            q_type = item.get("question_type")
            if q_type != "no answer":
                if q_type not in category_qas:
                    category_qas[q_type] = []
                category_qas[q_type].append((doc_name, item))
                if q_type not in doc_cat_qas:
                    doc_cat_qas[q_type] = []
                doc_cat_qas[q_type].append(item)
        doc_category_qas[doc_name] = doc_cat_qas
    
    total_valid_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    
    doc_valid_qas = {}
    for doc_name, items in doc_groups.items():
        valid_count = 0
        for item in items:
            if item.get("question_type") != "no answer":
                valid_count += 1
        doc_valid_qas[doc_name] = valid_count
    
    all_doc_names = list(doc_groups.keys())
    original_num_docs = len(all_doc_names)
    original_total_qas = len(all_data)
    print(f"SyllabusQA original size: {original_num_docs} documents, {original_total_qas} QAs (from {len(csv_files)} files)")
    print(f"Total valid QAs (excluding 'no answer'): {total_valid_qas}")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        selected_docs = all_doc_names
        print("Using full SyllabusQA dataset")
    else:
        if num_docs is not None:
            if num_docs >= original_num_docs:
                selected_docs = all_doc_names
                print("Using all documents")
            else:
                random.seed(seed)
                selected_docs = random.sample(all_doc_names, num_docs)
                print(f"Sampled {len(selected_docs)} documents (seed={seed})")
            
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                selected_doc_category_qas = {}
                for doc_name in selected_docs:
                    doc_cat_qas = doc_category_qas[doc_name]
                    for cat, items in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for item in items:
                            selected_doc_category_qas[cat].append(item)
                
                if sample_mode == "stratified":
                    sampled_items = stratified_sample_with_reallocation(
                        sample_size, selected_doc_category_qas, seed
                    )
                    
                    for doc_name in selected_docs:
                        doc_items = doc_groups[doc_name]
                        for item in doc_items:
                            if item.get("question_type") == "no answer":
                                sampled_items.append(item)
                    
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("syllabus_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
                
                if sample_mode == "random":
                    all_valid_items = []
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        for item in items:
                            if item.get("question_type") != "no answer":
                                all_valid_items.append(item)
                    
                    sampled_items = random_sample_qas(sample_size, all_valid_items, seed)
                    
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        for item in items:
                            if item.get("question_type") == "no answer":
                                sampled_items.append(item)
                    
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("syllabus_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
        else:
            if sample_mode == "stratified":
                print(f"Using stratified sampling (seed={seed})")
                selected_docs = sample_docs_stratified(
                    sample_size, category_qas, doc_category_qas, all_doc_names, seed
                )
            
            if sample_mode == "random":
                print(f"Using random sampling (seed={seed})")
                selected_docs = sample_docs_random(
                    sample_size, doc_valid_qas, all_doc_names, seed
                )
    
    selected_data = []
    for doc_name in selected_docs:
        selected_data.extend(doc_groups[doc_name])
    
    output_dir.mkdir(parents=True, exist_ok=True)
    for csv_file in csv_files:
        file_data = [item for item in selected_data if item.get("_source_file") == csv_file]
        if file_data:
            for item in file_data:
                item.pop("_source_file", None)
            output_file = output_dir / csv_file
            with open(output_file, "w", encoding="utf-8", newline="") as f:
                if file_data:
                    writer = csv.DictWriter(f, fieldnames=file_data[0].keys())
                    writer.writeheader()
                    writer.writerows(file_data)
            print(f"Saved {len(file_data)} samples to {csv_file}")
    
    syllabi_src = input_dir / "syllabi"
    syllabi_dst = output_dir / "syllabi"
    if syllabi_src.exists():
        syllabi_dst.mkdir(parents=True, exist_ok=True)
        
        syllabus_names = set()
        for doc_name in selected_docs:
            items = doc_groups[doc_name]
            for item in items:
                syllabus_name = item.get("syllabus_name")
                if syllabus_name:
                    syllabus_names.add(syllabus_name)
        
        print(f"Copying syllabi for {len(syllabus_names)} unique syllabus files")
        
        for subdir in ["pdf", "text", "word"]:
            src_subdir = syllabi_src / "syllabi_redacted" / subdir
            dst_subdir = syllabi_dst / "syllabi_redacted" / subdir
            if src_subdir.exists():
                dst_subdir.mkdir(parents=True, exist_ok=True)
                
                for syllabus_name in syllabus_names:
                    for ext in [".pdf", ".txt", ".docx"]:
                        src_file = src_subdir / f"{syllabus_name}{ext}"
                        if src_file.exists():
                            shutil.copy2(src_file, dst_subdir / f"{syllabus_name}{ext}")
                            print(f"Copied {subdir}/{syllabus_name}{ext}")
                            break
    
    sampled_valid_qas = 0
    for doc_name in selected_docs:
        items = doc_groups[doc_name]
        for item in items:
            if item.get("question_type") != "no answer":
                sampled_valid_qas += 1
    
    metadata = {
        "dataset": "SyllabusQA",
        "original_num_docs": original_num_docs,
        "original_total_qas": original_total_qas,
        "original_valid_qas": total_valid_qas,
        "sampled_num_docs": len(selected_docs),
        "sampled_total_qas": len(selected_data),
        "sampled_valid_qas": sampled_valid_qas,
        "sample_size": sample_size,
        "num_docs": num_docs,
        "seed": seed,
        "sample_mode": sample_mode,
        "is_full": is_full,
        "note": "'no answer' type questions are excluded from QA count"
    }
    
    return metadata


def sample_qasper(
    input_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    sample_mode: str = "random"
) -> Dict[str, Any]:
    """Sample Qasper dataset with stratified sampling support."""
    json_files = ["qasper-train-v0.3.json", "qasper-dev-v0.3.json", "qasper-test-v0.3.json"]
    all_paper_ids = []
    paper_data_map = {}
    
    category_qas = {}
    paper_category_qas = {}
    
    for json_file in json_files:
        file_path = input_dir / json_file
        if file_path.exists():
            data = load_json_data(file_path)
            for paper_id, paper_data in data.items():
                all_paper_ids.append(paper_id)
                paper_data_map[paper_id] = (paper_data, json_file)
                
                paper_cat_qas = {}
                if "qas" in paper_data:
                    for qa_item in paper_data["qas"]:
                        is_unanswerable = all(
                            ans.get("answer", {}).get("unanswerable", False)
                            for ans in qa_item.get("answers", [])
                        )
                        if is_unanswerable:
                            continue
                        
                        answer_types = set()
                        for ans in qa_item.get("answers", []):
                            ans_obj = ans.get("answer", {})
                            if ans_obj.get("unanswerable", False):
                                continue
                            if ans_obj.get("extractive_spans"):
                                answer_types.add("extractive")
                            elif ans_obj.get("free_form_answer", "").strip():
                                answer_types.add("free_form")
                            elif ans_obj.get("yes_no") is not None:
                                answer_types.add("yes_no")
                        
                        primary_type = next(iter(answer_types), "extractive")
                        if primary_type not in category_qas:
                            category_qas[primary_type] = []
                        category_qas[primary_type].append((paper_id, qa_item))
                        
                        if primary_type not in paper_cat_qas:
                            paper_cat_qas[primary_type] = []
                        paper_cat_qas[primary_type].append(qa_item)
                
                paper_category_qas[paper_id] = paper_cat_qas
    
    original_num_docs = len(all_paper_ids)
    print(f"Qasper original size: {original_num_docs} documents (from {len(json_files)} files)")
    
    total_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    print(f"Total QAs (excluding unanswerable): {total_qas}")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        selected_ids = all_paper_ids
        print("Using full Qasper dataset")
    else:
        if num_docs is not None:
            if num_docs >= original_num_docs:
                selected_ids = all_paper_ids
                print("Using all documents")
            else:
                if sample_size is not None:
                    paper_qas_count = {}
                    for paper_id in all_paper_ids:
                        count = 0
                        for cat_qas in paper_category_qas[paper_id].values():
                            count += len(cat_qas)
                        paper_qas_count[paper_id] = count
                    
                    random.seed(seed)
                    shuffled_papers = all_paper_ids.copy()
                    random.shuffle(shuffled_papers)
                    shuffled_papers.sort(key=lambda pid: paper_qas_count[pid], reverse=True)
                    
                    selected_ids = shuffled_papers[:num_docs]
                    print(f"Sampled {len(selected_ids)} documents with highest QA counts (seed={seed})")
                else:
                    random.seed(seed)
                    selected_ids = random.sample(all_paper_ids, num_docs)
                    print(f"Sampled {len(selected_ids)} documents (seed={seed})")
            
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                qa_with_indices = []
                for paper_id in selected_ids:
                    paper_data, source_file = paper_data_map[paper_id]
                    for i, qa_item in enumerate(paper_data.get("qas", [])):
                        is_unanswerable = all(
                            ans.get("answer", {}).get("unanswerable", False)
                            for ans in qa_item.get("answers", [])
                        )
                        if not is_unanswerable:
                            answer_types = set()
                            for ans in qa_item.get("answers", []):
                                ans_obj = ans.get("answer", {})
                                if ans_obj.get("unanswerable", False):
                                    continue
                                if ans_obj.get("extractive_spans"):
                                    answer_types.add("extractive")
                                elif ans_obj.get("free_form_answer", "").strip():
                                    answer_types.add("free_form")
                                elif ans_obj.get("yes_no") is not None:
                                    answer_types.add("yes_no")
                            primary_type = next(iter(answer_types), "extractive")
                            qa_with_indices.append((paper_id, primary_type, i, qa_item))
                
                if sample_mode == "stratified":
                    selected_doc_category_qas = {}
                    for paper_id, cat, i, qa_item in qa_with_indices:
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        selected_doc_category_qas[cat].append((paper_id, i, qa_item))
                    
                    category_targets, should_fallback = calculate_category_targets(
                        sample_size, sorted(selected_doc_category_qas.keys())
                    )
                    
                    if not should_fallback:
                        random.seed(seed)
                        sampled_qas_indices = set()
                        remaining_quota = sample_size
                        
                        category_actual = {}
                        cats = sorted(selected_doc_category_qas.keys())
                        for cat in cats:
                            if cat not in category_targets or category_targets[cat] <= 0:
                                category_actual[cat] = 0
                                continue
                            
                            cat_qas = selected_doc_category_qas[cat].copy()
                            random.shuffle(cat_qas)
                            sample_count = min(len(cat_qas), category_targets[cat])
                            category_actual[cat] = sample_count
                            remaining_quota -= sample_count
                            
                            for paper_id, i, qa_item in cat_qas[:sample_count]:
                                sampled_qas_indices.add((paper_id, i))
                        
                        if remaining_quota > 0:
                            print(f"Reallocating remaining {remaining_quota} QA(s) to categories with available QAs")
                            
                            category_available = {}
                            for cat in cats:
                                if cat in selected_doc_category_qas:
                                    total_available = len(selected_doc_category_qas[cat])
                                    used = category_actual.get(cat, 0)
                                    category_available[cat] = total_available - used
                            
                            while remaining_quota > 0:
                                allocated_this_round = 0
                                for cat in cats:
                                    if remaining_quota <= 0:
                                        break
                                    if category_available.get(cat, 0) > 0:
                                        cat_qas = selected_doc_category_qas[cat].copy()
                                        random.shuffle(cat_qas)
                                        for paper_id, i, qa_item in cat_qas:
                                            if (paper_id, i) not in sampled_qas_indices:
                                                sampled_qas_indices.add((paper_id, i))
                                                category_actual[cat] += 1
                                                category_available[cat] -= 1
                                                remaining_quota -= 1
                                                allocated_this_round += 1
                                                break
                                
                                if allocated_this_round == 0:
                                    print(f"Warning: No more QAs available to sample. Stopping with {remaining_quota} unallocated.")
                                    break
                        
                        print("Actual category counts after reallocation:")
                        for cat in cats:
                            print(f"  {cat}: {category_actual.get(cat, 0)} QAs")
                    
                    for paper_id in selected_ids:
                        paper_data, source_file = paper_data_map[paper_id]
                        new_qas = []
                        
                        for i, qa_item in enumerate(paper_data.get("qas", [])):
                            is_unanswerable = all(
                                ans.get("answer", {}).get("unanswerable", False)
                                for ans in qa_item.get("answers", [])
                            )
                            if is_unanswerable or (paper_id, i) in sampled_qas_indices:
                                new_qas.append(qa_item)
                        
                        paper_data["qas"] = new_qas
                
                if sample_mode == "random":
                    sampled_qas = random_sample_qas(sample_size, qa_with_indices, seed)
                    
                    keep_qas_indices = set()
                    for paper_id, cat, i, qa_item in sampled_qas:
                        keep_qas_indices.add((paper_id, i))
                    
                    for paper_id in selected_ids:
                        paper_data, source_file = paper_data_map[paper_id]
                        new_qas = []
                        for i, qa_item in enumerate(paper_data.get("qas", [])):
                            is_unanswerable = all(
                                ans.get("answer", {}).get("unanswerable", False)
                                for ans in qa_item.get("answers", [])
                            )
                            if is_unanswerable or (paper_id, i) in keep_qas_indices:
                                new_qas.append(qa_item)
                        paper_data["qas"] = new_qas
        else:
            paper_qas_count = {}
            for paper_id in all_paper_ids:
                count = 0
                for cat_qas in paper_category_qas[paper_id].values():
                    count += len(cat_qas)
                paper_qas_count[paper_id] = count
            
            if sample_mode == "stratified":
                print(f"Using stratified sampling (seed={seed})")
                selected_ids = sample_docs_stratified(
                    sample_size, category_qas, paper_category_qas, all_paper_ids, seed
                )
            
            if sample_mode == "random":
                print(f"Using random sampling (seed={seed})")
                selected_ids = sample_docs_random(
                    sample_size, paper_qas_count, all_paper_ids, seed
                )
    
    output_dir.mkdir(parents=True, exist_ok=True)
    data_by_file = {}
    for paper_id in selected_ids:
        paper_data, source_file = paper_data_map[paper_id]
        if source_file not in data_by_file:
            data_by_file[source_file] = {}
        data_by_file[source_file][paper_id] = paper_data
    
    for json_file, output_data in data_by_file.items():
        output_file = output_dir / json_file
        save_json_data(output_data, output_file)
        print(f"Saved {len(output_data)} papers to {json_file}")
    
    sampled_qas = 0
    for paper_id in selected_ids:
        paper_data, source_file = paper_data_map[paper_id]
        if "qas" in paper_data:
            for qa_item in paper_data["qas"]:
                is_unanswerable = all(
                    ans.get("answer", {}).get("unanswerable", False)
                    for ans in qa_item.get("answers", [])
                )
                if not is_unanswerable:
                    sampled_qas += 1
    
    metadata = {
        "dataset": "Qasper",
        "original_num_docs": original_num_docs,
        "original_total_qas": total_qas,
        "sampled_num_docs": len(selected_ids),
        "sampled_total_qas": sampled_qas,
        "sample_size": sample_size,
        "num_docs": num_docs,
        "seed": seed,
        "sample_mode": sample_mode,
        "is_full": is_full,
        "note": "Unanswerable questions are excluded from QA count"
    }
    
    return metadata


def sample_financebench(
    input_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    sample_mode: str = "random"
) -> Dict[str, Any]:
    """Sample Financebench dataset with stratified sampling support."""
    from collections import defaultdict
    
    input_file = input_dir / "data" / "financebench_open_source.jsonl"
    if not input_file.exists():
        raise FileNotFoundError(f"financebench_open_source.jsonl not found at {input_file}")
    
    data = load_jsonl_data(input_file)
    
    doc_groups = defaultdict(list)
    for item in data:
        doc_name = item.get("doc_name", "unknown")
        doc_groups[doc_name].append(item)
    
    category_qas = {}
    doc_category_qas = {}
    for doc_name, items in doc_groups.items():
        doc_cat_qas = {}
        for item in items:
            q_type = item.get("question_type", "domain-relevant")
            if q_type not in category_qas:
                category_qas[q_type] = []
            category_qas[q_type].append((doc_name, item))
            if q_type not in doc_cat_qas:
                doc_cat_qas[q_type] = []
            doc_cat_qas[q_type].append(item)
        doc_category_qas[doc_name] = doc_cat_qas
    
    all_doc_names = list(doc_groups.keys())
    original_num_docs = len(all_doc_names)
    original_total_qas = len(data)
    total_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    print(f"Financebench original size: {original_num_docs} documents, {original_total_qas} QAs")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        selected_docs = all_doc_names
        print("Using full Financebench dataset")
    else:
        if num_docs is not None:
            if num_docs >= original_num_docs:
                selected_docs = all_doc_names
                print("Using all documents")
            else:
                random.seed(seed)
                sorted_docs = sorted(all_doc_names, key=lambda x: len(doc_groups[x]), reverse=True)
                random.shuffle(sorted_docs)
                selected_docs = sorted(sorted_docs, key=lambda x: len(doc_groups[x]), reverse=True)[:num_docs]
                print(f"Sampled {len(selected_docs)} documents with highest QA counts (seed={seed})")
                print("Selected documents:")
                for doc in selected_docs:
                    print(f"  {doc}: {len(doc_groups[doc])} QAs")
            
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                selected_doc_category_qas = {}
                for doc_name in selected_docs:
                    doc_cat_qas = doc_category_qas[doc_name]
                    for cat, items in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for item in items:
                            selected_doc_category_qas[cat].append(item)
                
                if sample_mode == "stratified":
                    sampled_items = stratified_sample_with_reallocation(
                        sample_size, selected_doc_category_qas, seed
                    )
                    
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("doc_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
                
                if sample_mode == "random":
                    all_items = []
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        all_items.extend(items)
                    
                    sampled_items = random_sample_qas(sample_size, all_items, seed)
                    
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("doc_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
        else:
            doc_qas_count = {doc_name: len(items) for doc_name, items in doc_groups.items()}
            
            if sample_mode == "stratified":
                print(f"Using stratified sampling (seed={seed})")
                selected_docs = sample_docs_stratified(
                    sample_size, category_qas, doc_category_qas, all_doc_names, seed
                )
            
            if sample_mode == "random":
                print(f"Using random sampling (seed={seed})")
                selected_docs = sample_docs_random(
                    sample_size, doc_qas_count, all_doc_names, seed
                )
    
    selected_data = []
    for doc_name in selected_docs:
        selected_data.extend(doc_groups[doc_name])
    
    output_file = output_dir / "financebench_open_source.jsonl"
    save_jsonl_data(selected_data, output_file)
    
    pdfs_src = input_dir / "pdfs"
    pdfs_dst = output_dir / "pdfs"
    
    if pdfs_src.exists():
        pdfs_dst.mkdir(parents=True, exist_ok=True)
        
        for doc_name in selected_docs:
            src_pdf = pdfs_src / f"{doc_name}.pdf"
            if src_pdf.exists():
                shutil.copy2(src_pdf, pdfs_dst / f"{doc_name}.pdf")
                print(f"Copied PDF: {doc_name}.pdf")
    
    metadata = {
        "dataset": "Financebench",
        "original_num_docs": original_num_docs,
        "original_total_qas": original_total_qas,
        "sampled_num_docs": len(selected_docs),
        "sampled_total_qas": len(selected_data),
        "sample_size": sample_size,
        "num_docs": num_docs,
        "seed": seed,
        "sample_mode": sample_mode,
        "is_full": is_full
    }
    
    return metadata


DATASET_SAMPLERS = {
    "Locomo": sample_locomo,
    "SyllabusQA": sample_syllabusqa,
    "Qasper": sample_qasper,
    "FinanceBench": sample_financebench,
}


def sample_dataset(
    dataset_name: str,
    input_dir: Path,
    output_dir: Path,
    sample_size: Optional[int] = None,
    num_docs: Optional[int] = None,
    seed: int = 42,
    sample_mode: str = "stratified"
) -> bool:
    """Sample a single dataset."""
    if dataset_name not in DATASET_SAMPLERS:
        print(f"Unknown dataset: {dataset_name}")
        return False
    
    print(f"\nProcessing {dataset_name}...")
    print(f"Input: {input_dir}")
    print(f"Output: {output_dir}")
    
    try:
        sampler = DATASET_SAMPLERS[dataset_name]
        metadata = sampler(input_dir, output_dir, sample_size, num_docs, seed, sample_mode)
        
        metadata_file = output_dir / "sampling_metadata.json"
        save_json_data(metadata, metadata_file)
        print(f"✓ Saved metadata to {metadata_file}")
        
        return True
    except Exception as e:
        print(f"Error sampling {dataset_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Sample datasets for RAG benchmark"
    )
    parser.add_argument(
        "--dataset", "-d",
        type=str,
        choices=list(DATASET_SAMPLERS.keys()) + ["all"],
        default="all",
        help="Dataset to sample (default: all)"
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=Path,
        default=Path(__file__).parent.parent / "raw_data",
        help="Input directory with full datasets (default: raw_data/)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path(__file__).parent.parent / "datasets",
        help="Output directory for sampled datasets (default: datasets/)"
    )
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
        help="Use full dataset (overrides --sample-size)"
    )
    parser.add_argument(
        "--sample-mode",
        type=str,
        choices=["stratified", "random"],
        default="stratified",
        help="Sampling mode (default: stratified)"
    )
    
    args = parser.parse_args()
    
    if args.full:
        args.sample_size = None
        args.num_docs = None
    
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    
    datasets = (
        list(DATASET_SAMPLERS.keys()) 
        if args.dataset == "all" 
        else [args.dataset]
    )
    
    print("=" * 60)
    print("RAG Benchmark Dataset Sampler")
    print("=" * 60)
    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"Sample size: {args.sample_size if args.sample_size else 'full'}")
    print(f"Number of docs: {args.num_docs if args.num_docs else 'not set'}")
    print(f"Sample mode: {args.sample_mode}")
    print(f"Random seed: {args.seed}")
    print(f"Datasets: {', '.join(datasets)}")
    print("=" * 60)
    
    success_count = 0
    for dataset in datasets:
        dataset_input_dir = input_dir / dataset
        dataset_output_dir = output_dir / dataset
        
        if sample_dataset(
            dataset,
            dataset_input_dir,
            dataset_output_dir,
            args.sample_size,
            args.num_docs,
            args.seed,
            args.sample_mode
        ):
            success_count += 1
    
    print("\n" + "=" * 60)
    print(f"Sampling complete: {success_count}/{len(datasets)} successful")
    print("=" * 60)
    
    return 0 if success_count == len(datasets) else 1


if __name__ == "__main__":
    sys.exit(main())
