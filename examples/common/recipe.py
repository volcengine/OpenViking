#!/usr/bin/env python3
"""
RAG Pipeline - Retrieval-Augmented Generation using OpenViking + LLM
Focused on querying and answer generation, not resource management
"""

import json
import time
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

    def __init__(self, config_path: str = "./ov.conf", data_path: str = "./data"):
        """
        Initialize RAG pipeline

        Args:
            config_path: Path to config file with LLM settings
            data_path: Path to OpenViking data directory
        """
        # Load configuration
        with open(config_path, "r") as f:
            self.config_dict = json.load(f)

        # Extract LLM config
        self.vlm_config = self.config_dict.get("vlm", {})
        self.api_base = self.vlm_config.get("api_base")
        self.api_key = self.vlm_config.get("api_key")
        self.model = self.vlm_config.get("model")

        # Initialize OpenViking client
        config = OpenVikingConfig.from_dict(self.config_dict)
        self.client = ov.SyncOpenViking(path=data_path, config=config)
        self.client.initialize()

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
        results = self.client.search(query, target_uri=target_uri, score_threshold=score_threshold)

        # Extract top results
        search_results = []
        for _i, resource in enumerate(
            results.resources[:top_k] + results.memories[:top_k]
        ):  # ignore SKILLs for mvp
            try:
                content = self.client.read(resource.uri)
                search_results.append(
                    {
                        "uri": resource.uri,
                        "score": resource.score,
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
        url = f"{self.api_base}/chat/completions"

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        print(f"🤖 Calling LLM: {self.model}")
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        result = response.json()
        answer = result["choices"][0]["message"]["content"]

        return answer

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
