#!/usr/bin/env python3
"""
RAG Pipeline - Retrieval-Augmented Generation using OpenViking + LLM
Focused on querying and answer generation, not resource management
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


class Recipe:
    """
    Recipe (Boring name is RAG Pipeline)

    Combines semantic search with LLM generation:
    1. Search OpenViking database for relevant context
    2. Send context + query to LLM
    3. Return generated answer with sources
    """

    def __init__(
        self,
        config_path: Optional[str] = "./ov.conf",
        data_path: str = "./data",
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        account: Optional[str] = None,
        user: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout: float = 60.0,
        llm_api_key: Optional[str] = None,
    ):
        """
        Initialize RAG pipeline

        Args:
            config_path: Optional path to config file with LLM settings
            data_path: Path to local OpenViking data directory
            server_url: Optional remote OpenViking HTTP server URL
            api_key: Optional OpenViking HTTP API key
            account: Optional OpenViking account header
            user: Optional OpenViking user header
            agent_id: Optional OpenViking agent header
            timeout: Timeout for both OpenViking HTTP calls and LLM calls
            llm_api_key: Optional override for the query LLM API key
        """
        self.config_path = config_path
        self.data_path = data_path
        self.server_url = server_url
        self.timeout = timeout
        self.mode = "http" if server_url else "local"
        self.config_dict: Dict[str, Any] = {}

        if config_path and Path(config_path).is_file():
            with open(config_path, "r", encoding="utf-8") as f:
                self.config_dict = json.load(f)
        elif config_path and not server_url:
            raise FileNotFoundError(f"Config file not found: {config_path}")

        # Extract LLM config
        self.vlm_config = self.config_dict.get("vlm", {})
        self.api_base = self.vlm_config.get("api_base")
        self.api_key = llm_api_key or self.vlm_config.get("api_key")
        self.model = self.vlm_config.get("model")
        self.extra_headers = dict(self.vlm_config.get("extra_headers") or {})

        # Initialize OpenViking client
        if server_url:
            self.client = ov.SyncHTTPClient(
                url=server_url,
                api_key=api_key,
                agent_id=agent_id,
                account=account,
                user=user,
                timeout=timeout,
            )
        else:
            config = OpenVikingConfig.from_dict(self.config_dict)
            self.client = ov.SyncOpenViking(path=data_path, config=config)
        self.client.initialize()

    @property
    def query_ready(self) -> bool:
        """Whether the query tool has enough LLM config to answer questions."""
        return bool(self.api_base and self.model)

    def _get_result_timestamps(self, resource: Any) -> Dict[str, Optional[str]]:
        """Resolve timestamps for a search result.

        Prefer metadata already present on the matched context. If the remote server
        does not expose timestamps in search results yet, fall back to filesystem
        metadata from `stat()` and map `modTime` to `updated_at`.
        """
        created_at = getattr(resource, "created_at", None)
        updated_at = getattr(resource, "updated_at", None)

        if created_at or updated_at:
            return {
                "created_at": created_at,
                "updated_at": updated_at,
            }

        try:
            stat_result = self.client.stat(resource.uri)
        except Exception:
            return {
                "created_at": None,
                "updated_at": None,
            }

        return {
            "created_at": stat_result.get("created_at") or stat_result.get("createTime"),
            "updated_at": (
                stat_result.get("updated_at")
                or stat_result.get("modTime")
                or stat_result.get("modified_at")
            ),
        }

    def search(
        self,
        query: str,
        top_k: int = 3,
        target_uri: Optional[str] = None,
        score_threshold: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant content using semantic search

        Args:
            query: Search query
            top_k: Number of results to return
            target_uri: Optional specific URI to search in. If None, searches all resources.
            score_threshold: Minimum relevance score for search results (default: 0.2)

        Returns:
            List of search results with content and scores
        """
        # print(f"🔍 Searching for: '{query}'")

        # Search all resources or specific target
        # `find` has better performance, but not so smart
        search_target = target_uri or ""
        results = self.client.search(query, target_uri=search_target, score_threshold=score_threshold)

        # Extract top results
        search_results = []
        for _i, resource in enumerate(
            results.resources[:top_k] + results.memories[:top_k]
        ):  # ignore SKILLs for mvp
            timestamps = self._get_result_timestamps(resource)
            try:
                content = self.client.read(resource.uri)
                search_results.append(
                    {
                        "uri": resource.uri,
                        "score": resource.score,
                        "created_at": timestamps["created_at"],
                        "updated_at": timestamps["updated_at"],
                        "content": content,
                    }
                )
                # print(f"  {i + 1}. {resource.uri} (score: {resource.score:.4f})")

            except Exception as e:
                # Handle directories - read their abstract instead
                if "is a directory" in str(e):
                    try:
                        abstract = self.client.abstract(resource.uri)
                        search_results.append(
                            {
                                "uri": resource.uri,
                                "score": resource.score,
                                "created_at": timestamps["created_at"],
                                "updated_at": timestamps["updated_at"],
                                "content": f"[Directory Abstract] {abstract}",
                            }
                        )
                        # print(f"  {i + 1}. {resource.uri} (score: {resource.score:.4f}) [directory]")
                    except:
                        # Skip if we can't get abstract
                        continue
                else:
                    # Skip other errors
                    continue

        return search_results

    def call_llm(
        self, messages: List[Dict[str, str]], temperature: float = 0.7, max_tokens: int = 2048
    ) -> str:
        """
        Call LLM API to generate response

        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
                     Each message should have format: {"role": "user|assistant|system", "content": "..."}
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            LLM response text
        """
        if not self.query_ready:
            raise RuntimeError(
                "The query tool requires a local ov.conf with vlm.api_base and vlm.model. "
                "Start the MCP bridge with --config /path/to/ov.conf, or use the search tool."
            )

        url = f"{self.api_base.rstrip('/')}/chat/completions"

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        print(f"🤖 Calling LLM: {self.model}")
        response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()

        result = response.json()
        answer = result["choices"][0]["message"]["content"]

        return answer

    def add_resource(self, resource_path: str, wait_timeout: float = 300) -> str:
        """
        Add a resource through the configured OpenViking client.

        In HTTP mode the underlying client automatically uploads local files to the
        remote OpenViking server before indexing.
        """
        result = self.client.add_resource(path=resource_path)

        if result and "root_uri" in result:
            root_uri = result["root_uri"]
            self.client.wait_processed(timeout=wait_timeout)
            return f"Resource added and indexed: {root_uri}"
        if result and result.get("status") == "error":
            errors = result.get("errors", [])[:3]
            error_msg = "\n".join(f"  - {e}" for e in errors)
            return f"Resource had parsing issues:\n{error_msg}\nSome content may still be searchable."
        return "Failed to add resource."

    def create_memory_session(self) -> Dict[str, Any]:
        """Create a new OpenViking session for manual memory capture."""
        return self.client.create_session()

    def get_memory_session(self, session_id: str) -> Dict[str, Any]:
        """Inspect an existing OpenViking memory session."""
        return self.client.get_session(session_id)

    def delete_memory_session(self, session_id: str) -> Dict[str, Any]:
        """Delete an existing OpenViking memory session."""
        self.client.delete_session(session_id)
        return {"session_id": session_id, "deleted": True}

    def add_memory_turn(
        self,
        session_id: str,
        user_message: Optional[str] = None,
        assistant_message: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a user/assistant turn to an OpenViking session."""
        user_text = (user_message or "").strip()
        assistant_text = (assistant_message or "").strip()
        note_text = (note or "").strip()

        if not any((user_text, assistant_text, note_text)):
            raise ValueError("At least one of user_message, assistant_message, or note must be set.")

        if user_text:
            self.client.add_message(session_id, role="user", content=user_text)

        assistant_parts = []
        if assistant_text:
            assistant_parts.append(assistant_text)
        if note_text:
            assistant_parts.append(f"[note]\n{note_text}")
        if assistant_parts:
            self.client.add_message(
                session_id,
                role="assistant",
                content="\n\n".join(assistant_parts),
            )

        session = self.client.get_session(session_id)
        return {
            "session_id": session_id,
            "message_count": session.get("message_count", 0),
        }

    def commit_memory_session(self, session_id: str) -> Dict[str, Any]:
        """Commit a session so OpenViking extracts and indexes memories."""
        result = self.client.commit_session(session_id)
        if isinstance(result, dict):
            result.setdefault("session_id", session_id)
        return result

    def query(
        self,
        user_query: str,
        search_top_k: int = 3,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
        score_threshold: float = 0.2,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Full RAG pipeline: search → retrieve → generate

        Args:
            user_query: User's question
            search_top_k: Number of search results to use as context
            temperature: LLM sampling temperature
            max_tokens: Maximum tokens to generate
            system_prompt: Optional system prompt to prepend
            score_threshold: Minimum relevance score for search results (default: 0.2)
            chat_history: Optional list of previous conversation turns for multi-round chat.
                        Each turn should be a dict with 'role' and 'content' keys.
                        Example: [{"role": "user", "content": "previous question"},
                                  {"role": "assistant", "content": "previous answer"}]

        Returns:
            Dictionary with answer, context, metadata, and timings
        """
        # Track total time
        start_total = time.perf_counter()

        # Step 1: Search for relevant content (timed)
        start_search = time.perf_counter()
        search_results = self.search(
            user_query, top_k=search_top_k, score_threshold=score_threshold
        )
        search_time = time.perf_counter() - start_search

        # Step 2: Build context from search results
        context_text = "no relevant information found, try answer based on existing knowledge."
        if search_results:
            context_text = (
                "Answer should pivoting to the following:\n<context>\n"
                + "\n\n".join(
                    [
                        f"[Source {i + 1}] (relevance: {r['score']:.4f})\n{r['content']}"
                        for i, r in enumerate(search_results)
                    ]
                )
                + "\n</context>"
            )

        # Step 3: Build messages array for chat completion API
        messages = []

        # Add system message if provided
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append(
                {
                    "role": "system",
                    "content": "Answer questions with plain text. avoid markdown special character",
                }
            )

        # Add chat history if provided (for multi-round conversations)
        if chat_history:
            messages.extend(chat_history)

        # Build current turn prompt with context and question
        current_prompt = f"{context_text}\n"
        current_prompt += f"Question: {user_query}\n\n"

        # Add current user message
        messages.append({"role": "user", "content": current_prompt})

        # Step 4: Call LLM with messages array (timed)
        start_llm = time.perf_counter()
        answer = self.call_llm(messages, temperature=temperature, max_tokens=max_tokens)
        llm_time = time.perf_counter() - start_llm

        # Calculate total time
        total_time = time.perf_counter() - start_total

        # Return full result with timing data
        return {
            "answer": answer,
            "context": search_results,
            "query": user_query,
            "prompt": current_prompt,
            "timings": {
                "search_time": search_time,
                "llm_time": llm_time,
                "total_time": total_time,
            },
        }

    def close(self):
        """Clean up resources"""
        self.client.close()
