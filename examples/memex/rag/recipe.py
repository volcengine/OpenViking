"""
Memex RAG Recipe - RAG flow implementation for Memex.

Simplified design:
- OpenViking Session integration for context-aware search
- Automatic memory extraction via session.commit()
- Direct use of OpenViking score for ranking (no rerank)
- Unified content loading (no threshold-based truncation)
"""

from typing import Any, Optional

from openai import OpenAI

from ..client import MemexClient
from ..config import MemexConfig

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

Context from knowledge base:
{context}
"""

NO_CONTEXT_PROMPT = """You are Memex, a personal knowledge assistant.
No relevant information was found in the knowledge base for this query.
Please let the user know and suggest they add relevant documents or rephrase their question.
"""


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

    def start_session(self, session_id: Optional[str] = None):
        self._session = self.client.get_session(session_id)
        if session_id:
            try:
                self._session.load()
            except Exception:
                pass
        return self._session

    def end_session(self) -> dict[str, Any]:
        if not self._session:
            return {"status": "no_session"}
        try:
            result = self._session.commit()
            # Wait for memory embedding to complete
            memories_extracted = result.get("memories_extracted", 0)
            if memories_extracted > 0:
                self.client.wait_processed()
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @property
    def session(self):
        return self._session

    @property
    def session_id(self) -> Optional[str]:
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
                    base_url=vlm.get("api_base") or "https://api.openai.com/v1",
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

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        target_uri: Optional[str] = None,
        score_threshold: Optional[float] = None,
        use_session: bool = True,
    ) -> list[dict[str, Any]]:
        top_k = top_k or self.config.search_top_k
        target_uri = target_uri or self.config.default_resource_uri
        score_threshold = score_threshold or self.config.search_score_threshold

        # Use search() without session to get multi-type retrieval (memory + resource + skill)
        # while avoiding the IntentAnalyzer async deadlock that occurs when session is passed.
        session = self._session if use_session else None
        results = self.client.search(
            query=query,
            target_uri=target_uri,
            top_k=top_k,
            score_threshold=score_threshold,
            session=session,
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

                try:
                    content = self.client.read(uri)
                    content = content if content else ""
                except Exception as e:
                    if "is a directory" in str(e):
                        try:
                            content = f"[Directory] {self.client.abstract(uri)}"
                        except Exception:
                            continue
                    else:
                        continue

                if content:
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

    def build_context(self, search_results: list[dict[str, Any]]) -> str:
        if not search_results:
            return ""

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

            context_parts.append(f"[Source {i}] {uri} (score: {score:.2f})\n{content}")

        return "\n\n---\n\n".join(context_parts)

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
    ) -> str:
        if self._session and TextPart:
            self._session.add_message("user", [TextPart(text=user_query)])

        search_results = self.search(
            query=user_query,
            top_k=search_top_k,
            target_uri=target_uri,
            score_threshold=score_threshold,
            use_session=True,
        )

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

        context = self.build_context(search_results)

        if context:
            prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
            formatted_prompt = prompt.format(context=context)
        else:
            formatted_prompt = NO_CONTEXT_PROMPT

        messages = [{"role": "system", "content": formatted_prompt}]

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
