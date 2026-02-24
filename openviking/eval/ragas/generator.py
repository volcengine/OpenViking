# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Dataset generator for OpenViking evaluation.
"""

import uuid
from typing import Any, Optional

from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.logger import get_logger

from .types import EvalDataset, EvalSample

logger = get_logger(__name__)


class DatasetGenerator:
    """
    Generates evaluation datasets from OpenViking resources.
    """

    def __init__(self, llm: Optional[Any] = None):
        """
        Initialize generator.

        Args:
            llm: LLM instance to use for question/answer generation.
                 Should be an OpenViking VLMProcessor or similar.
        """
        self.llm = llm

    async def generate_from_viking_path(
        self,
        path: str,
        count: int = 5,
        scope: str = "resources",
        recursive: bool = True,
    ) -> EvalDataset:
        """
        Generate evaluation samples from a VikingFS directory.

        Args:
            path: Path in VikingFS (e.g., "docs/ai")
            count: Number of samples to generate
            scope: VikingFS scope
            recursive: Whether to search recursively

        Returns:
            EvalDataset
        """
        get_viking_fs()
        uri_base = f"viking://{scope}/{path.lstrip('/')}"

        # Collect files
        # This is a simplified logic, assuming we can list files in VikingFS
        # In a real scenario, we'd use VikingFS.list or similar
        try:
            # Placeholder for listing files in VikingFS
            # For now, we'll assume we can get content of specific files if we had their URIs
            # Since VikingFS listing is complex, we might need to use search or other methods
            pass
        except Exception as e:
            logger.error(f"Failed to list files in {uri_base}: {e}")

        # For demonstration, we'll just return an empty dataset or mock some logic
        # In a real implementation, we would:
        # 1. Fetch content from VikingFS
        # 2. Split content into chunks if needed
        # 3. Use LLM to generate (Question, Answer, Context) triples
        return EvalDataset(
            name=f"gen_{uuid.uuid4().hex[:8]}",
            description=f"Generated from {uri_base}",
            samples=[]
        )

    async def generate_from_content(
        self,
        content: str,
        count: int = 3,
        source_name: str = "raw_content",
    ) -> EvalDataset:
        """
        Generate evaluation samples from raw text content.

        Args:
            content: The text content to generate from
            count: Number of samples to generate
            source_name: Name of the source for metadata

        Returns:
            EvalDataset
        """
        if not self.llm:
            raise ValueError("LLM is required for dataset generation")

        # Simplified prompt for generation
        prompt = f"""
        Given the following content, generate {count} question-answer pairs.
        Each pair should include:
        1. A question that can be answered using ONLY the provided content.
        2. The correct answer based on the content.
        3. The specific snippet/context from the content used to answer the question.

        Format the output as a JSON list of objects:
        [{{"question": "...", "answer": "...", "context": "..."}}, ...]

        Content:
        {content[:4000]}
        """

        samples = []
        try:
            # Assuming self.llm has a method like get_completion
            # This depends on the LLM abstraction used
            response = await self.llm.get_completion_async(prompt)
            import json

            from json_repair import repair_json

            clean_json = repair_json(response)
            data = json.loads(clean_json)

            for item in data:
                samples.append(
                    EvalSample(
                        query=item["question"],
                        ground_truth=item["answer"],
                        context=[item["context"]],
                        meta={"source": source_name}
                    )
                )
        except Exception as e:
            logger.error(f"Failed to generate samples: {e}")

        return EvalDataset(
            name=f"gen_{source_name}",
            samples=samples
        )
