from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Union, Optional
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from core.logger import get_logger


@dataclass
class StandardQA:
    """Standardized single question-answer pair"""
    question: str
    gold_answers: List[str]
    evidence: List[str] = field(default_factory=list)
    category: Optional[Union[int, str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StandardSample:
    """Standardized sample containing document content and corresponding QA list"""
    sample_id: str
    qa_pairs: List[StandardQA]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class StandardDoc:
    """Standardized sampleid to doc_path mapping structure"""
    sample_id:str
    doc_path:str


class BaseAdapter(ABC):
    """Base class for all dataset adapters"""
    
    def __init__(self, raw_file_path: str):
        self.raw_file_path = raw_file_path
        self.logger = get_logger()

    @abstractmethod
    def data_prepare(self, doc_dir:str) -> List[StandardDoc]:
        """
        Data preparation.
        1. Convert dataset format to OpenViking-friendly format
        2. Return converted (or unconverted) file paths
        
        Returns:
            List[StandardDoc]: Array of file paths expected to be input to OpenViking
        """
        pass

    @abstractmethod
    def load_and_transform(self) -> List[StandardSample]:
        """
        Read raw files and convert to standard format list.
        Must be implemented by subclasses.
        """
        pass
    
    @abstractmethod
    def build_prompt(self, qa: StandardQA, context_blocks: List[str]) -> tuple[str, Dict[str, Any]]:
        """
        Build final prompt to send to LLM based on retrieved context and QA pair.
        
        Returns:
            - full_prompt (str): Complete prompt string
            - meta (Dict): Metadata to pass to post-processing function (e.g., option mapping for multiple choice)
        """
        pass

    def post_process_answer(self, qa: StandardQA, raw_answer: str, meta: Dict[str, Any]) -> str:
        """
        Post-process raw LLM output (default implementation only strips whitespace).
        """
        return raw_answer.strip()
