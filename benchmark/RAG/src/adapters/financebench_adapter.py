# src/adapters/finance_bench_adapter.py
"""
FinanceBench Dataset Adapter

FinanceBench is a financial domain QA dataset with SEC financial report PDFs as documents.
Data format: JSONL, each line contains question, answer, doc_name, evidence, etc.
evidence_text in evidence is used for recall calculation.
"""

import json
import os
from collections import defaultdict
from typing import List, Dict, Any
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent))

from base import BaseAdapter, StandardDoc, StandardSample, StandardQA

CATEGORY_INSTRUCTIONS = {
    "domain-relevant": """Answer the financial question based on the document.
- Use ONLY facts from the context
- If numerical, include units (e.g., USD millions, %)
- Provide concise, direct answer
- Do NOT invent information""",
    
    "metrics-generated": """Calculate the financial metric based on the document.
- Use ONLY numbers from the context
- Show your calculations clearly
- Round to appropriate decimal places
- Include units (e.g., USD millions, %)
- Do NOT invent numbers""",
    
    "novel-generated": """Answer the financial question based on the document.
- Use ONLY facts from the context
- If numerical, include units (e.g., USD millions, %)
- Provide clear, complete answer
- Do NOT invent information"""
}

MISSING_RULE = "If the provided context does not contain sufficient information to answer the question, respond with 'Insufficient information'."


class FinanceBenchAdapter(BaseAdapter):
    """
    FinanceBench Dataset Adapter.
    Processes financial domain QA data with SEC financial report PDFs as documents.
    """

    def __init__(self, raw_file_path: str):
        super().__init__(raw_file_path)
        data_dir = os.path.dirname(self.raw_file_path)
        # Check if pdfs is in data directory or parent directory
        self.pdf_dir = os.path.join(data_dir, "pdfs")
        if not os.path.exists(self.pdf_dir):
            # Try parent directory (new structure: raw_data/dataset_name/data/ and raw_data/dataset_name/pdfs/)
            parent_dir = os.path.dirname(data_dir)
            self.pdf_dir = os.path.join(parent_dir, "pdfs")

    def data_prepare(self, doc_dir: str) -> List[StandardDoc]:
        """
        Prepare document list for ingestion. Only ingest documents referenced in JSONL.
        """
        if not os.path.exists(self.pdf_dir):
            raise FileNotFoundError(f"PDF directory not found: {self.pdf_dir}")

        doc_names = set()
        with open(self.raw_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    doc_names.add(json.loads(line)["doc_name"])

        docs: List[StandardDoc] = []
        for doc_name in sorted(doc_names):
            pdf_path = os.path.join(self.pdf_dir, f"{doc_name}.pdf")
            if not os.path.exists(pdf_path):
                self.logger.warning(f"PDF not found: {pdf_path}, skipping")
                continue
            docs.append(StandardDoc(sample_id=doc_name, doc_path=pdf_path))

        self.logger.info(f"[FinanceBench] Prepared {len(docs)} documents for ingestion (referenced only)")
        return docs

    def load_and_transform(self) -> List[StandardSample]:
        """
        Parse JSONL question file, group by doc_name into StandardSample.
        evidence uses evidence_text field from each evidence.
        """
        if not os.path.exists(self.raw_file_path):
            raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")

        groups: Dict[str, List[Dict]] = defaultdict(list)
        with open(self.raw_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                groups[item["doc_name"]].append(item)

        samples: List[StandardSample] = []
        for doc_name, items in groups.items():
            qa_pairs = []
            for item in items:
                evidence_texts = [
                    ev["evidence_text"]
                    for ev in item.get("evidence", [])
                    if ev.get("evidence_text")
                ]

                qa_pairs.append(StandardQA(
                    question=item["question"],
                    gold_answers=[item["answer"]],
                    evidence=evidence_texts,
                    category=item.get("question_type"),
                    metadata={
                        "financebench_id": item.get("financebench_id"),
                        "question_reasoning": item.get("question_reasoning"),
                        "justification": item.get("justification", ""),
                        "company": item.get("company"),
                    }
                ))

            samples.append(StandardSample(
                sample_id=doc_name,
                qa_pairs=qa_pairs,
            ))

        self.logger.info(
            f"[FinanceBench] Loaded {sum(len(s.qa_pairs) for s in samples)} questions "
            f"across {len(samples)} documents"
        )
        return samples

    def build_prompt(self, qa: StandardQA, context_blocks: List[str]) -> tuple[str, Dict[str, Any]]:
        context_text = "\n\n".join(context_blocks)
        
        category = qa.category
        category_instruction = CATEGORY_INSTRUCTIONS.get(category, "")
        
        if category_instruction:
            full_prompt = f"""{context_text}

{category_instruction}

{MISSING_RULE}

Question: {qa.question}

Answer:"""
        else:
            full_prompt = f"""{context_text}

{MISSING_RULE}

Question: {qa.question}

Answer:"""
        
        meta = {
            "question_type": qa.category,
            "financebench_id": qa.metadata.get("financebench_id"),
        }
        return full_prompt, meta
