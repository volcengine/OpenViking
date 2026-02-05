"""
Memex RAG Recipe - RAG flow implementation for Memex.

Based on examples/common/recipe.py pattern with Tier 2 improvements:
- OpenViking Session integration for context-aware search
- Automatic memory extraction via session.commit()
- Conversation-aware query rewriting
- Confidence-based uncertainty hints
- Smart context management (L0/L2 based on score)
"""

from typing import Any, Optional

from openai import OpenAI

from client import MemexClient
from config import MemexConfig

try:
    from openviking.message import TextPart, ContextPart
except ImportError:
    TextPart = None
    ContextPart = None


DEFAULT_SYSTEM_PROMPT = """You are Memex, a personal knowledge assistant. 
You help users find and understand information from their personal knowledge base.

When answering questions:
1. Base your answers on the provided context from the knowledge base
2. If the context doesn't contain relevant information, say so clearly
3. Cite sources using [Source N] format when referencing information
4. Be concise but thorough

{confidence_hint}

Context from knowledge base:
{context}
"""

HIGH_CONFIDENCE_HINT = ""
LOW_CONFIDENCE_HINT = """Note: The search results have relatively low relevance scores. 
The information below may not directly answer the question. 
If unsure, acknowledge the limitation and suggest what additional information might help."""

NO_RESULTS_HINT = """Note: No relevant information was found in the knowledge base for this query.
Please let the user know and suggest they add relevant documents or rephrase their question."""


class MemexRecipe:
    def __init__(
        self,
        client: MemexClient,
        config: Optional[MemexConfig] = None,
    ):
        self.client = client
        self.config = config or client.config
        self._llm_client: Optional[OpenAI] = None
        self._chat_history: list[dict[str, str]] = []
        self._vlm_config: Optional[dict] = None
        self._session = None

        self.high_confidence_threshold = 0.25
        self.low_confidence_threshold = 0.15

    def start_session(self, session_id: Optional[str] = None):
        """Start or resume an OpenViking session for context-aware search."""
        self._session = self.client.get_session(session_id)
        if session_id:
            try:
                self._session.load()
            except Exception:
                pass
        return self._session

    def end_session(self) -> dict[str, Any]:
        """End session and extract long-term memories."""
        if not self._session:
            return {"status": "no_session"}
        try:
            result = self._session.commit()
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @property
    def session(self):
        """Get current OpenViking session."""
        return self._session

    @property
    def session_id(self) -> Optional[str]:
        """Get current session ID."""
        return self._session.session_id if self._session else None

    @property
    def vlm_config(self) -> dict:
        if self._vlm_config is None:
            self._vlm_config = self.config.get_vlm_config()
        return self._vlm_config

    @property
    def llm_client(self) -> OpenAI:
        if self._llm_client is None:
            vlm = self.vlm_config
            backend = vlm.get("backend", "openai")

            if backend == "openai":
                self._llm_client = OpenAI(
                    api_key=vlm.get("api_key"),
                    base_url=vlm.get("api_base"),
                )
            elif backend == "volcengine":
                self._llm_client = OpenAI(
                    api_key=vlm.get("api_key"),
                    base_url=vlm.get("api_base") or "https://ark.cn-beijing.volces.com/api/v3",
                )
            else:
                raise ValueError(f"Unsupported LLM backend: {backend}")
        return self._llm_client

    @property
    def llm_model(self) -> str:
        return self.vlm_config.get("model", "gpt-4o-mini")

    def _should_use_deep_search(self, query: str) -> bool:
        if self._session and hasattr(self._session, "messages") and len(self._session.messages) > 2:
            return True
        pronouns = ["它", "这个", "那个", "他", "她", "this", "that", "it", "they", "them"]
        if any(p in query.lower() for p in pronouns):
            return True
        if len(self._chat_history) > 0:
            return True
        return False

    def find(
        self,
        query: str,
        top_k: Optional[int] = None,
        target_uri: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        top_k = top_k or self.config.search_top_k
        target_uri = target_uri or self.config.default_resource_uri
        score_threshold = score_threshold or self.config.search_score_threshold

        results = self.client.find(
            query=query,
            target_uri=target_uri,
            top_k=top_k,
            score_threshold=score_threshold,
        )

        return self._process_search_results(results, top_k)

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        target_uri: Optional[str] = None,
        score_threshold: Optional[float] = None,
        use_session: bool = True,
        auto_select: bool = True,
    ) -> list[dict[str, Any]]:
        top_k = top_k or self.config.search_top_k
        target_uri = target_uri or self.config.default_resource_uri
        score_threshold = score_threshold or self.config.search_score_threshold

        if auto_select and not self._should_use_deep_search(query):
            return self.find(query, top_k, target_uri, score_threshold)

        session_to_use = self._session if use_session else None

        results = self.client.search(
            query=query,
            target_uri=target_uri,
            top_k=top_k,
            score_threshold=score_threshold,
            session=session_to_use,
        )

        return self._process_search_results(results, top_k)

    def _process_search_results(self, results: Any, top_k: int) -> list[dict[str, Any]]:
        search_results = []

        all_items = []
        if hasattr(results, "resources"):
            all_items.extend(results.resources[:top_k])
        if hasattr(results, "memories"):
            all_items.extend(results.memories[:top_k])

        for r in all_items:
            try:
                uri = r.uri if hasattr(r, "uri") else str(r)
                score = r.score if hasattr(r, "score") else 0.0

                if score >= self.high_confidence_threshold:
                    try:
                        content = self.client.read(uri)
                        content = content[:2000] if content else ""
                    except Exception as e:
                        if "is a directory" in str(e):
                            try:
                                content = f"[Directory] {self.client.abstract(uri)}"
                            except Exception:
                                continue
                        else:
                            continue
                else:
                    try:
                        content = self.client.abstract(uri)
                        if not content:
                            content = self.client.read(uri)[:500] if self.client.read(uri) else ""
                    except Exception:
                        continue

                search_results.append(
                    {
                        "uri": uri,
                        "score": score,
                        "content": content,
                    }
                )
            except Exception:
                continue

        search_results.sort(key=lambda x: x["score"], reverse=True)
        return search_results

    def rerank(
        self,
        query: str,
        results: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if len(results) <= 1:
            return results

        results_text = ""
        for i, r in enumerate(results[:10]):
            content = r.get("content", "")[:300]
            results_text += f"[{i}] {content}\n\n"

        rerank_prompt = f"""Rate each search result's relevance to the query (0-10).

Query: {query}

Results:
{results_text}

Return JSON array: [{{"index": 0, "score": 8}}, ...]
Only return the JSON, no explanation."""

        try:
            response = self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": rerank_prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            content = response.choices[0].message.content or "[]"

            import json
            import re

            json_match = re.search(r"\[.*\]", content, re.DOTALL)
            if json_match:
                scores = json.loads(json_match.group())
                score_map = {
                    s["index"]: s["score"] for s in scores if "index" in s and "score" in s
                }

                for i, r in enumerate(results):
                    if i in score_map:
                        r["rerank_score"] = score_map[i] / 10.0
                    else:
                        r["rerank_score"] = r.get("score", 0.0)

                results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        except Exception:
            pass

        return results[:top_k]

    def _get_confidence_level(self, search_results: list[dict[str, Any]]) -> str:
        if not search_results:
            return "none"

        max_score = max(r["score"] for r in search_results)
        avg_score = sum(r["score"] for r in search_results) / len(search_results)

        if (
            max_score >= self.high_confidence_threshold
            and avg_score >= self.low_confidence_threshold
        ):
            return "high"
        elif max_score >= self.low_confidence_threshold:
            return "low"
        else:
            return "very_low"

    def build_context(self, search_results: list[dict[str, Any]]) -> tuple[str, str]:
        """Build context and return (context_str, confidence_hint)."""
        if not search_results:
            return "No relevant information found in the knowledge base.", NO_RESULTS_HINT

        confidence = self._get_confidence_level(search_results)

        if confidence == "high":
            confidence_hint = HIGH_CONFIDENCE_HINT
        elif confidence == "low":
            confidence_hint = LOW_CONFIDENCE_HINT
        else:
            confidence_hint = LOW_CONFIDENCE_HINT

        context_parts = []
        for i, result in enumerate(search_results, 1):
            uri = result.get("uri", "unknown")
            content = result.get("content", "")
            score = result.get("score", 0.0)

            if not content:
                try:
                    content = self.client.read(uri)
                except Exception:
                    try:
                        content = self.client.abstract(uri)
                    except Exception:
                        content = f"[Content from {uri}]"

            context_parts.append(f"[Source {i}] {uri} (relevance: {score:.2f})\n{content}")

        return "\n\n---\n\n".join(context_parts), confidence_hint

    def call_llm(
        self,
        messages: list[dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        temperature = temperature if temperature is not None else self.config.llm_temperature
        max_tokens = max_tokens or self.config.llm_max_tokens

        response = self.llm_client.chat.completions.create(
            model=self.llm_model,
            messages=messages,  # type: ignore
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content or ""

    def query(
        self,
        user_query: str,
        search_top_k: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        score_threshold: Optional[float] = None,
        target_uri: Optional[str] = None,
        use_chat_history: bool = False,
        use_rerank: bool = True,
    ) -> str:
        if self._session and TextPart:
            self._session.add_message("user", [TextPart(text=user_query)])

        search_results = self.search(
            query=user_query,
            top_k=(search_top_k or 5) * 2 if use_rerank else search_top_k,
            target_uri=target_uri,
            score_threshold=score_threshold,
            use_session=True,
        )

        if use_rerank and len(search_results) > 1:
            search_results = self.rerank(user_query, search_results, top_k=search_top_k or 5)

        if self._session and ContextPart:
            for result in search_results[:3]:
                try:
                    self._session.add_message(
                        "assistant",
                        [
                            ContextPart(
                                uri=result.get("uri", ""),
                                context_type="resource",
                                abstract=result.get("content", "")[:200],
                            )
                        ],
                    )
                except Exception:
                    pass

        context, confidence_hint = self.build_context(search_results)

        system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        formatted_system_prompt = system_prompt.format(
            context=context,
            confidence_hint=confidence_hint,
        )

        messages = [{"role": "system", "content": formatted_system_prompt}]

        if use_chat_history and self._chat_history:
            messages.extend(self._chat_history[-6:])

        messages.append({"role": "user", "content": user_query})

        response = self.call_llm(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if self._session and TextPart:
            self._session.add_message("assistant", [TextPart(text=response)])

        if use_chat_history:
            self._chat_history.append({"role": "user", "content": user_query})
            self._chat_history.append({"role": "assistant", "content": response})

        return response

    def clear_history(self) -> None:
        self._chat_history = []

    @property
    def chat_history(self) -> list[dict[str, str]]:
        return self._chat_history.copy()
