# src/adapters/locomo_adapter.py
import json
import os
from typing import List, Dict, Any

from .base import BaseAdapter, StandardDoc, StandardSample, StandardQA


MISSING_RULE = "If no information is available to answer the question, write 'Not mentioned'."


CATEGORY_INSTRUCTIONS = {
    "1": """Extract the exact factual answer from the conversation.
- Use the exact words from the context when possible
- If multiple items, separate with commas""",
    
    "2": """Answer the time-related question.
- Pay close attention to DATE labels in the conversation
- Calculate relative time (e.g., "10 years ago") when needed
- Use the exact dates from the context""",
    
    "3": """Reason and infer based on the conversation.
- Use ONLY the facts in the context
- State your conclusion clearly (e.g., "Likely yes", "Probably no")
- Do NOT explain your reasoning or provide any basis/justification
- Only output your final conclusion, nothing else
- Do NOT invent information""",
    
    "4": """Understand the meaning and significance.
- Focus on what the speakers mean, not just what they say
- Identify symbolism or implied meaning
- Use wording from the context when possible""",
}


class LocomoAdapter(BaseAdapter):
    """
    Adapter specifically for processing the LocoMo dataset.
    Converts session-format JSON to Markdown with time information.
    """
    def data_prepare(self,doc_dir:str) -> List[StandardDoc]:
        """
        Load raw data and convert to OpenViking-friendly format
        """
        if not os.path.exists(self.raw_file_path):
            raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")

        res:List[StandardDoc] = []

        with open(self.raw_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            dataset = [data] if isinstance(data, dict) else data
        os.makedirs(doc_dir, exist_ok=True)
        for item in dataset:
            sample_id = item.get("sample_id", "unknown")
            doc_content = self._convert_conversation_to_markdown(sample_id, item.get("conversation", {}))

            try:
                doc_path = os.path.join(doc_dir, f"{sample_id}_doc.md")
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write(doc_content)
                res.append(StandardDoc(sample_id, doc_path))
            except Exception as e:
                self.logger.error(f"[locomo adapter] doc:{sample_id} prepare error {e}")
                raise e
        return res

    def load_and_transform(self) -> List[StandardSample]:
        """
        Load raw JSON data and convert to standardized StandardSample object list.
        """
        if not os.path.exists(self.raw_file_path):
            raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")

        with open(self.raw_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            dataset = [data] if isinstance(data, dict) else data

        standard_samples = []

        for item in dataset:
            sample_id = item.get("sample_id", "unknown")

            qa_pairs = []
            for q in item.get("qa", []):
                if str(q.get("category")) == "5":
                    continue
                raw_ans = q.get("answer")

                if isinstance(raw_ans, list):
                    golds = raw_ans
                elif raw_ans is None or raw_ans == "":
                    golds = ["Not mentioned"]
                else:
                    golds = [raw_ans]

                qa_pairs.append(StandardQA(
                    question=q["question"],
                    gold_answers=[str(g) for g in golds],
                    evidence=q.get("evidence", []),
                    category=q.get("category"),
                    metadata={"original_id": q.get("id")}
                ))

            standard_samples.append(StandardSample(
                sample_id=sample_id,
                qa_pairs=qa_pairs
            ))

        return standard_samples

    def _convert_conversation_to_markdown(self, sample_id: str, conv: Dict[str, Any]) -> str:
        """
        Convert LocoMo session structure to flat Markdown string.
        """
        md_lines = [f"# Chat History: {sample_id}"]

        session_idx = 1
        while f"session_{session_idx}" in conv:
            s_key = f"session_{session_idx}"
            dt_key = f"session_{session_idx}_date_time"
            sum_key = f"session_{session_idx}_summary"

            md_lines.append(f"\n## Session {session_idx}")

            session_dt = conv.get(dt_key)
            if session_dt:
                md_lines.append(f"DATE: {session_dt}")

            session_sum = conv.get(sum_key)
            if session_sum:
                md_lines.append(f"SUMMARY: {session_sum}")

            for turn in conv[s_key]:
                spk = turn.get("speaker", "Unknown")
                txt = turn.get("text", "")

                raw_id = turn.get("dia_id") or turn.get("id")
                
                image_suffix = ""
                img_url = turn.get("img_url", [])
                blip_caption = turn.get("blip_caption", "")
                
                if img_url and blip_caption:
                    if len(img_url) == 1:
                        image_suffix = f"[Attached image：{blip_caption}]"
                    else:
                        for i, caption in enumerate([blip_caption] * len(img_url)):
                            image_suffix += f"[Attached image {i+1}：{caption}]"
                
                dia_suffix = f" [{raw_id}]" if raw_id else ""
                
                md_lines.append(f"**{spk}**: {txt}{image_suffix}{dia_suffix}")

            session_idx += 1

        return "\n".join(md_lines)

    def build_prompt(self, qa: StandardQA, context_blocks: List[str]) -> tuple[str, Dict[str, Any]]:
        category = str(qa.category)
        context_text = "\n\n".join(context_blocks)
        
        category_instruction = CATEGORY_INSTRUCTIONS.get(category, "")
        
        if category_instruction:
            full_prompt = f"""{context_text}

{MISSING_RULE}

---
{category_instruction}

Question: {qa.question}

Answer:"""
        else:
            full_prompt = f"""{context_text}

{MISSING_RULE}

Based on the conversation above, answer the following question.
Use ONLY the provided context. Do NOT invent any information.

Question: {qa.question}

Answer:"""

        meta = {"category": category}
        return full_prompt, meta

    def post_process_answer(self, qa: StandardQA, raw_answer: str, meta: Dict[str, Any]) -> str:
        return raw_answer.strip()
