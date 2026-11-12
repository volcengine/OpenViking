# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
RAG Query Pipeline for OpenViking evaluation.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Union

from openviking.parse.parsers.upload_utils import is_text_file
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
        server_url: str = "http://127.0.0.1:1933",
    ):
        """
        Initialize the RAG pipeline.

        Args:
            config_path: Path to OpenViking config file
            server_url: OpenViking HTTP server URL
        """
        self.config_path = config_path
        self.server_url = server_url
        self._client: Any = None
        self._llm: Any = None

    def _get_client(self):
        """Lazy initialization of OpenViking client."""
        if self._client is None:
            from openviking_sdk import SyncHTTPClient

            self._client = SyncHTTPClient(url=self.server_url)
            self._client.initialize()
            logger.info("OpenViking client initialized")
        return self._client

    def _get_llm(self):
        """Lazy initialization of LLM for answer generation."""
        if self._llm is None:
            from openviking.config.vlm import ClusterVLMResolver
            from openviking_cli.utils.config.open_viking_config import (
                OpenVikingConfigSingleton,
            )

            config_path = Path(self.config_path).expanduser()
            if not config_path.is_file():
                raise FileNotFoundError(f"VLM config file does not exist: {config_path}")
            config = OpenVikingConfigSingleton.initialize(config_path=str(config_path))
            self._llm = ClusterVLMResolver(lambda: config).get_vlm_sync()
        return self._llm

    def add_documents(
        self,
        docs_dirs: List[Union[str, Path]],
        wait: bool = True,
    ) -> List[str]:
        """
        Add document directories/files to OpenViking.

        Args:
            docs_dirs: List of document directory or file paths
            wait: Whether to wait for processing

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
                str(path),
                wait=wait,
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
    ) -> List[str]:
        """
        Add code repositories to OpenViking.

        Args:
            code_dirs: List of code repository paths (local or git URLs)
            wait: Whether to wait for processing

        Returns:
            List of root URIs for added resources
        """
        return self.add_documents(code_dirs, wait=wait)

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
            'contexts' contains exactly the evidence used for generation (at most
            three chunks), or all materialized evidence in retrieval-only mode.
            'retrieved_contexts' retains all candidates with provenance; 'errors'
            records unsupported media, read failures, and generation failures.
        """
        client = self._get_client()
        start = time.perf_counter()

        # Retrieve contexts
        logger.debug(f"Searching for: {question}")
        search_result = client.search(
            question,
            limit=top_k,
        )

        retrieved_contexts = []
        retrieved_uris = []
        errors = []

        items = (
            search_result.get("memories", [])
            + search_result.get("resources", [])
            + search_result.get("skills", [])
        )
        for item in items or []:
            if isinstance(item, dict):
                uri = item.get("uri", "")
            else:
                uri = getattr(item, "uri", "")
                item = vars(item)
            retrieved_uris.append(uri)
            level = item.get("level", 2)
            source = {0: "abstract", 1: "overview"}.get(level, "body")
            try:
                if level in (0, 1):
                    content = item.get(source) or getattr(client, source)(uri)
                else:
                    if not is_text_file(uri):
                        errors.append({"uri": uri, "stage": "unsupported_media"})
                        continue
                    content = item.get("content")
                    if content:
                        source = "inline_content"
                    else:
                        content = client.read(uri)
                if not isinstance(content, str) or not content.strip():
                    errors.append({"uri": uri, "stage": "empty_context"})
                    continue
            except Exception as e:
                errors.append({"uri": uri, "stage": "read", "message": str(e)})
                continue
            retrieved_contexts.append(
                {
                    "uri": uri,
                    "content": content,
                    "content_source": source,
                    "score": item.get("score", 0.0),
                }
            )

        retrieval_time = time.perf_counter() - start
        if not retrieved_contexts and not errors:
            errors.append({"stage": "empty_context"})
        selected = retrieved_contexts[:3] if generate_answer else retrieved_contexts
        contexts = [ctx["content"] for ctx in selected]

        result = {
            "question": question,
            "contexts": contexts,
            "context_uris": [ctx["uri"] for ctx in selected],
            "retrieved_contexts": retrieved_contexts,
            "retrieved_uris": retrieved_uris,
            "answer": None,
            "errors": errors,
            "retrieval_time": retrieval_time,
            "generation_time": 0.0,
        }

        # Generate answer if requested
        if generate_answer:
            start = time.perf_counter()
            context_text = "\n\n---\n\n".join(contexts)

            prompt = f"""Based on the following context, please answer the question.
If the context does not contain enough information to answer the question, say "I cannot answer this question based on the provided context."

Context:
{context_text}

Question: {question}

Answer:"""

            try:
                llm = self._get_llm()
                answer = llm.get_completion(prompt)
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Answer model returned an empty response")
                result["answer"] = answer
            except Exception as e:
                logger.error(f"Failed to generate answer: {e}")
                errors.append({"stage": "generation", "message": str(e)})
            result["generation_time"] = time.perf_counter() - start

        return result

    def close(self):
        """Close the OpenViking client."""
        if self._client:
            self._client.close()
            self._client = None
