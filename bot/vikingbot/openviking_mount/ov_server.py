import asyncio
import hashlib
import time
from typing import Any, Dict, List, Optional

from loguru import logger

import openviking as ov
from vikingbot.config.loader import load_config
from vikingbot.openviking_mount.user_apikey_manager import UserApiKeyManager

viking_resource_prefix = "viking://resources/"


class VikingClient:
    def __init__(self, agent_id: Optional[str] = None):
        config = load_config()
        openviking_config = config.ov_server
        self.openviking_config = openviking_config
        self.ov_path = config.ov_data_path
        self.mode = openviking_config.mode
        self.api_key_type = (openviking_config.api_key_type or "root").strip().lower()
        if self.api_key_type not in {"root", "user"}:
            raise ValueError(f"Invalid ov_server.api_key_type: {self.api_key_type}")

        self._apikey_manager = None
        self.admin_user_client = None
        self._namespace_policy = {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        }
        self._namespace_policy_loaded = False

        if openviking_config.mode == "local":
            self.client = ov.AsyncHTTPClient(url=openviking_config.server_url)
            self.agent_id = "default"
            self.account_id = "default"
            self.user_id = "default"
            self.admin_user_id = "default"
            return

        if agent_id and "#" in agent_id:
            agent_id = agent_id.split("#", 1)[0]

        self.agent_id = agent_id
        self.account_id = openviking_config.account_id
        self.admin_user_id = openviking_config.admin_user_id

        remote_client_kwargs = {
            "url": openviking_config.server_url,
            "api_key": openviking_config.root_api_key,
            "agent_id": agent_id,
        }
        if self._is_root_key_mode():
            remote_client_kwargs["account"] = openviking_config.account_id
            remote_client_kwargs["user"] = openviking_config.admin_user_id

        self.client = ov.AsyncHTTPClient(**remote_client_kwargs)

        if self._is_root_key_mode() and self.ov_path:
            self._apikey_manager = UserApiKeyManager(
                ov_path=self.ov_path,
                server_url=openviking_config.server_url,
                account_id=openviking_config.account_id,
            )

    async def _initialize(self):
        """Initialize the client (must be called after construction)"""
        await self.client.initialize()
        await self._load_namespace_policy()

        if self._is_root_key_mode() and self.admin_user_id:
            user_exists = await self._check_user_exists(self.admin_user_id)
            if not user_exists:
                await self._initialize_user(self.admin_user_id, role="admin")
            admin_user_api_key = await self._get_or_create_user_apikey(self.admin_user_id)
            if admin_user_api_key:
                self.admin_user_client = ov.AsyncHTTPClient(
                    url=self.openviking_config.server_url,
                    api_key=admin_user_api_key,
                    agent_id=self.agent_id,
                    account=self.account_id,
                    user=self.admin_user_id,
                )
                await self.admin_user_client.initialize()

    @classmethod
    async def create(cls, agent_id: Optional[str] = None):
        """Factory method to create and initialize a VikingClient instance.

        Args:
            agent_id: The agent ID to use
        """
        instance = cls(agent_id)
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

    def get_agent_space_name(self, user_id: str) -> str:
        return hashlib.md5(f"{user_id}:{self.agent_id}".encode()).hexdigest()[:12]

    def _is_root_key_mode(self) -> bool:
        return self.mode == "remote" and self.api_key_type == "root"

    def _is_user_key_mode(self) -> bool:
        return self.mode == "remote" and self.api_key_type == "user"

    def _effective_user_id(self, user_id: Optional[str]) -> str:
        if self._is_user_key_mode():
            return ""
        return user_id or self.admin_user_id

    async def _load_namespace_policy(self) -> None:
        if self._namespace_policy_loaded:
            return

        policy = {
            "isolate_user_scope_by_agent": False,
            "isolate_agent_scope_by_user": False,
        }

        if self.mode == "remote" and self.account_id:
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

    def _agent_space_fragment(self, user_id: Optional[str]) -> str:
        if not self.agent_id:
            return ""
        effective_user_id = self._effective_user_id(user_id)
        if self._namespace_policy["isolate_agent_scope_by_user"] and effective_user_id:
            return f"{self.agent_id}/user/{effective_user_id}"
        return self.agent_id

    def _memory_target_uri(self, user_id: Optional[str]) -> str:
        user_space = self._user_space_fragment(user_id)
        if user_space:
            return f"viking://user/{user_space}/memories/"
        return "viking://user/memories/"

    def _agent_memory_target_uri(self, user_id: Optional[str]) -> str:
        agent_space = self._agent_space_fragment(user_id)
        if agent_space:
            return f"viking://agent/{agent_space}/memories/"
        return "viking://agent/memories/"

    def _skill_memory_uri(self, skill_name: str, user_id: Optional[str] = None) -> str:
        return f"{self._agent_memory_target_uri(user_id)}skills/{skill_name}.md"

    def should_sender_fanout(self) -> bool:
        return self._is_root_key_mode()

    async def find(self, query: str, target_uri: Optional[str] = None):
        """搜索资源"""
        if target_uri:
            return await self.client.find(query, target_uri=target_uri)
        return await self.client.find(query)

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

    async def read_content(self, uri: str, level: str = "abstract") -> str:
        """读取内容

        Args:
            uri: Viking URI
            level: 读取级别 ("abstract" - L0摘要, "overview" - L1概览, "read" - L2完整内容)
        """
        try:
            if level == "abstract":
                return await self.client.abstract(uri)
            elif level == "overview":
                return await self.client.overview(uri)
            elif level == "read":
                return await self.client.read(uri)
            else:
                raise ValueError(f"Unsupported level: {level}")
        except FileNotFoundError:
            return ""
        except Exception as e:
            logger.warning(f"Failed to read content from {uri}: {e}")
            return ""

    async def read_user_profile(self, user_id: str) -> str:
        """读取用户 profile。"""
        await self._load_namespace_policy()
        effective_user_id = self._effective_user_id(user_id)
        user_exists = await self._check_user_exists(effective_user_id)

        if not user_exists:
            await self._initialize_user(effective_user_id)
            return ""

        uri = f"{self._memory_target_uri(effective_user_id)}profile.md"
        result = await self.read_content(uri=uri, level="read")
        return result

    async def search(
        self, query: str, target_uri: str | list[str] | None = None, limit: int = 10
    ) -> Dict[str, Any]:
        # session = self.client.session()

        result = await self.client.search(query, target_uri=target_uri, limit=limit)

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
        await self._load_namespace_policy()
        effective_user_id = self._effective_user_id(user_id)
        user_exists = await self._check_user_exists(effective_user_id)
        if not user_exists:
            return []
        uri_user_memory = self._memory_target_uri(effective_user_id)
        result = await self.client.search(query, target_uri=uri_user_memory)
        return (
            [self._matched_context_to_dict(m) for m in result.memories]
            if hasattr(result, "memories")
            else []
        )

    async def _check_user_exists(self, user_id: str) -> bool:
        """检查用户是否存在于账户中。"""
        if self.mode == "local" or self._is_user_key_mode():
            return True
        if not user_id:
            return False
        try:
            res = await self.client.admin_list_users(self.account_id)
            if not res or len(res) == 0:
                return False
            return any(user.get("user_id") == user_id for user in res)
        except Exception as e:
            logger.warning(f"Failed to check user existence: {e}")
            return False

    async def _initialize_user(self, user_id: str, role: str = "user") -> bool:
        """初始化用户。"""
        if self.mode == "local" or self._is_user_key_mode():
            return True
        if not user_id:
            return False
        try:
            result = await self.client.admin_register_user(
                account_id=self.account_id, user_id=user_id, role=role
            )

            if self._apikey_manager and isinstance(result, dict):
                api_key = result.get("user_key")
                if api_key:
                    self._apikey_manager.set_apikey(user_id, api_key)

            return True
        except Exception as e:
            if "User already exists" in str(e):
                return True
            logger.warning(f"Failed to initialize user {user_id}: {e}")
            return False

    async def _get_or_create_user_apikey(self, user_id: str) -> Optional[str]:
        """获取或创建用户的 API key。"""
        if self._is_user_key_mode() or not self._apikey_manager or not user_id:
            return None

        # Step 1: Check local storage first
        api_key = self._apikey_manager.get_apikey(user_id)
        if api_key:
            return api_key

        try:
            # 2a. Remove user if exists
            user_exists = await self._check_user_exists(user_id)
            if user_exists:
                await self.client.admin_remove_user(self.account_id, user_id)
            # 2b. Recreate user - this will save API key in _initialize_user
            success = await self._initialize_user(user_id)
            if not success:
                logger.warning(f"Failed to recreate user {user_id}")
                return None

            # 2c. Get API key from local storage (it was saved by _initialize_user)
            api_key = self._apikey_manager.get_apikey(user_id)
            if api_key:
                return api_key
            else:
                return None

        except Exception as e:
            logger.error(f"Error getting or creating API key for user {user_id}: {e}")
            return None

    async def search_memory(
        self, query: str, user_ids: str | list[str], agent_user_id: str, limit: int = 10
    ) -> dict[str, list[Any]]:
        """通过上下文消息，检索viking 的user、Agent memory。"""
        await self._load_namespace_policy()

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

        if isinstance(user_ids, str):
            user_ids = [user_ids]

        all_user_memories = []
        all_agent_memories = []

        for user_id in user_ids:
            effective_user_id = self._effective_user_id(user_id)
            user_exists = await self._check_user_exists(effective_user_id)
            if not user_exists:
                await self._initialize_user(effective_user_id)
                continue

            uri_user_memory = self._memory_target_uri(effective_user_id)
            user_memory = await self.client.find(
                query=query,
                target_uri=uri_user_memory,
                limit=limit,
            )
            all_user_memories.extend(_extract_memories(user_memory))

        effective_agent_user_id = self._effective_user_id(agent_user_id)
        uri_agent_memory = self._agent_memory_target_uri(effective_agent_user_id)
        agent_memory = await self.client.find(
            query=query,
            target_uri=uri_agent_memory,
            limit=limit,
        )
        all_agent_memories.extend(_extract_memories(agent_memory))

        return {
            "user_memory": all_user_memories,
            "agent_memory": all_agent_memories,
        }

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = 10,
        exclude_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """通过模式（正则表达式）搜索内容"""
        return await self.client.grep(
            uri,
            pattern,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            exclude_uri=exclude_uri,
        )

    async def glob(self, pattern: str, uri: Optional[str] = None) -> Dict[str, Any]:
        """通过 glob 模式匹配文件"""
        return await self.client.glob(pattern, uri=uri)

    async def commit(self, session_id: str, messages: list[dict[str, Any]], user_id: str = None):
        """提交会话"""
        import re
        import uuid

        from openviking.message.part import TextPart, ToolPart

        effective_user_id = self._effective_user_id(user_id)

        client = self.client
        start = time.time()
        if self._is_root_key_mode():
            user_exists = await self._check_user_exists(effective_user_id)
            if not user_exists:
                success = await self._initialize_user(effective_user_id)
                if not success:
                    return {"error": "Failed to initialize user"}

            if (
                effective_user_id
                and effective_user_id != self.admin_user_id
                and self._apikey_manager
            ):
                user_api_key = await self._get_or_create_user_apikey(effective_user_id)
                if user_api_key:
                    client = ov.AsyncHTTPClient(
                        url=self.openviking_config.server_url,
                        api_key=user_api_key,
                        agent_id=self.agent_id,
                    )
                    await client.initialize()

        create_res = await client.create_session()
        session_id = create_res["session_id"]
        session = client.session(session_id)

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            tools_used = message.get("tools_used") or []

            parts: list[Any] = []

            if content:
                parts.append(TextPart(text=content))

            for tool_info in tools_used:
                tool_name = tool_info.get("tool_name", "")
                if not tool_name:
                    continue

                tool_id = f"{tool_name}_{uuid.uuid4().hex[:8]}"
                tool_input = None
                try:
                    import json

                    args_str = tool_info.get("args", "{}")
                    tool_input = json.loads(args_str) if args_str else {}
                except Exception:
                    tool_input = {"raw_args": tool_info.get("args", "")}

                result_str = str(tool_info.get("result", ""))

                skill_uri = ""
                if tool_name == "read_file" and result_str:
                    match = re.search(r"^---\s*\nname:\s*(.+?)\s*\n", result_str, re.MULTILINE)
                    if match:
                        skill_name = match.group(1).strip()
                        skill_uri = f"viking://agent/skills/{skill_name}"

                execute_success = tool_info.get("execute_success", True)
                tool_status = "completed" if execute_success else "error"
                parts.append(
                    ToolPart(
                        tool_id=tool_id,
                        tool_name=tool_name,
                        tool_uri=f"viking://session/{session_id}/tools/{tool_id}",
                        tool_input=tool_input,
                        tool_output=result_str[:2000],
                        tool_status=tool_status,
                        skill_uri=skill_uri,
                        duration_ms=float(tool_info.get("duration", 0.0)),
                        prompt_tokens=tool_info.get("input_token"),
                        completion_tokens=tool_info.get("output_token"),
                    )
                )

            if not parts:
                continue
            # 获取消息的时间戳，如果没有则使用当前时间
            created_at = message.get("timestamp")
            await session.add_message(role=role, parts=parts, created_at=created_at)

        result = await session.commit_async()
        if client is not self.client:
            await client.close()
        logger.info(f"time spent: {time.time() - start}")
        logger.debug(
            f"Message add ed to OpenViking session {session_id}, api_key_type={self.api_key_type}, user: {effective_user_id}"
        )
        return {"success": result["status"]}

    async def close(self):
        """关闭客户端"""
        await self.client.close()


async def main_test():
    client = await VikingClient.create(agent_id="shared")
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
        agent_id="shared",
    )
    await client.initialize()

    # res = await client.admin_list_users("eval")
    # res = await client.admin_remove_user("default", "")
    # res = await client.admin_remove_user("default", "admin")
    # res = await client.admin_list_accounts()
    # res = await client.admin_create_account("eval", "default")
    res = await client.search("123")

    print(res)


if __name__ == "__main__":
    asyncio.run(main_test())
    # asyncio.run(account_test())
