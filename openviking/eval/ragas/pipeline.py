# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
RAG Query Pipeline for OpenViking evaluation.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class RAGQueryPipeline:
    """
    RAG query pipeline for document and code repositories.

    This pipeline:
    1. Adds documents/code to OpenViking
    2. Performs retrieval for queries
    3. Generates answers using LLM
    """

    def __init__(
        self,
        config_path: str = "./ov.conf",
        data_path: str = "./data",
    ):
        """
        Initialize the RAG pipeline.

        Args:
            config_path: Path to OpenViking config file
            data_path: Path to OpenViking data directory
        """
        self.config_path = config_path
        self.data_path = data_path
        self._client = None
        self._llm = None

    def _get_client(self):
        """Lazy initialization of OpenViking client."""
        if self._client is None:
            import openviking as ov
            from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

            with open(self.config_path, "r") as f:
                config_dict = json.load(f)

            config = OpenVikingConfig.from_dict(config_dict)
            self._client = ov.SyncOpenViking(path=self.data_path, config=config)
            self._client.initialize()
            logger.info("OpenViking client initialized")
        return self._client

    def _get_llm(self):
        """Lazy initialization of LLM for answer generation."""
        if self._llm is None:
            from openviking_cli.utils.config import get_openviking_config

            config = get_openviking_config()
            self._llm = config.vlm
        return self._llm

    def add_documents(
        self,
        docs_dirs: List[Union[str, Path]],
        wait: bool = True,
        timeout: float = 300,
    ) -> List[str]:
        """
        Add document directories/files to OpenViking.

        Args:
            docs_dirs: List of document directory or file paths
            wait: Whether to wait for processing
            timeout: Timeout for waiting

        Returns:
            List of root URIs for added resources
        """
        client = self._get_client()
        root_uris = []

        for doc_path in docs_dirs:
            path = Path(doc_path).expanduser()
            if not path.exists():
                logger.warning(f"Path does not exist: {path}")
                continue

            logger.info(f"Adding document: {path}")
            result = client.add_resource(
                path=str(path),
                wait=wait,
                timeout=timeout,
            )

            if result and "root_uri" in result:
                root_uris.append(result["root_uri"])
                logger.info(f"Added: {result['root_uri']}")
            elif result and result.get("status") == "error":
                errors = result.get("errors", [])
                logger.error(f"Failed to add {path}: {errors}")

        return root_uris

    def add_code_repos(
        self,
        code_dirs: List[Union[str, Path]],
        wait: bool = True,
        timeout: float = 300,
    ) -> List[str]:
        """
        Add code repositories to OpenViking.

        Args:
            code_dirs: List of code repository paths (local or git URLs)
            wait: Whether to wait for processing
            timeout: Timeout for waiting

        Returns:
            List of root URIs for added resources
        """
        return self.add_documents(code_dirs, wait=wait, timeout=timeout)

    def query(
        self,
        question: str,
        top_k: int = 5,
        generate_answer: bool = True,
    ) -> Dict[str, Any]:
        """
        Query the RAG pipeline.

        Args:
            question: The question to answer
            top_k: Number of context chunks to retrieve
            generate_answer: Whether to generate an answer using LLM

        Returns:
            Dict with 'question', 'contexts', 'answer', and 'retrieved_uris'
        """
        client = self._get_client()

        # Retrieve contexts
        logger.debug(f"Searching for: {question}")
        search_result = client.search(
            query=question,
            limit=top_k,
        )

        contexts = []
        retrieved_uris = []

        if search_result and "results" in search_result:
            for item in search_result["results"]:
                uri = item.get("uri", "")
                content = item.get("content", "") or item.get("overview", "") or item.get("abstract", "")
                if content:
                    contexts.append(content)
                    retrieved_uris.append(uri)

        result = {
            "question": question,
            "contexts": contexts,
            "retrieved_uris": retrieved_uris,
            "answer": None,
        }

        # Generate answer if requested
        if generate_answer and contexts:
            llm = self._get_llm()
            context_text = "\n\n---\n\n".join(contexts[:3])

            prompt = f"""Based on the following context, please answer the question.
If the context does not contain enough information to answer the question, say "I cannot answer this question based on the provided context."

Context:
{context_text}

Question: {question}

Answer:"""

            try:
                answer = llm.get_completion(prompt)
                result["answer"] = answer
            except Exception as e:
                logger.error(f"Failed to generate answer: {e}")
                result["answer"] = f"Error generating answer: {str(e)}"

        return result

    def close(self):
        """Close the OpenViking client."""
        if self._client:
            self._client.close()
            self._client = None
