# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""High-level OpenViking context lifecycle helpers for LangChain."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

try:
    from langchain_core.messages import SystemMessage
    from langchain_core.runnables import ConfigurableFieldSpec, RunnableLambda
    from langchain_core.runnables.history import RunnableWithMessageHistory
except ImportError as exc:  # pragma: no cover - exercised by optional import path
    from openviking.integrations.langchain.client import missing_dependency

    raise missing_dependency("langchain", "langchain-core") from exc

from openviking.integrations.langchain.client import (
    OpenVikingCommitPolicy,
    OpenVikingConnection,
    call_openviking,
    ensure_client,
    extract_message_text,
    get_latest_user_text,
)
from openviking.integrations.langchain.history import (
    OpenVikingChatMessageHistory,
    context_parts_from_documents,
)
from openviking.integrations.langchain.retrievers import OpenVikingRetriever

OPENVIKING_CONTEXT_MARKER = "<openviking_context>"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenVikingAssembledContext:
    """Context block plus structured references assembled before an agent turn."""

    block: str = ""
    context_parts: list[dict[str, Any]] = field(default_factory=list)
    session_context: dict[str, Any] = field(default_factory=dict)
    recall_documents: list[Any] = field(default_factory=list)


class OpenVikingSessionContextAssembler:
    """Assemble session working memory, archive context, and recall results."""

    def __init__(
        self,
        *,
        client: Any = None,
        retriever: OpenVikingRetriever | None = None,
        url: str | None = None,
        api_key: str | None = None,
        account: str | None = None,
        user: str | None = None,
        user_id: str | None = None,
        agent_id: str | None = None,
        path: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        auto_initialize: bool = True,
        target_uri: str | list[str] = "",
        limit: int = 5,
        score_threshold: float | None = None,
        token_budget: int = 128_000,
        include_session_context: bool = True,
        include_active_messages: bool = True,
        include_recall: bool = True,
        recall_header: str = "Relevant OpenViking context:",
    ):
        self._connection = OpenVikingConnection(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
        )
        self.retriever = retriever or OpenVikingRetriever(
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
            target_uri=target_uri,
            limit=limit,
            score_threshold=score_threshold,
            search_mode="search",
        )
        self.token_budget = token_budget
        self.include_session_context = include_session_context
        self.include_active_messages = include_active_messages
        self.include_recall = include_recall
        self.recall_header = recall_header
        self._client_cache: Any = None

    def assemble(self, *, session_id: str, query: str = "") -> OpenVikingAssembledContext:
        client = self._get_client()
        self._ensure_session(client, session_id)
        session_context = self._get_session_context(client, session_id)
        recall_documents = self._get_recall_documents(session_id, query)
        block = self._format_context_block(session_context, recall_documents)
        return OpenVikingAssembledContext(
            block=block,
            context_parts=context_parts_from_documents(recall_documents),
            session_context=session_context,
            recall_documents=recall_documents,
        )

    def _get_client(self) -> Any:
        if self._client_cache is None:
            self._client_cache = ensure_client(self._connection)
        return self._client_cache

    def _ensure_session(self, client: Any, session_id: str) -> None:
        try:
            call_openviking(client, "create_session", session_id=session_id)
        except Exception:
            logger.debug("OpenViking session ensure failed", exc_info=True)
            pass

    def _get_session_context(self, client: Any, session_id: str) -> dict[str, Any]:
        if not self.include_session_context:
            return {}
        try:
            return call_openviking(
                client,
                "get_session_context",
                session_id=session_id,
                token_budget=self.token_budget,
            )
        except Exception:
            logger.debug("OpenViking session context assembly failed", exc_info=True)
            return {}

    def _get_recall_documents(self, session_id: str, query: str) -> list[Any]:
        if not self.include_recall or not query:
            return []
        try:
            return list(_retriever_for_session(self.retriever, session_id).invoke(query))
        except Exception:
            logger.debug("OpenViking recall retrieval failed", exc_info=True)
            return []

    def _format_context_block(
        self,
        session_context: dict[str, Any],
        recall_documents: Sequence[Any],
    ) -> str:
        sections: list[str] = []
        latest_archive = str(session_context.get("latest_archive_overview") or "").strip()
        if latest_archive:
            sections.append("Session archive overview:\n" + latest_archive)

        abstracts = []
        for archive in session_context.get("pre_archive_abstracts") or []:
            archive_id = archive.get("archive_id") or "archive"
            abstract = str(archive.get("abstract") or "").strip()
            if abstract:
                abstracts.append(f"[{archive_id}] {abstract}")
        if abstracts:
            sections.append("Older archive abstracts:\n" + "\n".join(abstracts))

        if self.include_active_messages:
            active_messages = [
                _format_session_message(message)
                for message in session_context.get("messages") or []
            ]
            active_messages = [message for message in active_messages if message]
            if active_messages:
                sections.append("Active session messages:\n" + "\n".join(active_messages))

        if recall_documents:
            recall_lines = []
            for index, doc in enumerate(recall_documents, start=1):
                metadata = getattr(doc, "metadata", {}) or {}
                uri = metadata.get("openviking_uri") or metadata.get("source") or ""
                recall_lines.append(f"[{index}] {uri}\n{doc.page_content}".strip())
            sections.append(self.recall_header + "\n\n" + "\n\n".join(recall_lines))

        if not sections:
            return ""
        return f"{OPENVIKING_CONTEXT_MARKER}\n" + "\n\n".join(sections) + "\n</openviking_context>"


def with_openviking_context(
    runnable: Any,
    *,
    client: Any = None,
    url: str | None = None,
    api_key: str | None = None,
    account: str | None = None,
    user: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    path: str | None = None,
    timeout: float = 60.0,
    extra_headers: dict[str, str] | None = None,
    auto_initialize: bool = True,
    session_id: str | None = None,
    target_uri: str | list[str] = "",
    limit: int = 5,
    score_threshold: float | None = None,
    token_budget: int = 128_000,
    input_messages_key: str | None = None,
    output_messages_key: str | None = None,
    history_messages_key: str | None = None,
    commit_policy: OpenVikingCommitPolicy | None = None,
    session_id_config_key: str = "session_id",
    inject_context: bool = True,
) -> RunnableWithMessageHistory:
    """Wrap a LangChain runnable with OpenViking context and message history."""

    assembler = OpenVikingSessionContextAssembler(
        client=client,
        url=url,
        api_key=api_key,
        account=account,
        user=user,
        user_id=user_id,
        agent_id=agent_id,
        path=path,
        timeout=timeout,
        extra_headers=extra_headers,
        auto_initialize=auto_initialize,
        target_uri=target_uri,
        limit=limit,
        score_threshold=score_threshold,
        token_budget=token_budget,
        include_active_messages=False,
        include_recall=inject_context,
    )
    pending_context_parts: dict[str, list[dict[str, Any]]] = {}

    def make_history(active_session_id: str) -> OpenVikingChatMessageHistory:
        return OpenVikingChatMessageHistory(
            session_id=active_session_id,
            client=client,
            url=url,
            api_key=api_key,
            account=account,
            user=user,
            user_id=user_id,
            agent_id=agent_id,
            path=path,
            timeout=timeout,
            extra_headers=extra_headers,
            auto_initialize=auto_initialize,
            token_budget=token_budget,
            commit_policy=commit_policy,
            context_parts_provider=lambda current_session_id: pending_context_parts.pop(
                current_session_id,
                [],
            ),
        )

    if session_id is None:

        def session_history_factory(resolved_session_id: str) -> OpenVikingChatMessageHistory:
            return make_history(
                _validate_session_id(resolved_session_id, key=session_id_config_key)
            )

        history_factory_config = [
            ConfigurableFieldSpec(
                id=session_id_config_key,
                annotation=str,
                name="Session ID",
                description=("Required OpenViking session ID for dynamic chat history and recall."),
                default="",
                is_shared=True,
            )
        ]
    else:

        def session_history_factory() -> OpenVikingChatMessageHistory:
            return make_history(session_id)

        history_factory_config = None

    def inject(input_value: Any, config: dict[str, Any] | None = None) -> Any:
        if not inject_context:
            return input_value
        resolved_session_id = session_id or _session_id_from_config(
            config,
            key=session_id_config_key,
        )
        pending_context_parts.pop(resolved_session_id, None)
        query = _latest_user_text_from_input(input_value, input_messages_key)
        assembled = assembler.assemble(session_id=resolved_session_id, query=query)
        if not assembled.block:
            return input_value
        if assembled.context_parts:
            pending_context_parts[resolved_session_id] = assembled.context_parts
        return _inject_system_context(input_value, assembled.block, input_messages_key)

    def clear_pending_on_error(_run: Any, config: dict[str, Any] | None = None) -> None:
        del config
        if session_id is not None:
            pending_context_parts.pop(session_id, None)
        else:
            pending_context_parts.clear()

    bound = (RunnableLambda(inject) | runnable).with_listeners(on_error=clear_pending_on_error)
    return RunnableWithMessageHistory(
        bound,
        session_history_factory,
        input_messages_key=input_messages_key,
        output_messages_key=output_messages_key,
        history_messages_key=history_messages_key,
        history_factory_config=history_factory_config,
    )


def _session_id_from_config(config: dict[str, Any] | None, *, key: str) -> str:
    configurable = (config or {}).get("configurable") or {}
    return _validate_session_id(configurable.get(key), key=key)


def _validate_session_id(value: Any, *, key: str) -> str:
    session_id = str(value or "").strip()
    if not session_id:
        raise ValueError(
            "OpenViking dynamic sessions require "
            f"config={{'configurable': {{'{key}': '<session-id>'}}}}. "
            "Pass session_id='...' to with_openviking_context for no-config usage."
        )
    return session_id


def _retriever_for_session(retriever: Any, session_id: str) -> Any:
    if hasattr(retriever, "model_copy"):
        return retriever.model_copy(
            update={
                "session_id": session_id,
                "search_mode": "search",
            }
        )
    return retriever


def _latest_user_text_from_input(input_value: Any, input_messages_key: str | None) -> str:
    messages = _input_messages(input_value, input_messages_key)
    if messages:
        return get_latest_user_text(messages)
    if isinstance(input_value, str):
        return input_value
    if isinstance(input_value, dict):
        for value in input_value.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _input_messages(input_value: Any, input_messages_key: str | None) -> list[Any]:
    if isinstance(input_value, list):
        return list(input_value)
    if isinstance(input_value, dict):
        key = input_messages_key or "messages"
        value = input_value.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _inject_system_context(
    input_value: Any,
    context_block: str,
    input_messages_key: str | None,
) -> Any:
    if isinstance(input_value, list):
        return _merge_system_message(input_value, context_block)
    if isinstance(input_value, dict):
        key = input_messages_key or "messages"
        if isinstance(input_value.get(key), list):
            updated = dict(input_value)
            updated[key] = _merge_system_message(input_value[key], context_block)
            return updated
        updated = dict(input_value)
        updated["openviking_context"] = context_block
        return updated
    return input_value


def _merge_system_message(messages: Sequence[Any], context_block: str) -> list[Any]:
    updated = list(messages)
    for index, message in enumerate(updated):
        if isinstance(message, SystemMessage):
            content = extract_message_text(message.content)
            updated[index] = SystemMessage(content=f"{content}\n\n{context_block}".strip())
            return updated
    return [SystemMessage(content=context_block), *updated]


def _format_session_message(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "assistant")
    parts = message.get("parts") or []
    chunks: list[str] = []
    for part in parts:
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            chunks.append(str(part["text"]))
        elif part_type == "context" and part.get("abstract"):
            chunks.append(f"[context] {part['abstract']}")
        elif part_type == "tool":
            tool_name = part.get("tool_name") or "tool"
            status = part.get("tool_status") or "completed"
            output = part.get("tool_output") or ""
            chunks.append(f"[tool:{tool_name} ({status})] {output}".strip())
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    return f"[{role}] {text}" if text else ""
