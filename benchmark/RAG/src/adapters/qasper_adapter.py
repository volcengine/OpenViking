# src/adapters/qasper_adapter.py
"""
Qasper Dataset Adapter

Qasper is an academic paper QA dataset containing 1585 NLP papers and 5049 questions.
Each question is answered by multiple annotators, with answer types including:
- extractive_spans: text spans extracted from the paper
- free_form_answer: free-form answers
- yes_no: yes/no answers
- unanswerable: no answer can be found in the paper

Dataset characteristics:
1. Each paper includes title, abstract, section content, and figure/table information
2. Each question may have answers from multiple annotators
3. Each answer has corresponding evidence (evidence text)

Adapter functions:
- data_prepare: Convert papers to Markdown format, preserving section structure
- load_and_transform: Parse QA data, preserving answer-evidence correspondence
- build_prompt: Build QA prompt
- post_process_answer: Post-process LLM output
"""

import json
import os
from typing import List, Dict, Any

from .base import BaseAdapter, StandardDoc, StandardSample, StandardQA

# Specific instructions for different answer types
CATEGORY_INSTRUCTIONS = {
    "extractive": """Extract the exact answer from the paper.
- Use EXACT wording from the context
- Do NOT rephrase or add explanation
- Provide concise, direct answer""",
    
    "free_form": """Answer using information from the paper.
- Use ONLY facts from the context
- You may rephrase or summarize in your own words
- Provide clear, complete answer
- Do NOT invent information""",
    
    "yes_no": """Answer Yes/No question based on the paper.
- First respond "Yes" or "No"
- Do NOT add explanation
- Use ONLY info from context
- Do NOT invent information"""
}

# Rule for when answer cannot be found
MISSING_RULE = "If no information is available to answer the question, write 'Not mentioned'."


class QasperAdapter(BaseAdapter):
    """
    Adapter specifically for processing Qasper dataset.
    
    Converts academic papers to Markdown documents with section structure,
    and converts QA data to standardized StandardSample format.
    
    Attributes:
        raw_file_path: Raw JSON data file path
        logger: Logger
    """
    
    def data_prepare(self, doc_dir: str) -> List[StandardDoc]:
        """
        Load raw data and convert to OpenViking-friendly format.
        
        Converts each paper to Markdown document, preserving:
        - Title (# Title)
        - Abstract (## Abstract)
        - Sections (## Section Name)
        - Figures and Tables (## Figures and Tables)
        
        Args:
            doc_dir: Document output directory path
            
        Returns:
            List[StandardDoc]: List of standardized document objects, each containing paper_id and document path
            
        Raises:
            FileNotFoundError: Raw data file not found
        """
        res: List[StandardDoc] = []
        data = {}

        if os.path.isdir(self.raw_file_path):
            json_files = [f for f in os.listdir(self.raw_file_path) if f.endswith('.json') and f != 'sampling_metadata.json']
            for json_file in json_files:
                file_path = os.path.join(self.raw_file_path, json_file)
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)
                    data.update(file_data)
        else:
            if not os.path.exists(self.raw_file_path):
                raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")
            with open(self.raw_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

        os.makedirs(doc_dir, exist_ok=True)
        
        for paper_id, paper_data in data.items():
            doc_content = self._convert_paper_to_markdown(paper_id, paper_data)

            try:
                doc_path = os.path.join(doc_dir, f"{paper_id}_doc.md")
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write(doc_content)
                res.append(StandardDoc(paper_id, doc_path))
            except Exception as e:
                self.logger.error(f"[qasper adapter] doc:{paper_id} prepare error {e}")
                raise e
        return res

    def load_and_transform(self) -> List[StandardSample]:
        """
        Load raw JSON data and convert to standardized StandardSample object list.
        
        Processing logic:
        1. Iterate through QA list of each paper
        2. For each question, collect answers from all annotators
        3. Preserve answer-evidence correspondence (stored in metadata)
        4. Format question as "Based on the paper \"{title}\", {question}"
        
        Answer type handling:
        - extractive_spans: directly use extracted text spans
        - free_form_answer: use free-form answer
        - yes_no: convert to "Yes" or "No"
        - unanswerable: convert to "Not mentioned"
        
        Returns:
            List[StandardSample]: List of standardized sample objects
            
        Raises:
            FileNotFoundError: Raw data file not found
        """
        data = {}

        if os.path.isdir(self.raw_file_path):
            json_files = [f for f in os.listdir(self.raw_file_path) if f.endswith('.json') and f != 'sampling_metadata.json']
            for json_file in json_files:
                file_path = os.path.join(self.raw_file_path, json_file)
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)
                    data.update(file_data)
        else:
            if not os.path.exists(self.raw_file_path):
                raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")
            with open(self.raw_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

        standard_samples = []

        for paper_id, paper_data in data.items():
            qa_pairs = []
            paper_title = paper_data.get("title", "Unknown Title")
            
            for qa_item in paper_data.get("qas", []):

                # --- Unanswerable filtering logic ---
                # Check if all answers are marked as unanswerable
                is_unanswerable = all(
                    ans.get("answer", {}).get("unanswerable", False) 
                    for ans in qa_item.get("answers", [])
                )
                if is_unanswerable:
                    continue
                # ------------------
                
                raw_question = qa_item.get("question", "")
                question_id = qa_item.get("question_id", "")
                # Append paper title to question for easier retrieval
                question = f'Based on the paper "{paper_title}", {raw_question}'
                
                gold_answers = []
                evidence_list = []
                answer_types = []
                answer_evidence_pairs = []
                
                # Iterate through all annotator answers
                for answer_wrapper in qa_item.get("answers", []):
                    answer_obj = answer_wrapper.get("answer", {})
                    
                    current_answer = None
                    answer_type = self._get_answer_type(answer_obj)
                    
                    # Process different answer types
                    if answer_obj.get("unanswerable", False):
                        current_answer = "Not mentioned"
                        gold_answers.append(current_answer)
                    else:
                        extractive_spans = answer_obj.get("extractive_spans", [])
                        free_form_answer = answer_obj.get("free_form_answer", "")
                        yes_no = answer_obj.get("yes_no")
                        
                        if extractive_spans:
                            valid_spans = [span.strip() for span in extractive_spans if span and span.strip()]
                            if valid_spans:
                                combined_answer = "; ".join(valid_spans)
                                gold_answers.append(combined_answer)
                                current_answer = combined_answer
                            else:
                                current_answer = None
                        elif free_form_answer and free_form_answer.strip():
                            current_answer = free_form_answer.strip()
                            gold_answers.append(current_answer)
                        elif yes_no is not None:
                            current_answer = "Yes" if yes_no else "No"
                            gold_answers.append(current_answer)
                    
                    # Collect evidence text
                    current_evidence = []
                    evidence = answer_obj.get("evidence", [])
                    for ev in evidence:
                        if ev and ev.strip():
                            current_evidence.append(ev)
                            if ev not in evidence_list:
                                evidence_list.append(ev)
                    
                    # Record answer type (deduplicated)
                    if answer_type not in answer_types:
                        answer_types.append(answer_type)
                    
                    # Save answer-evidence correspondence
                    if current_answer:
                        answer_evidence_pairs.append({
                            "answer": current_answer,
                            "evidence": current_evidence,
                            "answer_type": answer_type
                        })
                
                # If no answers, default to "Not mentioned"
                if not gold_answers:
                    gold_answers = ["Not mentioned"]
                
                # Deduplicate (preserve order)
                gold_answers = list(dict.fromkeys(gold_answers))
                
                qa_pairs.append(StandardQA(
                    question=question,
                    gold_answers=gold_answers,
                    evidence=evidence_list,
                    category=None,
                    metadata={
                        "question_id": question_id,
                        "answer_types": answer_types,
                        "answer_evidence_pairs": answer_evidence_pairs
                    }
                ))

            standard_samples.append(StandardSample(
                sample_id=paper_id,
                qa_pairs=qa_pairs
            ))

        return standard_samples
    
    def _get_answer_type(self, answer_obj: Dict[str, Any]) -> str:
        """
        Determine answer type from answer object.
        
        Args:
            answer_obj: Answer object, containing extractive_spans, free_form_answer, yes_no, etc.
            
        Returns:
            str: Answer type, possible values:
                - "unanswerable": cannot answer
                - "extractive": extractive answer
                - "free_form": free-form answer
                - "yes_no": yes/no answer
                - "unknown": unknown type
        """
        if answer_obj.get("unanswerable", False):
            return "unanswerable"
        if answer_obj.get("extractive_spans"):
            return "extractive"
        if answer_obj.get("free_form_answer", "").strip():
            return "free_form"
        if answer_obj.get("yes_no") is not None:
            return "yes_no"
        return "unknown"

    def _convert_paper_to_markdown(self, paper_id: str, paper_data: Dict[str, Any]) -> str:
        """
        Convert Qasper paper structure to Markdown string.
        
        Conversion format:
        ```markdown
        # {title}
        Paper ID: {paper_id}
        
        ## Abstract
        {abstract}
        
        ## Section Name 1
        {paragraph 1}
        {paragraph 2}
        
        ## Section Name 2
        ...
        
        ## Figures and Tables
        ### Figure 1
        Caption: {caption}
        File: {filename}
        
        ### Table 1
        Caption: {caption}
        File: {filename}
        ```
        
        Args:
            paper_id: Paper ID
            paper_data: Paper data, containing title, abstract, full_text, figures_and_tables
            
        Returns:
            str: Markdown formatted paper content
        """
        md_lines = []
        
        # Title
        title = paper_data.get("title", "Unknown Title")
        md_lines.append(f"# {title}")
        md_lines.append(f"Paper ID: {paper_id}\n")
        
        # Abstract
        abstract = paper_data.get("abstract", "")
        if abstract:
            md_lines.append("## Abstract")
            md_lines.append(abstract)
            md_lines.append("")
        
        # Main text sections
        full_text = paper_data.get("full_text", [])
        for section in full_text:
            section_name = section.get("section_name", "")
            paragraphs = section.get("paragraphs", [])
            
            if section_name:
                md_lines.append(f"## {section_name}")
            
            for para in paragraphs:
                if para and para.strip():
                    md_lines.append(para.strip())
                    md_lines.append("")
        
        # Figure and table information
        figures_and_tables = paper_data.get("figures_and_tables", [])
        if figures_and_tables:
            md_lines.append("## Figures and Tables")
            for idx, fig in enumerate(figures_and_tables, 1):
                caption = fig.get("caption", "")
                file_name = fig.get("file", "")
                
                # Determine if figure or table based on filename or caption
                if "Figure" in file_name or "figure" in caption.lower():
                    md_lines.append(f"### Figure {idx}")
                else:
                    md_lines.append(f"### Table {idx}")
                
                if caption:
                    md_lines.append(f"Caption: {caption}")
                if file_name:
                    md_lines.append(f"File: {file_name}")
                md_lines.append("")

        return "\n".join(md_lines)

    def build_prompt(self, qa: StandardQA, context_blocks: List[str]) -> tuple[str, Dict[str, Any]]:
        context_text = "\n\n".join(context_blocks) if context_blocks else "No relevant context found."
        
        answer_types = qa.metadata.get("answer_types", [])
        primary_type = answer_types[0] if answer_types else None
        
        category_instruction = CATEGORY_INSTRUCTIONS.get(primary_type, "")
        
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
            "question_id": qa.metadata.get("question_id", ""),
            "answer_types": answer_types
        }
        return full_prompt, meta

    def post_process_answer(self, qa: StandardQA, raw_answer: str, meta: Dict[str, Any]) -> str:
        """
        Post-process raw answer generated by LLM.
        
        Current implementation only strips leading/trailing whitespace.
        
        Args:
            qa: Standardized QA object
            raw_answer: Raw answer generated by LLM
            meta: Metadata dictionary
            
        Returns:
            str: Processed answer
        """
        return raw_answer.strip()
