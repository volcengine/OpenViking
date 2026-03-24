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
from typing import Any, Dict, List, Optional

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
    
    # 按类别分组 QA
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
    
    # 计算总 QA 数
    total_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    print(f"Total QAs (excluding category 5): {total_qas}")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  Category {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        # 全量模式
        selected_docs = data
        print("Using full Locomo dataset")
    else:
        # 先抽样文档
        if num_docs is not None:
            # 按文档数抽样
            if num_docs >= original_num_docs:
                selected_docs = data
                print("Using all documents")
            else:
                random.seed(seed)
                selected_docs = random.sample(data, num_docs)
                print(f"Sampled {len(selected_docs)} documents (seed={seed})")
            
            # 如果同时指定了 sample_size，则在选中的文档中再抽样 QA
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                # 在选中的文档中，按类别重新分组 QA
                selected_doc_category_qas = {}
                selected_doc_indices = [data.index(doc) for doc in selected_docs]
                
                for doc_idx in selected_doc_indices:
                    doc = data[doc_idx]
                    doc_cat_qas = doc_category_qas[doc_idx]
                    for cat, qs in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for q in qs:
                            selected_doc_category_qas[cat].append((doc, q))
                
                if sample_mode == "stratified":
                    # 在选中文档中分层抽样 QA
                    num_categories = len(selected_doc_category_qas)
                    if num_categories > 0:
                        base_per_category = sample_size // num_categories
                        remainder = sample_size % num_categories
                        
                        if base_per_category == 0:
                            print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                            print("Falling back to random sampling")
                            sample_mode = "random"
                        else:
                            category_targets = {}
                            cats = sorted(selected_doc_category_qas.keys())
                            for i, cat in enumerate(cats):
                                category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                            
                            if remainder > 0:
                                print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                                print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                            
                            print("Category targets in selected docs:")
                            for cat in cats:
                                print(f"  Category {cat}: {category_targets[cat]} QAs")
                            
                            # 在每个文档中，按类别抽样 QA
                            random.seed(seed)
                            for doc in selected_docs:
                                doc_idx = data.index(doc)
                                doc_cat_qas = doc_category_qas[doc_idx]
                                
                                # 创建新的 qa 列表，只保留抽样的 QA
                                new_qas = []
                                for cat, qs in doc_cat_qas.items():
                                    if cat in category_targets and category_targets[cat] > 0:
                                        # 从该类别的 QA 中抽样
                                        random.shuffle(qs)
                                        sample_count = min(len(qs), category_targets[cat])
                                        # 找到原始的 qa 列表中的对应项
                                        doc_all_qas = doc.get("qa", [])
                                        sampled_q_indices = []
                                        for q in qs[:sample_count]:
                                            # 找到 q 在原始 qa 列表中的索引
                                            for i, qa_item in enumerate(doc_all_qas):
                                                if qa_item == q:
                                                    sampled_q_indices.append(i)
                                                    break
                                        # 添加所有原始 qa 中在 sampled_q_indices 中的项
                                        for i, qa_item in enumerate(doc_all_qas):
                                            if i in sampled_q_indices or str(qa_item.get("category")) == "5":
                                                new_qas.append(qa_item)
                                        category_targets[cat] -= sample_count
                                    else:
                                        # 如果该类别没有目标，只保留 category 5 的 QA
                                        doc_all_qas = doc.get("qa", [])
                                        for qa_item in doc_all_qas:
                                            if str(qa_item.get("category")) == "5":
                                                new_qas.append(qa_item)
                                doc["qa"] = new_qas
                
                if sample_mode == "random":
                    # 在选中文档中随机抽样 QA
                    random.seed(seed)
                    # 收集所有有效 QA（排除 category 5）
                    all_valid_qas = []
                    for doc in selected_docs:
                        doc_idx = data.index(doc)
                        doc_cat_qas = doc_category_qas[doc_idx]
                        for cat, qs in doc_cat_qas.items():
                            for q in qs:
                                all_valid_qas.append((doc, q))
                    
                    # 随机抽样
                    if len(all_valid_qas) > sample_size:
                        sampled_qas = random.sample(all_valid_qas, sample_size)
                    else:
                        sampled_qas = all_valid_qas
                    
                    # 创建一个集合来标记哪些 QA 需要保留
                    keep_qas = set()
                    for doc, q in sampled_qas:
                        keep_qas.add((doc, q))
                    
                    # 过滤每个文档的 QA
                    for doc in selected_docs:
                        new_qas = []
                        for q in doc.get("qa", []):
                            if (doc, q) in keep_qas or str(q.get("category")) == "5":
                                new_qas.append(q)
                        doc["qa"] = new_qas
        else:
            if sample_mode == "stratified":
                # 分层抽样
                print(f"Using stratified sampling (seed={seed})")
                random.seed(seed)
                
                num_categories = len(categories)
                base_per_category = sample_size // num_categories
                remainder = sample_size % num_categories
                
                if base_per_category == 0:
                    print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                    print("Falling back to random sampling")
                    sample_mode = "random"
                else:
                    category_targets = {}
                    for i, cat in enumerate(categories):
                        category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                    
                    if remainder > 0:
                        print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                        print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                    
                    print("Category targets:")
                    for cat in categories:
                        print(f"  Category {cat}: {category_targets[cat]} QAs")
                    
                    selected_docs = []
                    selected_qas_by_cat = {cat: 0 for cat in categories}
                    doc_used = [False] * len(data)
                    
                    for cat in categories:
                        target = category_targets[cat]
                        if target == 0:
                            continue
                        
                        # 获取该类别的所有 QA 并打乱
                        cat_qas = category_qas[cat].copy()
                        random.shuffle(cat_qas)
                        
                        for doc, q in cat_qas:
                            doc_idx = data.index(doc)
                            if doc_used[doc_idx]:
                                continue
                            
                            # 检查添加这个文档是否会超过目标
                            new_count = selected_qas_by_cat[cat] + doc_category_qas[doc_idx].get(cat, 0)
                            if new_count > target:
                                continue
                            
                            selected_docs.append(doc)
                            doc_used[doc_idx] = True
                            
                            # 更新各类别的计数
                            for c, qs in doc_category_qas[doc_idx].items():
                                selected_qas_by_cat[c] += len(qs)
                            
                            if selected_qas_by_cat[cat] >= target:
                                break
                    
                    # 检查是否达到目标
                    total_selected = sum(selected_qas_by_cat.values())
                    print(f"Sampled {len(selected_docs)} documents with {total_selected} QAs")
                    for cat in categories:
                        print(f"  Category {cat}: {selected_qas_by_cat[cat]} QAs (target: {category_targets[cat]})")
            
            if sample_mode == "random":
                # 按 QA 数抽样，但保持文档完整性
                # 策略：随机选文档，直到达到或超过 sample_size 个 QA（过滤 category 5）
                print(f"Using random sampling (seed={seed})")
                random.seed(seed)
                doc_indices = list(range(original_num_docs))
                random.shuffle(doc_indices)
                
                selected_docs = []
                selected_qas_count = 0
                
                for idx in doc_indices:
                    doc = data[idx]
                    # 计算文档中的有效 QA 数（排除 category 5）
                    doc_qas = 0
                    for q in doc.get("qa", []):
                        if str(q.get("category")) != "5":
                            doc_qas += 1
                    
                    if selected_qas_count + doc_qas <= sample_size or not selected_docs:
                        selected_docs.append(doc)
                        selected_qas_count += doc_qas
                    else:
                        # 如果已经达到样本量，停止
                        if selected_qas_count >= sample_size:
                            break
                
                print(f"Sampled {len(selected_docs)} documents with {selected_qas_count} QAs (seed={seed})")
    
    # 保存数据 - 保持完整文档结构（包含所有 QA，包括 category 5）
    # adapter 会在加载时过滤 category 5
    output_data = selected_docs
    
    # Save data
    output_file = output_dir / "locomo10.json"
    save_json_data(output_data, output_file)
    
    # 计算抽样后的 QA 数（过滤 category 5）
    sampled_qas = 0
    for doc in selected_docs:
        if "qa" in doc:
            for q in doc["qa"]:
                if str(q.get("category")) != "5":
                    sampled_qas += 1
    
    # Save metadata
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
    # Read all CSV files from data/dataset_split/
    dataset_split_dir = input_dir / "data" / "dataset_split"
    if not dataset_split_dir.exists():
        raise FileNotFoundError(f"data/dataset_split not found at {dataset_split_dir}")
    
    # Read all CSV files
    import csv
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
    
    # 按 syllabus_name 分组（每个 syllabus_name 对应一个文档）
    from collections import defaultdict
    doc_groups = defaultdict(list)
    for item in all_data:
        syllabus_name = item.get("syllabus_name", "unknown")
        doc_groups[syllabus_name].append(item)
    
    # 按类别分组 QA
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
    
    # 计算有效 QA 数
    total_valid_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    
    # 计算每个文档的有效 QA 数（确保在所有代码路径中都有定义）
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
        # 全量模式
        selected_docs = all_doc_names
        print("Using full SyllabusQA dataset")
    else:
        # 先抽样文档
        if num_docs is not None:
            # 按文档数抽样
            if num_docs >= original_num_docs:
                selected_docs = all_doc_names
                print("Using all documents")
            else:
                random.seed(seed)
                selected_docs = random.sample(all_doc_names, num_docs)
                print(f"Sampled {len(selected_docs)} documents (seed={seed})")
            
            # 如果同时指定了 sample_size，则在选中的文档中再抽样 QA
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                # 在选中的文档中，按类别重新分组 QA
                selected_doc_category_qas = {}
                for doc_name in selected_docs:
                    doc_cat_qas = doc_category_qas[doc_name]
                    for cat, items in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for item in items:
                            selected_doc_category_qas[cat].append((doc_name, item))
                
                if sample_mode == "stratified":
                    # 在选中文档中分层抽样 QA
                    num_categories = len(selected_doc_category_qas)
                    if num_categories > 0:
                        base_per_category = sample_size // num_categories
                        remainder = sample_size % num_categories
                        
                        if base_per_category == 0:
                            print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                            print("Falling back to random sampling")
                            sample_mode = "random"
                        else:
                            category_targets = {}
                            cats = sorted(selected_doc_category_qas.keys())
                            for i, cat in enumerate(cats):
                                category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                            
                            if remainder > 0:
                                print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                                print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                            
                            print("Category targets in selected docs:")
                            for cat in cats:
                                print(f"  {cat}: {category_targets[cat]} QAs")
                            
                            # 先收集所有选中文档中的有效 QA
                            all_valid_qas_with_info = []
                            for doc_name in selected_docs:
                                doc_items = doc_groups[doc_name]
                                doc_cat_qas = doc_category_qas[doc_name]
                                for cat, items in doc_cat_qas.items():
                                    for item in items:
                                        all_valid_qas_with_info.append((doc_name, cat, item))
                            
                            # 从所有 QA 中分层抽样，支持剩余配额重新分配
                            random.seed(seed)
                            sampled_items = []
                            remaining_quota = sample_size
                            
                            # 第一轮：按目标抽样，但不超过每个类别的可用数量
                            category_actual = {}
                            for cat in cats:
                                if cat not in category_targets or category_targets[cat] <= 0:
                                    category_actual[cat] = 0
                                    continue
                                
                                cat_qas = [item for doc_name, c, item in all_valid_qas_with_info if c == cat]
                                random.shuffle(cat_qas)
                                sample_count = min(len(cat_qas), category_targets[cat])
                                category_actual[cat] = sample_count
                                remaining_quota -= sample_count
                                sampled_items.extend(cat_qas[:sample_count])
                            
                            # 第二轮：如果还有剩余配额，分配给还有可用 QA 的类别
                            if remaining_quota > 0:
                                print(f"Reallocating remaining {remaining_quota} QA(s) to categories with available QAs")
                                
                                # 为每个类别计算还有多少可用 QA
                                category_available = {}
                                for cat in cats:
                                    cat_qas = [item for doc_name, c, item in all_valid_qas_with_info if c == cat]
                                    total_available = len(cat_qas)
                                    used = category_actual.get(cat, 0)
                                    category_available[cat] = total_available - used
                                
                                # 循环分配剩余配额
                                while remaining_quota > 0:
                                    allocated_this_round = 0
                                    for cat in cats:
                                        if remaining_quota <= 0:
                                            break
                                        if category_available.get(cat, 0) > 0:
                                            # 从该类别再抽样一个
                                            cat_qas = [item for doc_name, c, item in all_valid_qas_with_info if c == cat and item not in sampled_items]
                                            if cat_qas:
                                                random.shuffle(cat_qas)
                                                sampled_items.append(cat_qas[0])
                                                category_actual[cat] += 1
                                                category_available[cat] -= 1
                                                remaining_quota -= 1
                                                allocated_this_round += 1
                                    
                                    # 如果这一轮没有分配任何配额，说明没有更多可用 QA 了
                                    if allocated_this_round == 0:
                                        print(f"Warning: No more QAs available to sample. Stopping with {remaining_quota} unallocated.")
                                        break
                            
                            print("Actual category counts after reallocation:")
                            for cat in cats:
                                print(f"  {cat}: {category_actual.get(cat, 0)} QAs")
                            
                            # 保留 "no answer" 类型的 QA
                            for doc_name in selected_docs:
                                doc_items = doc_groups[doc_name]
                                for item in doc_items:
                                    if item.get("question_type") == "no answer":
                                        sampled_items.append(item)
                            
                            # 更新 doc_groups，只保留抽样的 items
                            new_doc_groups = defaultdict(list)
                            for item in sampled_items:
                                doc_name = item.get("syllabus_name", "unknown")
                                new_doc_groups[doc_name].append(item)
                            doc_groups = new_doc_groups
                
                if sample_mode == "random":
                    # 在选中文档中随机抽样 QA
                    random.seed(seed)
                    # 收集所有有效 QA（排除 no answer）
                    all_valid_items = []
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        for item in items:
                            if item.get("question_type") != "no answer":
                                all_valid_items.append(item)
                    
                    # 随机抽样
                    if len(all_valid_items) > sample_size:
                        sampled_items = random.sample(all_valid_items, sample_size)
                    else:
                        sampled_items = all_valid_items
                    
                    # 添加 "no answer" 类型的 QA
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        for item in items:
                            if item.get("question_type") == "no answer":
                                sampled_items.append(item)
                    
                    # 更新 doc_groups，只保留抽样的 items
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("syllabus_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
        else:
            if sample_mode == "stratified":
                # 分层抽样
                print(f"Using stratified sampling (seed={seed})")
                random.seed(seed)
                
                num_categories = len(categories)
                base_per_category = sample_size // num_categories
                remainder = sample_size % num_categories
                
                if base_per_category == 0:
                    print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                    print("Falling back to random sampling")
                    sample_mode = "random"
                else:
                    category_targets = {}
                    for i, cat in enumerate(categories):
                        category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                    
                    if remainder > 0:
                        print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                        print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                    
                    print("Category targets:")
                    for cat in categories:
                        print(f"  {cat}: {category_targets[cat]} QAs")
                    
                    selected_docs = []
                    selected_qas_by_cat = {cat: 0 for cat in categories}
                    doc_used = {doc_name: False for doc_name in all_doc_names}
                    
                    for cat in categories:
                        target = category_targets[cat]
                        if target == 0:
                            continue
                        
                        # 获取该类别的所有 QA 并打乱
                        cat_qas = category_qas[cat].copy()
                        random.shuffle(cat_qas)
                        
                        for doc_name, item in cat_qas:
                            if doc_used[doc_name]:
                                continue
                            
                            # 检查添加这个文档是否会超过目标
                            doc_cat_qas = doc_category_qas[doc_name]
                            new_count = selected_qas_by_cat[cat] + len(doc_cat_qas.get(cat, []))
                            if new_count > target:
                                continue
                            
                            selected_docs.append(doc_name)
                            doc_used[doc_name] = True
                            
                            # 更新各类别的计数
                            for c, qs in doc_cat_qas.items():
                                selected_qas_by_cat[c] += len(qs)
                            
                            if selected_qas_by_cat[cat] >= target:
                                break
                    
                    # 检查是否达到目标
                    total_selected = sum(selected_qas_by_cat.values())
                    print(f"Sampled {len(selected_docs)} documents with {total_selected} QAs")
                    for cat in categories:
                        print(f"  {cat}: {selected_qas_by_cat[cat]} QAs (target: {category_targets[cat]})")
            
            if sample_mode == "random":
                # 按 QA 数抽样，但保持文档完整性（只计算有效 QA）
                print(f"Using random sampling (seed={seed})")
                random.seed(seed)
                shuffled_docs = all_doc_names.copy()
                random.shuffle(shuffled_docs)
                
                selected_docs = []
                selected_qas_count = 0
                
                for doc_name in shuffled_docs:
                    doc_qas = doc_valid_qas[doc_name]
                    
                    if doc_qas == 0:
                        continue
                    
                    if selected_qas_count + doc_qas <= sample_size or not selected_docs:
                        selected_docs.append(doc_name)
                        selected_qas_count += doc_qas
                    else:
                        # 如果已经达到样本量，停止
                        if selected_qas_count >= sample_size:
                            break
                
                print(f"Sampled {len(selected_docs)} documents with {selected_qas_count} valid QAs (seed={seed})")
    
    # 构建选中的数据
    selected_data = []
    for doc_name in selected_docs:
        selected_data.extend(doc_groups[doc_name])
    
    # Save data - split by source file
    output_dir.mkdir(parents=True, exist_ok=True)
    for csv_file in csv_files:
        file_data = [item for item in selected_data if item.get("_source_file") == csv_file]
        if file_data:
            # Remove _source_file field
            for item in file_data:
                item.pop("_source_file", None)
            output_file = output_dir / csv_file
            with open(output_file, "w", encoding="utf-8", newline="") as f:
                if file_data:
                    writer = csv.DictWriter(f, fieldnames=file_data[0].keys())
                    writer.writeheader()
                    writer.writerows(file_data)
            print(f"Saved {len(file_data)} samples to {csv_file}")
    
    # Copy only required syllabi files
    syllabi_src = input_dir / "syllabi"
    syllabi_dst = output_dir / "syllabi"
    if syllabi_src.exists():
        syllabi_dst.mkdir(parents=True, exist_ok=True)
        
        # 提取所有唯一的 syllabus_name 从抽样的数据中
        syllabus_names = set()
        for doc_name in selected_docs:
            items = doc_groups[doc_name]
            for item in items:
                syllabus_name = item.get("syllabus_name")
                if syllabus_name:
                    syllabus_names.add(syllabus_name)
        
        print(f"Copying syllabi for {len(syllabus_names)} unique syllabus files")
        
        # 只复制需要的 syllabus 文件（支持 pdf, text, word 三种格式
        for subdir in ["pdf", "text", "word"]:
            src_subdir = syllabi_src / "syllabi_redacted" / subdir
            dst_subdir = syllabi_dst / "syllabi_redacted" / subdir
            if src_subdir.exists():
                dst_subdir.mkdir(parents=True, exist_ok=True)
                
                # 复制对应的文件
                for syllabus_name in syllabus_names:
                    # 查找对应的文件
                    for ext in [".pdf", ".txt", ".docx"]:
                        src_file = src_subdir / f"{syllabus_name}{ext}"
                        if src_file.exists():
                            shutil.copy2(src_file, dst_subdir / f"{syllabus_name}{ext}")
                            print(f"Copied {subdir}/{syllabus_name}{ext}")
                            break
    
    # 计算抽样后的有效 QA 数（排除 no answer）
    sampled_valid_qas = 0
    for doc_name in selected_docs:
        items = doc_groups[doc_name]
        for item in items:
            if item.get("question_type") != "no answer":
                sampled_valid_qas += 1
    
    # Save metadata
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
    # Read all JSON files and keep track of source file
    json_files = ["qasper-train-v0.3.json", "qasper-dev-v0.3.json", "qasper-test-v0.3.json"]
    all_paper_ids = []
    paper_data_map = {}  # paper_id -> (paper_data, source_file)
    
    # 按 answer type 分组 QA
    category_qas = {}
    paper_category_qas = {}
    
    for json_file in json_files:
        file_path = input_dir / json_file
        if file_path.exists():
            data = load_json_data(file_path)
            for paper_id, paper_data in data.items():
                all_paper_ids.append(paper_id)
                paper_data_map[paper_id] = (paper_data, json_file)
                
                # 按 answer type 分组
                paper_cat_qas = {}
                if "qas" in paper_data:
                    for qa_item in paper_data["qas"]:
                        # 检查是否所有 answer 都是 unanswerable
                        is_unanswerable = all(
                            ans.get("answer", {}).get("unanswerable", False)
                            for ans in qa_item.get("answers", [])
                        )
                        if is_unanswerable:
                            continue
                        
                        # 确定 answer type
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
                        
                        # 选择第一个类型作为主要类型
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
    
    # 计算总 QA 数
    total_qas = sum(len(qas) for qas in category_qas.values())
    categories = sorted(category_qas.keys())
    print(f"Total QAs (excluding unanswerable): {total_qas}")
    print(f"Categories: {categories}")
    for cat in categories:
        print(f"  {cat}: {len(category_qas[cat])} QAs")
    
    is_full = (sample_size is None and num_docs is None)
    if is_full:
        # 全量模式
        selected_ids = all_paper_ids
        print("Using full Qasper dataset")
    else:
        # 先抽样文档
        if num_docs is not None:
            # 按文档数抽样
            if num_docs >= original_num_docs:
                selected_ids = all_paper_ids
                print("Using all documents")
            else:
                # 如果同时指定了 sample_size，则优先选择 QA 多的文档
                if sample_size is not None:
                    # 计算每个文档的有效 QA 数
                    paper_qas_count = {}
                    for paper_id in all_paper_ids:
                        count = 0
                        for cat_qas in paper_category_qas[paper_id].values():
                            count += len(cat_qas)
                        paper_qas_count[paper_id] = count
                    
                    # 按 QA 数量排序，优先选择 QA 多的文档
                    random.seed(seed)
                    # 先随机打乱，然后按 QA 数量排序
                    shuffled_papers = all_paper_ids.copy()
                    random.shuffle(shuffled_papers)
                    shuffled_papers.sort(key=lambda pid: paper_qas_count[pid], reverse=True)
                    
                    selected_ids = shuffled_papers[:num_docs]
                    print(f"Sampled {len(selected_ids)} documents with highest QA counts (seed={seed})")
                else:
                    random.seed(seed)
                    selected_ids = random.sample(all_paper_ids, num_docs)
                    print(f"Sampled {len(selected_ids)} documents (seed={seed})")
            
            # 如果同时指定了 sample_size，则在选中的文档中再抽样 QA
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                # 首先收集所有选中文档中的所有 QA（排除 unanswerable），并为每个 QA 分配索引
                qa_with_indices = []
                qa_index_map = {}  # (paper_id, index) -> qa_item
                idx = 0
                for paper_id in selected_ids:
                    paper_data, source_file = paper_data_map[paper_id]
                    for i, qa_item in enumerate(paper_data.get("qas", [])):
                        # 检查是否所有 answer 都是 unanswerable
                        is_unanswerable = all(
                            ans.get("answer", {}).get("unanswerable", False)
                            for ans in qa_item.get("answers", [])
                        )
                        if not is_unanswerable:
                            # 确定 answer type
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
                            qa_index_map[(paper_id, i)] = qa_item
                
                if sample_mode == "stratified":
                    # 在选中文档中分层抽样 QA
                    # 先按类别分组
                    selected_doc_category_qas = {}
                    for paper_id, cat, i, qa_item in qa_with_indices:
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        selected_doc_category_qas[cat].append((paper_id, i, qa_item))
                    
                    num_categories = len(selected_doc_category_qas)
                    if num_categories > 0:
                        base_per_category = sample_size // num_categories
                        remainder = sample_size % num_categories
                        
                        if base_per_category == 0:
                            print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                            print("Falling back to random sampling")
                            sample_mode = "random"
                        else:
                            category_targets = {}
                            cats = sorted(selected_doc_category_qas.keys())
                            for i, cat in enumerate(cats):
                                category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                            
                            if remainder > 0:
                                print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                                print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                            
                            print("Category targets in selected docs:")
                            for cat in cats:
                                print(f"  {cat}: {category_targets[cat]} QAs")
                            
                            # 从所有 QA 中分层抽样，支持剩余配额重新分配
                            random.seed(seed)
                            sampled_qas_indices = set()
                            remaining_quota = sample_size
                            
                            # 第一轮：按目标抽样，但不超过每个类别的可用数量
                            category_actual = {}
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
                            
                            # 第二轮：如果还有剩余配额，分配给还有可用 QA 的类别
                            if remaining_quota > 0:
                                print(f"Reallocating remaining {remaining_quota} QA(s) to categories with available QAs")
                                
                                # 为每个类别计算还有多少可用 QA
                                category_available = {}
                                for cat in cats:
                                    if cat in selected_doc_category_qas:
                                        total_available = len(selected_doc_category_qas[cat])
                                        used = category_actual.get(cat, 0)
                                        category_available[cat] = total_available - used
                                
                                # 循环分配剩余配额
                                while remaining_quota > 0:
                                    allocated_this_round = 0
                                    for cat in cats:
                                        if remaining_quota <= 0:
                                            break
                                        if category_available.get(cat, 0) > 0:
                                            # 从该类别再抽样一个
                                            cat_qas = selected_doc_category_qas[cat].copy()
                                            random.shuffle(cat_qas)
                                            # 找到还没有被抽样的 QA
                                            for paper_id, i, qa_item in cat_qas:
                                                if (paper_id, i) not in sampled_qas_indices:
                                                    sampled_qas_indices.add((paper_id, i))
                                                    category_actual[cat] += 1
                                                    category_available[cat] -= 1
                                                    remaining_quota -= 1
                                                    allocated_this_round += 1
                                                    break
                                    
                                    # 如果这一轮没有分配任何配额，说明没有更多可用 QA 了
                                    if allocated_this_round == 0:
                                        print(f"Warning: No more QAs available to sample. Stopping with {remaining_quota} unallocated.")
                                        break
                            
                            print("Actual category counts after reallocation:")
                            for cat in cats:
                                print(f"  {cat}: {category_actual.get(cat, 0)} QAs")
                            
                    # 过滤每个文档的 QA，只保留抽样的
                    for paper_id in selected_ids:
                        paper_data, source_file = paper_data_map[paper_id]
                        new_qas = []
                        
                        for i, qa_item in enumerate(paper_data.get("qas", [])):
                            # 检查是否所有 answer 都是 unanswerable
                            is_unanswerable = all(
                                ans.get("answer", {}).get("unanswerable", False)
                                for ans in qa_item.get("answers", [])
                            )
                            # 保留 unanswerable 的 QA 或抽样的可回答 QA
                            if is_unanswerable or (paper_id, i) in sampled_qas_indices:
                                new_qas.append(qa_item)
                        
                        paper_data["qas"] = new_qas
                
                if sample_mode == "random":
                    # 在选中文档中随机抽样 QA
                    random.seed(seed)
                    
                    # 随机抽样
                    if len(qa_with_indices) > sample_size:
                        sampled_qas = random.sample(qa_with_indices, sample_size)
                    else:
                        sampled_qas = qa_with_indices
                    
                    # 创建一个集合来标记哪些 QA 需要保留
                    keep_qas_indices = set()
                    for paper_id, cat, i, qa_item in sampled_qas:
                        keep_qas_indices.add((paper_id, i))
                    
                    # 过滤每个文档的 QA
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
            if sample_mode == "stratified":
                # 分层抽样
                print(f"Using stratified sampling (seed={seed})")
                random.seed(seed)
                
                num_categories = len(categories)
                base_per_category = sample_size // num_categories
                remainder = sample_size % num_categories
                
                if base_per_category == 0:
                    print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                    print("Falling back to random sampling")
                    sample_mode = "random"
                else:
                    category_targets = {}
                    for i, cat in enumerate(categories):
                        category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                    
                    if remainder > 0:
                        print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                        print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                    
                    print("Category targets:")
                    for cat in categories:
                        print(f"  {cat}: {category_targets[cat]} QAs")
                    
                    selected_ids = []
                    selected_qas_by_cat = {cat: 0 for cat in categories}
                    doc_used = {paper_id: False for paper_id in all_paper_ids}
                    
                    for cat in categories:
                        target = category_targets[cat]
                        if target == 0:
                            continue
                        
                        # 获取该类别的所有 QA 并打乱
                        cat_qas = category_qas[cat].copy()
                        random.shuffle(cat_qas)
                        
                        for paper_id, qa_item in cat_qas:
                            if doc_used[paper_id]:
                                continue
                            
                            # 检查添加这个文档是否会超过目标
                            paper_cat_qas = paper_category_qas[paper_id]
                            new_count = selected_qas_by_cat[cat] + len(paper_cat_qas.get(cat, []))
                            if new_count > target:
                                continue
                            
                            selected_ids.append(paper_id)
                            doc_used[paper_id] = True
                            
                            # 更新各类别的计数
                            for c, qs in paper_cat_qas.items():
                                selected_qas_by_cat[c] += len(qs)
                            
                            if selected_qas_by_cat[cat] >= target:
                                break
                    
                    # 检查是否达到目标
                    total_selected = sum(selected_qas_by_cat.values())
                    print(f"Sampled {len(selected_ids)} documents with {total_selected} QAs")
                    for cat in categories:
                        print(f"  {cat}: {selected_qas_by_cat[cat]} QAs (target: {category_targets[cat]})")
            
            if sample_mode == "random":
                # 按 QA 数抽样，但保持文档完整性
                print(f"Using random sampling (seed={seed})")
                random.seed(seed)
                shuffled_ids = all_paper_ids.copy()
                random.shuffle(shuffled_ids)
                
                # 计算每个文档的有效 QA 数
                paper_qas_count = {}
                for paper_id in all_paper_ids:
                    count = 0
                    for cat_qas in paper_category_qas[paper_id].values():
                        count += len(cat_qas)
                    paper_qas_count[paper_id] = count
                
                selected_ids = []
                selected_qas_count = 0
                
                for paper_id in shuffled_ids:
                    paper_qas = paper_qas_count[paper_id]
                    
                    if selected_qas_count + paper_qas <= sample_size or not selected_ids:
                        selected_ids.append(paper_id)
                        selected_qas_count += paper_qas
                    else:
                        # 如果已经达到样本量，停止
                        if selected_qas_count >= sample_size:
                            break
                
                print(f"Sampled {len(selected_ids)} documents with {selected_qas_count} QAs (seed={seed})")
    
    # Group by source file
    output_dir.mkdir(parents=True, exist_ok=True)
    data_by_file = {}
    for paper_id in selected_ids:
        paper_data, source_file = paper_data_map[paper_id]
        if source_file not in data_by_file:
            data_by_file[source_file] = {}
        data_by_file[source_file][paper_id] = paper_data
    
    # Save each file
    for json_file, output_data in data_by_file.items():
        output_file = output_dir / json_file
        save_json_data(output_data, output_file)
        print(f"Saved {len(output_data)} papers to {json_file}")
    
    # 计算抽样后的 QA 数
    sampled_qas = 0
    for paper_id in selected_ids:
        paper_data, source_file = paper_data_map[paper_id]
        if "qas" in paper_data:
            for qa_item in paper_data["qas"]:
                # 检查是否所有 answer 都是 unanswerable
                is_unanswerable = all(
                    ans.get("answer", {}).get("unanswerable", False)
                    for ans in qa_item.get("answers", [])
                )
                if not is_unanswerable:
                    sampled_qas += 1
    
    # Save metadata
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
    input_file = input_dir / "data" / "financebench_open_source.jsonl"
    if not input_file.exists():
        raise FileNotFoundError(f"financebench_open_source.jsonl not found at {input_file}")
    
    data = load_jsonl_data(input_file)
    
    # 按文档分组
    from collections import defaultdict
    doc_groups = defaultdict(list)
    for item in data:
        doc_name = item.get("doc_name", "unknown")
        doc_groups[doc_name].append(item)
    
    # 按 question_type 分组 QA
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
        # 全量模式
        selected_docs = all_doc_names
        print("Using full Financebench dataset")
    else:
        # 先抽样文档
        if num_docs is not None:
            # 按文档数抽样
            if num_docs >= original_num_docs:
                selected_docs = all_doc_names
                print("Using all documents")
            else:
                random.seed(seed)
                selected_docs = random.sample(all_doc_names, num_docs)
                print(f"Sampled {len(selected_docs)} documents (seed={seed})")
            
            # 如果同时指定了 sample_size，则在选中的文档中再抽样 QA
            if sample_size is not None:
                print(f"Further sampling {sample_size} QAs from selected documents (mode: {sample_mode})")
                
                # 在选中的文档中，按类别重新分组 QA
                selected_doc_category_qas = {}
                for doc_name in selected_docs:
                    doc_cat_qas = doc_category_qas[doc_name]
                    for cat, items in doc_cat_qas.items():
                        if cat not in selected_doc_category_qas:
                            selected_doc_category_qas[cat] = []
                        for item in items:
                            selected_doc_category_qas[cat].append((doc_name, item))
                
                if sample_mode == "stratified":
                    # 在选中文档中分层抽样 QA
                    num_categories = len(selected_doc_category_qas)
                    if num_categories > 0:
                        base_per_category = sample_size // num_categories
                        remainder = sample_size % num_categories
                        
                        if base_per_category == 0:
                            print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                            print("Falling back to random sampling")
                            sample_mode = "random"
                        else:
                            category_targets = {}
                            cats = sorted(selected_doc_category_qas.keys())
                            for i, cat in enumerate(cats):
                                category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                            
                            if remainder > 0:
                                print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                                print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                            
                            print("Category targets in selected docs:")
                            for cat in cats:
                                print(f"  {cat}: {category_targets[cat]} QAs")
                            
                            # 抽样 QA
                            random.seed(seed)
                            sampled_items = []
                            
                            for doc_name in selected_docs:
                                doc_items = doc_groups[doc_name]
                                doc_cat_qas = doc_category_qas[doc_name]
                                
                                for cat, items in doc_cat_qas.items():
                                    if cat in category_targets and category_targets[cat] > 0:
                                        # 从该类别的 QA 中抽样
                                        random.shuffle(items)
                                        sample_count = min(len(items), category_targets[cat])
                                        sampled_items.extend(items[:sample_count])
                                        category_targets[cat] -= sample_count
                            
                            # 更新 doc_groups，只保留抽样的 items
                            new_doc_groups = defaultdict(list)
                            for item in sampled_items:
                                doc_name = item.get("doc_name", "unknown")
                                new_doc_groups[doc_name].append(item)
                            doc_groups = new_doc_groups
                
                if sample_mode == "random":
                    # 在选中文档中随机抽样 QA
                    random.seed(seed)
                    # 收集所有 QA
                    all_items = []
                    for doc_name in selected_docs:
                        items = doc_groups[doc_name]
                        all_items.extend(items)
                    
                    # 随机抽样
                    if len(all_items) > sample_size:
                        sampled_items = random.sample(all_items, sample_size)
                    else:
                        sampled_items = all_items
                    
                    # 更新 doc_groups，只保留抽样的 items
                    new_doc_groups = defaultdict(list)
                    for item in sampled_items:
                        doc_name = item.get("doc_name", "unknown")
                        new_doc_groups[doc_name].append(item)
                    doc_groups = new_doc_groups
        else:
            if sample_mode == "stratified":
                # 分层抽样
                print(f"Using stratified sampling (seed={seed})")
                random.seed(seed)
                
                num_categories = len(categories)
                base_per_category = sample_size // num_categories
                remainder = sample_size % num_categories
                
                if base_per_category == 0:
                    print(f"Warning: Sample size {sample_size} is too small for {num_categories} categories")
                    print("Falling back to random sampling")
                    sample_mode = "random"
                else:
                    category_targets = {}
                    for i, cat in enumerate(categories):
                        category_targets[cat] = base_per_category + (1 if i < remainder else 0)
                    
                    if remainder > 0:
                        print(f"Cannot split {sample_size} QAs evenly into {num_categories} categories")
                        print(f"Distributing {remainder} extra QA(s) to first {remainder} category(ies)")
                    
                    print("Category targets:")
                    for cat in categories:
                        print(f"  {cat}: {category_targets[cat]} QAs")
                    
                    selected_docs = []
                    selected_qas_by_cat = {cat: 0 for cat in categories}
                    doc_used = {doc_name: False for doc_name in all_doc_names}
                    
                    for cat in categories:
                        target = category_targets[cat]
                        if target == 0:
                            continue
                        
                        # 获取该类别的所有 QA 并打乱
                        cat_qas = category_qas[cat].copy()
                        random.shuffle(cat_qas)
                        
                        for doc_name, item in cat_qas:
                            if doc_used[doc_name]:
                                continue
                            
                            # 检查添加这个文档是否会超过目标
                            doc_cat_qas = doc_category_qas[doc_name]
                            new_count = selected_qas_by_cat[cat] + len(doc_cat_qas.get(cat, []))
                            if new_count > target:
                                continue
                            
                            selected_docs.append(doc_name)
                            doc_used[doc_name] = True
                            
                            # 更新各类别的计数
                            for c, qs in doc_cat_qas.items():
                                selected_qas_by_cat[c] += len(qs)
                            
                            if selected_qas_by_cat[cat] >= target:
                                break
                    
                    # 检查是否达到目标
                    total_selected = sum(selected_qas_by_cat.values())
                    print(f"Sampled {len(selected_docs)} documents with {total_selected} QAs")
                    for cat in categories:
                        print(f"  {cat}: {selected_qas_by_cat[cat]} QAs (target: {category_targets[cat]})")
            
            if sample_mode == "random":
                # 按 QA 数抽样，但保持文档完整性
                print(f"Using random sampling (seed={seed})")
                random.seed(seed)
                shuffled_docs = all_doc_names.copy()
                random.shuffle(shuffled_docs)
                
                selected_docs = []
                selected_qas_count = 0
                
                for doc_name in shuffled_docs:
                    doc_qas = len(doc_groups[doc_name])
                    
                    if selected_qas_count + doc_qas <= sample_size or not selected_docs:
                        selected_docs.append(doc_name)
                        selected_qas_count += doc_qas
                    else:
                        # 如果已经达到样本量，停止
                        if selected_qas_count >= sample_size:
                            break
                
                print(f"Sampled {len(selected_docs)} documents with {selected_qas_count} QAs (seed={seed})")
    
    # 构建选中的数据
    selected_data = []
    for doc_name in selected_docs:
        selected_data.extend(doc_groups[doc_name])
    
    # Save data
    output_file = output_dir / "financebench_open_source.jsonl"
    save_jsonl_data(selected_data, output_file)
    
    # Copy document info JSONL
    doc_info_src = input_dir / "data" / "financebench_document_information.jsonl"
    if doc_info_src.exists():
        doc_info_dst = output_dir / "financebench_document_information.jsonl"
        shutil.copy2(doc_info_src, doc_info_dst)
    
    # Copy PDFs used by selected samples
    pdfs_src = input_dir / "pdfs"
    pdfs_dst = output_dir / "pdfs"
    
    if pdfs_src.exists():
        pdfs_dst.mkdir(parents=True, exist_ok=True)
        
        # Copy required PDFs
        for doc_name in selected_docs:
            src_pdf = pdfs_src / f"{doc_name}.pdf"
            if src_pdf.exists():
                shutil.copy2(src_pdf, pdfs_dst / f"{doc_name}.pdf")
                print(f"Copied PDF: {doc_name}.pdf")
    
    # Save metadata
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
        
        # Save metadata
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
        choices=["stratified"],
        default="stratified",
        help="Sampling mode (default: stratified)"
    )
    
    args = parser.parse_args()
    
    # Handle --full flag - use full dataset, no sampling
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
