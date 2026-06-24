import asyncio
import base64
import json
import re
import uuid
from typing import Any, Dict, List, Mapping, Optional

from loguru import logger

import openviking as ov
from openviking.core.namespace import uri_parts
from openviking.core.peer_id import normalize_peer_id
from vikingbot.config.loader import load_config

viking_resource_prefix = "viking://resources/"


def _is_session_key(agent_id: Optional[str]) -> bool:
    return agent_id is not None and "__" in agent_id


def _peer_id_from_external_id(peer_id: Optional[str]) -> Optional[str]:
    if not peer_id:
        return None
    raw_peer_id = str(peer_id).strip()
    if not raw_peer_id:
        return None
    if "/" in raw_peer_id or "\\" in raw_peer_id:
        return None
    try:
        return normalize_peer_id(raw_peer_id)
    except ValueError:
        pass

    encoded = base64.urlsafe_b64encode(raw_peer_id.encode("utf-8")).decode("ascii").rstrip("=")
    return normalize_peer_id(f"ext-{encoded}")


class VikingClient:
    def __init__(
        self,
        workspace_id: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        connection: Optional[Mapping[str, Any]] = None,
        actor_peer_id: Optional[str] = None,
    ):
        if agent_id is None:
            agent_id = workspace_id
        if agent_id and "#" in agent_id:
            agent_id = agent_id.split("#", 1)[0]

        config = load_config()
        openviking_config = config.ov_server
        self.openviking_config = openviking_config
        self.workspace_id = agent_id
        self.agent_id = agent_id
        self.ov_path = config.ov_data_path
        self.auth_mode = self._resolve_auth_mode(openviking_config)
        self.mode = "local" if self.auth_mode == "dev" else "remote"
        self.api_key_type = self._resolve_api_key_type(openviking_config)

        self.admin_user_client = None
        self._user_clients = {}
        self._namespace_policy = {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        }
        self._namespace_policy_loaded = False
        self._request_connection = self._normalize_connection(connection)
        self._request_role: str | None = None
        connection_actor_peer_id = (
            self._request_connection.get("actor_peer_id") if self._request_connection else None
        )
        self.actor_peer_id = self._peer_id(actor_peer_id) or self._peer_id(connection_actor_peer_id)

        if self._is_dev_mode():
            client_kwargs = {"url": openviking_config.server_url}
            self._apply_actor_peer_scope(client_kwargs)
            if agent_id is None or _is_session_key(agent_id):
                self.client = ov.AsyncHTTPClient(**client_kwargs)
                self.agent_id = "default"
            else:
                self.client = ov.AsyncHTTPClient(**client_kwargs)
            self.account_id = "default"
            self.user_id = "default"
            self.admin_user_id = "default"
            return

        if self._request_connection:
            self._configure_request_connection(agent_id)
            return

        self.account_id = openviking_config.account_id
        self.admin_user_id = openviking_config.admin_user_id

        api_key = openviking_config.api_key
        remote_client_kwargs = {
            "url": openviking_config.server_url,
            "api_key": api_key,
            "profile_enabled": False,
        }
        if self._is_root_key_mode():
            remote_client_kwargs["account"] = openviking_config.account_id
            remote_client_kwargs["user"] = openviking_config.admin_user_id

        self._apply_actor_peer_scope(remote_client_kwargs)
        self.client = ov.AsyncHTTPClient(**remote_client_kwargs)

    @staticmethod
    def _normalize_auth_value(value: Any) -> str:
        return str(value or "").strip().lower()

    def _resolve_auth_mode(self, openviking_config: Any) -> str:
        effective_auth_mode = self._normalize_auth_value(
            getattr(openviking_config, "effective_auth_mode", None)
        )
        if effective_auth_mode in {"trusted", "api_key", "dev"}:
            return effective_auth_mode

        api_key_type = self._normalize_auth_value(getattr(openviking_config, "api_key_type", None))
        if api_key_type == "root":
            return "trusted"
        return "api_key"

    def _resolve_api_key_type(self, openviking_config: Any) -> str:
        api_key_type = self._normalize_auth_value(getattr(openviking_config, "api_key_type", None))
        if self.auth_mode == "trusted":
            api_key_type = "root"
        elif self.auth_mode in {"api_key", "dev"}:
            api_key_type = "user"
        if api_key_type not in {"root", "user"}:
            raise ValueError(f"Invalid ov_server.api_key_type: {api_key_type}")
        return api_key_type

    @staticmethod
    def _normalize_connection(
        connection: Optional[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(connection, Mapping):
            return None
        normalized: dict[str, Any] = {}
        for key in (
            "api_key",
            "account_id",
            "user_id",
            "agent_id",
            "role",
            "api_key_type",
            "server_url",
            "actor_peer_id",
        ):
            value = connection.get(key)
            if isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
        policy = connection.get("namespace_policy")
        if isinstance(policy, Mapping):
            normalized["namespace_policy"] = {
                "isolate_user_scope_by_agent": bool(
                    policy.get("isolate_user_scope_by_agent", False)
                ),
                "isolate_agent_scope_by_user": bool(
                    policy.get("isolate_agent_scope_by_user", False)
                ),
            }
        if not normalized.get("api_key"):
            if (
                normalized.get("api_key_type") == "root"
                and normalized.get("account_id")
                and normalized.get("user_id")
            ):
                return normalized
            return None
        return normalized

    def _configure_request_connection(self, agent_id: Optional[str]) -> None:
        connection = self._request_connection or {}
        self.mode = "remote"
        request_role = str(connection.get("role") or "").strip().lower()
        self._request_role = request_role or None
        self.api_key_type = self._normalize_auth_value(connection.get("api_key_type")) or "user"
        self.auth_mode = "trusted" if self.api_key_type == "root" else "api_key"
        self.agent_id = connection.get("agent_id") or agent_id
        self.workspace_id = self.agent_id
        self.account_id = connection.get("account_id") or self.openviking_config.account_id
        self.admin_user_id = connection.get("user_id") or self.openviking_config.admin_user_id

        policy = connection.get("namespace_policy")
        if isinstance(policy, dict):
            self._namespace_policy = policy
            self._namespace_policy_loaded = True

        remote_client_kwargs = {
            "url": connection.get("server_url") or self.openviking_config.server_url,
            "profile_enabled": False,
        }
        if connection.get("api_key"):
            remote_client_kwargs["api_key"] = connection["api_key"]
        if self.api_key_type == "root" and self.account_id:
            remote_client_kwargs["account"] = self.account_id
        if self.api_key_type == "root" and self.admin_user_id:
            remote_client_kwargs["user"] = self.admin_user_id

        self._apply_actor_peer_scope(remote_client_kwargs)
        self.client = ov.AsyncHTTPClient(**remote_client_kwargs)

    async def _initialize(self):
        """Initialize the client (must be called after construction)"""
        await self.client.initialize()
        await self._load_namespace_policy()

    @classmethod
    async def create(
        cls,
        workspace_id: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        connection: Optional[Mapping[str, Any]] = None,
        actor_peer_id: Optional[str] = None,
    ):
        """Factory method to create and initialize a VikingClient instance."""
        instance = cls(
            workspace_id=workspace_id,
            agent_id=agent_id,
            connection=connection,
            actor_peer_id=actor_peer_id,
        )
        await instance._initialize()
        return instance

    def _matched_context_to_dict(self, matched_context: Any) -> Dict[str, Any]:
        """将 MatchedContext 对象转换为字典"""
        return {
            "uri": getattr(matched_context, "uri", ""),
            "context_type": str(getattr(matched_context, "context_type", "")),
            "is_leaf": getattr(matched_context, "is_leaf", False),
            "abstract": getattr(matched_context, "abstract", ""),
            "overview": getattr(matched_context, "overview", None),
            "category": getattr(matched_context, "category", ""),
            "score": getattr(matched_context, "score", 0.0),
            "match_reason": getattr(matched_context, "match_reason", ""),
            "relations": [
                self._relation_to_dict(r) for r in getattr(matched_context, "relations", [])
            ],
        }

    def _relation_to_dict(self, relation: Any) -> Dict[str, Any]:
        """将 Relation 对象转换为字典"""
        return {
            "from_uri": getattr(relation, "from_uri", ""),
            "to_uri": getattr(relation, "to_uri", ""),
            "relation_type": getattr(relation, "relation_type", ""),
            "reason": getattr(relation, "reason", ""),
        }

    def _is_root_key_mode(self) -> bool:
        return (
            self.auth_mode == "trusted"
            and self.api_key_type == "root"
            and not self._has_request_connection()
        )

    def _is_user_key_mode(self) -> bool:
        return (
            self.auth_mode == "api_key"
            and self.api_key_type == "user"
            and not self._has_request_connection()
        )

    def _is_dev_mode(self) -> bool:
        return self.auth_mode == "dev" and not self._has_request_connection()

    def _has_request_connection(self) -> bool:
        return self._request_connection is not None

    def should_sender_fanout(self) -> bool:
        return self._is_root_key_mode()

    def _effective_user_id(self, user_id: Optional[str]) -> str:
        if self._has_request_connection():
            return ""
        if self._is_user_key_mode():
            return ""
        return user_id or self.admin_user_id

    def _effective_session_user_id(self, user_id: Optional[str] = None) -> Optional[str]:
        if self._has_request_connection() or self._is_dev_mode() or self._is_user_key_mode():
            return None
        return user_id or self.admin_user_id

    def session_owner_user_id(self) -> Optional[str]:
        """Return the explicit OpenViking user for session operations, when needed."""
        return self._effective_session_user_id()

    @staticmethod
    def default_memory_policy() -> Dict[str, Dict[str, bool]]:
        """Write extracted conversation memories to peers by default for bot sessions."""
        return {
            "self": {"enabled": False},
            "peer": {"enabled": True},
        }

    @staticmethod
    def _peer_id(value: Optional[str]) -> Optional[str]:
        return _peer_id_from_external_id(str(value)) if value is not None else None

    def _apply_actor_peer_scope(self, client_kwargs: Dict[str, Any]) -> None:
        if self.actor_peer_id:
            client_kwargs["actor_peer_id"] = self.actor_peer_id

    async def _load_namespace_policy(self) -> None:
        if self._namespace_policy_loaded:
            return

        policy = {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        }
        if self._has_request_connection() or self._is_dev_mode() or self._is_user_key_mode():
            self._namespace_policy = policy
            self._namespace_policy_loaded = True
            return

        if self._is_root_key_mode() and self.account_id:
            try:
                accounts = await self.client.admin_list_accounts()
                for account in accounts or []:
                    if account.get("account_id") == self.account_id:
                        policy = {
                            "isolate_user_scope_by_agent": bool(
                                account.get("isolate_user_scope_by_agent", False)
                            ),
                            "isolate_agent_scope_by_user": bool(
                                account.get("isolate_agent_scope_by_user", False)
                            ),
                        }
                        break
            except Exception as e:
                logger.warning(
                    f"Failed to load account namespace policy for {self.account_id}: {e}"
                )

        self._namespace_policy = policy
        self._namespace_policy_loaded = True

    def _user_space_fragment(self, user_id: Optional[str]) -> str:
        effective_user_id = self._effective_user_id(user_id)
        if not effective_user_id:
            return ""
        if self._namespace_policy["isolate_user_scope_by_agent"] and self.agent_id:
            return f"{effective_user_id}/agent/{self.agent_id}"
        return effective_user_id

    def _memory_target_uri(self, user_id: Optional[str]) -> str:
        user_space = self._user_space_fragment(user_id)
        if user_space:
            return f"viking://user/{user_space}/memories/"
        return "viking://user/memories/"

    def _owner_user_id_for_uri(self, uri: Optional[str]) -> Optional[str]:
        if not self._is_root_key_mode():
            return None
        try:
            parts = uri_parts(str(uri or ""))
        except Exception:
            return None
        if len(parts) < 2 or parts[0] != "user":
            return None
        if parts[1] in {"memories", "resources", "skills", "peers", "privacy", "sessions"}:
            return None
        owner_user_id = parts[1]
        if owner_user_id == self.admin_user_id:
            return None
        return owner_user_id

    def _peer_memory_target_uri(self, user_id: Optional[str], peer_id: str) -> str:
        user_space = self._user_space_fragment(user_id)
        normalized_peer_id = self._peer_id(peer_id)
        if not normalized_peer_id:
            raise ValueError("peer_id is required for peer memory target")
        if not user_space:
            raise ValueError("peer memory target requires explicit user_id")
        return f"viking://user/{user_space}/peers/{normalized_peer_id}/memories/"

    def _current_user_space_fragment(self) -> str:
        current_user_id = getattr(self, "admin_user_id", None) or getattr(self, "user_id", None)
        if not current_user_id:
            return ""
        return current_user_id

    def _current_peer_memory_target_uri(self, peer_id: str) -> str:
        normalized_peer_id = self._peer_id(peer_id)
        if not normalized_peer_id:
            raise ValueError("peer_id is required for peer memory target")
        if self._is_user_key_mode() or self._has_request_connection():
            return f"viking://user/peers/{normalized_peer_id}/memories/"
        user_space = self._current_user_space_fragment()
        if not user_space:
            raise ValueError("peer memory target requires current user_id")
        return f"viking://user/{user_space}/peers/{normalized_peer_id}/memories/"

    def _current_peer_profile_uri(self, peer_id: str) -> str:
        return f"{self._current_peer_memory_target_uri(peer_id).rstrip('/')}/profile.md"

    def build_current_memory_target_uris(
        self,
        *,
        peer_ids: Optional[List[str]] = None,
        include_self: bool = True,
    ) -> List[str]:
        uris: List[str] = []
        if include_self:
            uris.append(self._memory_target_uri(None))

        normalized_peer_ids = self._dedupe_strings(
            [
                pid
                for pid in (self._peer_id(peer_id) for peer_id in (peer_ids or []))
                if pid
            ]
        )
        for peer_id in normalized_peer_ids:
            try:
                uris.append(self._current_peer_memory_target_uri(peer_id))
            except ValueError as exc:
                logger.warning(f"Skip invalid current peer memory target peer_id={peer_id}: {exc}")

        return self._dedupe_strings(uris)

    @staticmethod
    def _dedupe_strings(values: List[str]) -> List[str]:
        deduped: List[str] = []
        seen: set[str] = set()
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            deduped.append(value)
        return deduped

    def build_memory_search_target_uris(
        self,
        *,
        user_ids: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
        peer_ids: Optional[List[str]] = None,
    ) -> List[str]:
        target_uris: List[str] = []
        normalized_user_ids = self._dedupe_strings(
            [str(user_id).strip() for user_id in (user_ids or []) if str(user_id).strip()]
        )
        normalized_peer_ids = self._dedupe_strings(
            [
                pid
                for pid in (self._peer_id(peer_id) for peer_id in (peer_ids or []))
                if pid
            ]
        )
        effective_owner_user_id = self._effective_user_id(owner_user_id) if owner_user_id else None

        for user_id in normalized_user_ids:
            target_uris.append(self._memory_target_uri(user_id))

        if effective_owner_user_id and (not target_uris or normalized_peer_ids):
            target_uris.append(self._memory_target_uri(effective_owner_user_id))

        if effective_owner_user_id:
            for peer_id in normalized_peer_ids:
                try:
                    target_uris.append(
                        self._peer_memory_target_uri(effective_owner_user_id, peer_id)
                    )
                except ValueError as exc:
                    logger.warning(
                        f"Skip invalid peer memory target owner_user_id={effective_owner_user_id}, peer_id={peer_id}: {exc}"
                    )
        elif normalized_peer_ids:
            for peer_id in normalized_peer_ids:
                try:
                    target_uris.append(self._current_peer_memory_target_uri(peer_id))
                except ValueError as exc:
                    logger.warning(f"Skip invalid current peer memory target peer_id={peer_id}: {exc}")

        if not target_uris:
            target_uris.append(self._memory_target_uri(None))

        deduped: List[str] = []
        seen: set[str] = set()
        for target_uri in target_uris:
            target_uri = str(target_uri or "")
            if not target_uri or target_uri in seen:
                continue
            seen.add(target_uri)
            deduped.append(target_uri)
        return deduped

    def build_memory_search_targets(
        self,
        *,
        user_ids: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
        peer_ids: Optional[List[str]] = None,
    ) -> List[tuple[str, Optional[str]]]:
        target_uris = self.build_memory_search_target_uris(
            user_ids=user_ids,
            owner_user_id=owner_user_id,
            peer_ids=peer_ids,
        )
        return [
            (target_uri, self._owner_user_id_for_uri(target_uri))
            for target_uri in target_uris
        ]

    def _skill_memory_uri(self, skill_name: str, user_id: Optional[str] = None) -> str:
        return f"{self._memory_target_uri(user_id)}skills/{skill_name}.md"

    async def find(
        self,
        query: str,
        target_uri: Optional[str] = None,
        context_type: Optional[str | list[str]] = None,
        filter: Optional[Dict[str, Any]] = None,
        limit: int = 10,
    ):
        """搜索资源"""
        kwargs: Dict[str, Any] = {"limit": limit}
        if context_type is not None:
            kwargs["context_type"] = context_type
        if filter is not None:
            kwargs["filter"] = filter
        if target_uri:
            return await self.client.find(query, target_uri=target_uri, **kwargs)
        return await self.client.find(query, **kwargs)

    async def add_resource(self, local_path: str, desc: str) -> Optional[Dict[str, Any]]:
        """添加资源到 Viking"""
        result = await self.client.add_resource(path=local_path, reason=desc)
        return result

    async def list_resources(
        self, path: Optional[str] = None, recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """列出资源"""
        if path is None or path == "":
            path = viking_resource_prefix
        entries = await self.client.ls(path, recursive=recursive)
        return entries

    async def read_content(
        self,
        uri: str,
        level: str = "abstract",
        user_id: Optional[str] = None,
    ) -> str:
        """读取内容

        Args:
            uri: Viking URI
            level: 读取级别 ("abstract" - L0摘要, "overview" - L1概览, "read" - L2完整内容)
        """
        client = self.client
        should_close = False
        scoped_user_id = user_id or self._owner_user_id_for_uri(uri)
        if scoped_user_id:
            client, should_close = await self._get_user_scoped_client(scoped_user_id)

        try:
            if level == "abstract":
                return await client.abstract(uri)
            elif level == "overview":
                return await client.overview(uri)
            elif level == "read":
                return await client.read(uri)
            else:
                raise ValueError(f"Unsupported level: {level}")
        except FileNotFoundError:
            return ""
        except Exception as e:
            logger.warning(f"Failed to read content from {uri}: {e}")
            return ""
        finally:
            if should_close:
                await client.close()

    async def read_user_profile(self, user_id: str) -> str:
        """读取用户 profile。"""
        effective_user_id = self._effective_user_id(user_id)
        if not effective_user_id:
            return await self.read_content(uri="viking://user/memories/profile.md", level="read")

        uri = f"{self._memory_target_uri(effective_user_id)}profile.md"
        result = await self.read_content(uri=uri, level="read", user_id=effective_user_id)
        return result

    async def read_peer_profile(self, peer_id: str) -> str:
        """读取当前 User 下指定 peer 的 profile。"""
        try:
            uri = self._current_peer_profile_uri(peer_id)
        except ValueError:
            return ""
        return await self.read_content(uri=uri, level="read")

    async def search(
        self,
        query: str,
        target_uri: str | list[str] | None = None,
        limit: int = 10,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self.client
        should_close = False
        scoped_user_id = user_id
        if scoped_user_id is None and isinstance(target_uri, str):
            scoped_user_id = self._owner_user_id_for_uri(target_uri)
        if scoped_user_id:
            client, should_close = await self._get_user_scoped_client(scoped_user_id)

        try:
            result = await client.search(
                query,
                target_uri=target_uri,
                limit=limit,
            )
        finally:
            if should_close:
                await client.close()

        # 将 FindResult 对象转换为 JSON map
        return {
            "memories": [self._matched_context_to_dict(m) for m in result.memories]
            if hasattr(result, "memories")
            else [],
            "resources": [self._matched_context_to_dict(r) for r in result.resources]
            if hasattr(result, "resources")
            else [],
            "skills": [self._matched_context_to_dict(s) for s in result.skills]
            if hasattr(result, "skills")
            else [],
            "total": getattr(result, "total", len(getattr(result, "resources", []))),
            "query": query,
            "target_uri": target_uri,
        }

    async def search_user_memory(self, query: str, user_id: str) -> list[Any]:
        effective_user_id = self._effective_user_id(user_id)
        uri_user_memory = self._memory_target_uri(effective_user_id)
        result = await self.search(query, target_uri=uri_user_memory, user_id=effective_user_id)
        memories = result.get("memories") if isinstance(result, dict) else None
        return memories if isinstance(memories, list) else []

    async def _get_user_scoped_client(self, user_id: Optional[str]) -> tuple[Any, bool]:
        effective_user_id = self._effective_user_id(user_id)
        if not effective_user_id:
            return self.client, False

        if self._is_root_key_mode():
            client = await self._create_trusted_user_client(effective_user_id)
            return client, True

        return self.client, False

    async def _create_trusted_user_client(self, user_id: str):
        client_kwargs = {
            "url": self.openviking_config.server_url,
            "api_key": self.openviking_config.api_key,
            "account": self.account_id,
            "user": user_id,
            "profile_enabled": False,
        }
        self._apply_actor_peer_scope(client_kwargs)
        client = ov.AsyncHTTPClient(**client_kwargs)
        await client.initialize()
        return client

    async def search_memory(
        self,
        query: str,
        user_ids: str | list[str] | None = None,
        limit: int = 10,
        agent_user_id: Optional[str] = None,
        *,
        owner_user_id: Optional[str] = None,
        peer_ids: Optional[list[str]] = None,
    ) -> list[Any] | dict[str, list[Any]]:
        """通过上下文消息检索用户 memory。"""

        def _extract_memories(result: Any) -> list[Any]:
            if not result:
                return []
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                memories = result.get("memories")
                return memories if isinstance(memories, list) else []
            memories = getattr(result, "memories", None)
            return memories if isinstance(memories, list) else []

        if user_ids is None:
            normalized_user_ids: list[str] = []
        elif isinstance(user_ids, str):
            normalized_user_ids = [user_ids]
        else:
            normalized_user_ids = list(user_ids)
        normalized_user_ids = [
            str(user_id).strip() for user_id in normalized_user_ids if str(user_id).strip()
        ]

        normalized_peer_ids = self._dedupe_strings(
            [
                pid
                for pid in (self._peer_id(peer_value) for peer_value in (peer_ids or []))
                if pid
            ]
        )
        effective_owner_user_id = self._effective_user_id(owner_user_id) if owner_user_id else None

        search_targets = self.build_memory_search_targets(
            user_ids=normalized_user_ids,
            owner_user_id=effective_owner_user_id,
            peer_ids=normalized_peer_ids,
        )
        if not search_targets:
            return []

        all_user_memories = []
        for target_uri, scoped_user_id in search_targets:
            find_kwargs: Dict[str, Any] = {
                "query": query,
                "target_uri": target_uri,
                "limit": limit,
            }
            client = self.client
            should_close = False
            if scoped_user_id:
                client, should_close = await self._get_user_scoped_client(scoped_user_id)
            try:
                user_memory = await client.find(**find_kwargs)
                all_user_memories.extend(_extract_memories(user_memory))
            finally:
                if should_close:
                    await client.close()

        if agent_user_id is not None:
            agent_uri = f"viking://agent/{self.agent_id or self.admin_user_id}/memories/"
            agent_memory = await self.client.find(
                query=query,
                target_uri=agent_uri,
                limit=limit,
            )
            return {
                "user_memory": all_user_memories,
                "agent_memory": _extract_memories(agent_memory),
            }

        return all_user_memories

    async def search_experiences(self, query: str, limit: int = 5) -> list[Any]:
        """用 query 检索 vikingbot experience 记忆。"""
        exp_uri = f"{self._memory_target_uri(self.admin_user_id)}experiences/"
        result = await self.search(query=query, target_uri=exp_uri, limit=limit)
        return result.get("memories", [])

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = 10,
        exclude_uri: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """通过模式（正则表达式）搜索内容"""
        client = self.client
        should_close = False
        if user_id:
            client, should_close = await self._get_user_scoped_client(user_id)

        try:
            return await client.grep(
                uri,
                pattern,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                exclude_uri=exclude_uri,
            )
        finally:
            if should_close:
                await client.close()

    async def glob(
        self, pattern: str, uri: Optional[str] = None, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """通过 glob 模式匹配文件"""
        client = self.client
        should_close = False
        if user_id:
            client, should_close = await self._get_user_scoped_client(user_id)

        try:
            return await client.glob(pattern, uri=uri)
        finally:
            if should_close:
                await client.close()

    def _session_client(self, user_id: Optional[str] = None):
        if user_id and user_id == self.admin_user_id and self.admin_user_client:
            return self.admin_user_client
        return self.admin_user_client or self.client

    async def _session_client_for_user(self, user_id: Optional[str] = None):
        if self._has_request_connection():
            return self.client
        if not user_id or self._is_dev_mode() or self._is_user_key_mode():
            return self._session_client(user_id)
        if user_id == self.admin_user_id and self.admin_user_client:
            return self.admin_user_client

        client = self._user_clients.get(user_id)
        if client is None and self._is_root_key_mode():
            client = await self._create_trusted_user_client(user_id)
            self._user_clients[user_id] = client
        return client or self._session_client(user_id)

    def _assistant_peer_id(self) -> Optional[str]:
        return None

    def _normalize_session_messages(
        self,
        messages: list[dict[str, Any]],
        default_user_peer_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        assistant_peer_id = self._assistant_peer_id()

        for message in messages:
            role = str(message.get("role") or "").strip().lower()
            if role not in {"user", "assistant", "system", "tool"}:
                continue

            content = self._session_message_content(message)
            tools_used = message.get("tools_used") or []
            parts: list[dict[str, Any]] = []

            if content:
                parts.append({"type": "text", "text": content})

            if isinstance(tools_used, list):
                for tool_info in tools_used:
                    if not isinstance(tool_info, dict):
                        continue
                    tool_name = str(tool_info.get("tool_name") or "").strip()
                    if not tool_name:
                        continue

                    raw_args = tool_info.get("args", {})
                    if isinstance(raw_args, dict):
                        tool_input = raw_args
                    else:
                        try:
                            tool_input = json.loads(raw_args) if raw_args else {}
                        except Exception:
                            tool_input = {"raw_args": str(raw_args)}

                    result_str = str(tool_info.get("result", ""))
                    skill_uri = ""
                    if tool_name == "read_file" and result_str:
                        match = re.search(
                            r"^---\s*\nname:\s*(.+?)\s*\n",
                            result_str,
                            re.MULTILINE,
                        )
                        if match:
                            skill_name = match.group(1).strip()
                            skill_uri = self._skill_memory_uri(skill_name)

                    tool_id = f"{tool_name}_{uuid.uuid4().hex[:8]}"
                    parts.append(
                        {
                            "type": "tool",
                            "tool_id": tool_id,
                            "tool_name": tool_name,
                            "tool_uri": f"viking://session/{session_id}/tools/{tool_id}"
                            if session_id
                            else "",
                            "tool_input": tool_input,
                            "tool_output": result_str[:2000],
                            "tool_status": "completed"
                            if tool_info.get("execute_success", True)
                            else "error",
                            "skill_uri": skill_uri,
                            "duration_ms": float(tool_info.get("duration", 0.0) or 0.0),
                            "prompt_tokens": tool_info.get("input_token"),
                            "completion_tokens": tool_info.get("output_token"),
                        }
                    )

            if not parts:
                continue

            ov_role = "user" if role == "user" else "assistant"
            payload = {
                "role": ov_role,
                "content": content,
                "parts": parts,
                "created_at": message.get("created_at") or message.get("timestamp"),
            }

            peer_id = message.get("peer_id")
            if not peer_id and ov_role == "user":
                peer_id = message.get("sender_id") or default_user_peer_id
            elif not peer_id and ov_role == "assistant":
                peer_id = assistant_peer_id

            safe_message_peer_id = self._peer_id(peer_id)
            if safe_message_peer_id:
                payload["peer_id"] = safe_message_peer_id

            normalized.append(payload)

        return normalized

    def _session_message_content(self, message: dict[str, Any]) -> str:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return ""

    async def ensure_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        client = await self._session_client_for_user(user_id)
        try:
            return await client.get_session(session_id)
        except Exception as exc:
            if not self._is_not_found_error(exc):
                raise

        return await client.create_session(
            session_id=session_id,
            memory_policy=memory_policy,
        )

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if code == "NOT_FOUND":
            return True
        if exc.__class__.__name__ == "NotFoundError":
            return True
        return "not found" in str(exc).lower()

    async def get_session(self, session_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        await self.ensure_session(session_id, user_id=user_id)
        client = await self._session_client_for_user(user_id)
        return await client.get_session(session_id)

    async def get_session_context(
        self, session_id: str, token_budget: int, user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        await self.ensure_session(session_id, user_id=user_id)
        client = await self._session_client_for_user(user_id)
        return await client.get_session_context(
            session_id,
            token_budget=token_budget,
        )

    async def append_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        default_user_peer_id: Optional[str] = None,
        session_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.ensure_session(session_id, user_id=session_user_id)
        batch = self._normalize_session_messages(
            messages,
            default_user_peer_id=default_user_peer_id,
            session_id=session_id,
        )
        if not batch:
            return {"session_id": session_id, "added": 0, "message_count": 0}
        client = await self._session_client_for_user(session_user_id)
        total_added = 0
        message_count = 0
        for start in range(0, len(batch), 100):
            result = await client.batch_add_messages(
                session_id=session_id,
                messages=batch[start : start + 100],
            )
            total_added += int(result.get("added", 0) or 0)
            message_count = int(result.get("message_count", message_count) or 0)
        return {"session_id": session_id, "added": total_added, "message_count": message_count}

    async def commit_session(
        self,
        session_id: str,
        keep_recent_count: int = 0,
        user_id: Optional[str] = None,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_session(
            session_id,
            user_id=user_id,
            memory_policy=memory_policy
            if memory_policy is not None
            else self.default_memory_policy(),
        )
        client = await self._session_client_for_user(user_id)
        return await client.commit_session(
            session_id,
            keep_recent_count=keep_recent_count,
        )

    async def commit(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        user_id: str = None,
        keep_recent_count: int = 0,
        peer_id: Optional[str] = None,
        memory_policy: Optional[Dict[str, Any]] = None,
    ):
        """Append messages to a stable session and commit it."""
        session_user_id = self._effective_session_user_id(user_id)
        session_memory_policy = (
            memory_policy if memory_policy is not None else self.default_memory_policy()
        )
        await self.ensure_session(
            session_id,
            user_id=session_user_id,
            memory_policy=session_memory_policy,
        )
        appended = await self.append_messages(
            session_id,
            messages,
            default_user_peer_id=self._peer_id(peer_id),
            session_user_id=session_user_id,
        )
        commit_result = await self.commit_session(
            session_id,
            keep_recent_count=keep_recent_count,
            user_id=session_user_id,
            memory_policy=session_memory_policy,
        )
        logger.debug(
            f"Committed OpenViking session {session_id}, "
            f"api_key_type={self.api_key_type}, appended={appended.get('added', 0)}"
        )
        return {
            "success": True,
            "session_id": session_id,
            "append": appended,
            "commit": commit_result,
        }

    async def close(self):
        """关闭客户端"""
        await self.client.close()
        if self.admin_user_client:
            await self.admin_user_client.close()
        for client in self._user_clients.values():
            await client.close()


async def main_test():
    client = await VikingClient.create()
    # res = client.list_resources()
    # res = await client.search("头有点疼", target_uri="viking://user/memories/")
    # res = await client.get_viking_memory_context("123", current_message="头疼", history=[])
    res = await client.search_memory("你好", "user_1")
    # res = await client.list_resources("viking://resources/")
    # res = await client.read_content("viking://user/memories/profile.md", level="read")
    # res = await client.add_resource("https://github.com/volcengine/OpenViking", "ov代码")
    # res = await client.grep("viking://resources/", "viking", True)
    # res = await client.commit(
    #     session_id="99999",
    #     messages=[{"role": "user", "content": "你好"}],
    #     user_id="1010101010",
    # )
    # res = await client.commit("1234", [{"role": "user", "content": "帮我搜索 Python asyncio 教程"}
    #                                    ,{"role": "assistant", "content": "我来帮你r搜索 Python asyncio 相关的教程。"}])
    print(res)

    await client.close()
    print("处理完成！")


async def account_test():
    client = ov.AsyncHTTPClient(
        url="http://localhost:1933",
        api_key="",
    )
    await client.initialize()

    res = await client.search("123")

    print(res)

if __name__ == "__main__":
    asyncio.run(main_test())
    # asyncio.run(account_test())
