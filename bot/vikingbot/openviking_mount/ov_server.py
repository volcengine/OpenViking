import asyncio
import hashlib
from typing import List, Dict, Any, Optional

from loguru import logger

import openviking as ov
from vikingbot.config.loader import get_data_dir
from vikingbot.config.loader import load_config

viking_resource_prefix = "viking://resources/"


class VikingClient:
    def __init__(self, agent_id: Optional[str] = None):
        config = load_config()
        openviking_config = config.ov_server
        if openviking_config.mode == "local":
            ov_data_path = get_data_dir() / "ov_data"
            ov_data_path.mkdir(parents=True, exist_ok=True)
            self.client = ov.AsyncOpenViking(path=str(ov_data_path))
            self.agent_id = "default"
            self.account_id = "default"
            self.admin_user_id = "default"
        else:
            self.client = ov.AsyncHTTPClient(
                url=openviking_config.server_url,
                api_key=openviking_config.root_api_key,
                agent_id=agent_id,
            )
            self.agent_id = agent_id
            self.account_id = openviking_config.account_id
            self.admin_user_id = openviking_config.admin_user_id
        self.mode = openviking_config.mode

    async def _initialize(self):
        """Initialize the client (must be called after construction)"""
        await self.client.initialize()

        # 检查并初始化 admin_user_id（如果配置了）
        if self.admin_user_id:
            user_exists = await self._check_user_exists(self.admin_user_id)
            if not user_exists:
                await self._initialize_user(self.admin_user_id, role="admin")


    @classmethod
    async def create(cls, agent_id: Optional[str] = None):
        """Factory method to create and initialize a VikingClient instance"""
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
        return hashlib.md5((user_id + self.agent_id).encode()).hexdigest()[:12]

    async def find(self, query: str, target_uri: Optional[str] = None):
        """搜索资源"""
        if target_uri:
            return await self.client.find(query, target_uri=target_uri)
        return await self.client.find(query)

    async def add_resource(
        self, local_path: str, desc: str, target_path: Optional[str] = None, wait: bool = False
    ) -> Optional[Dict[str, Any]]:
        """添加资源到 Viking"""
        result = await self.client.add_resource(path=local_path, reason=desc, wait=wait)
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
        """读取用户 profile。

        首先检查用户是否存在，如不存在则初始化用户并返回空字符串。
        用户存在时，再查询 profile 信息。

        Args:
            user_id: 用户ID

        Returns:
            str: 用户 profile 内容，如果用户不存在或查询失败返回空字符串
        """
        # Step 1: 检查用户是否存在
        user_exists = await self._check_user_exists(user_id)

        # Step 2: 如果用户不存在，初始化用户并直接返回
        if not user_exists:
            success = await self._initialize_user(user_id)
            if success:
                logger.debug(f"User {user_id} initialized, returning empty profile")
            return ""

        # Step 3: 用户存在，查询 profile
        uri = f"viking://user/{user_id}/memories/profile.md"
        result = await self.read_content(uri=uri, level="read")
        return result

    async def search(self, query: str, target_uri: Optional[str] = "") -> Dict[str, Any]:
        # session = self.client.session()

        result = await self.client.search(query, target_uri=target_uri)

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
        user_exists = await self._check_user_exists(user_id)
        if not user_exists:
            return []
        uri_user_memory = f"viking://user/{user_id}/memories/"
        result = await self.client.search(query, target_uri=uri_user_memory)
        return (
            [self._matched_context_to_dict(m) for m in result.memories]
            if hasattr(result, "memories")
            else []
        )

    async def _check_user_exists(self, user_id: str) -> bool:
        """检查用户是否存在于账户中。

        Args:
            user_id: 用户ID

        Returns:
            bool: 用户是否存在
        """
        try:
            res = await self.client.admin_list_users(self.account_id)
            if not res or len(res) == 0:
                return False
            return any(user.get("user_id") == user_id for user in res)
        except Exception as e:
            logger.warning(f"Failed to check user existence: {e}")
            return False

    async def _initialize_user(self, user_id: str, role: str = "user") -> bool:
        """初始化用户。

        Args:
            user_id: 用户ID

        Returns:
            bool: 初始化是否成功
        """
        try:
            await self.client.admin_register_user(account_id=self.account_id, user_id=user_id, role=role)
            logger.debug(f"Initialized user {user_id} in account {self.account_id}")
            return True
        except Exception as e:
            if "User already exists" in str(e):
                return True
            logger.warning(f"Failed to initialize user {user_id}: {e}")
            return False

    async def search_memory(
        self, query: str, user_id: str, limit: int = 10
    ) -> dict[str, list[Any]]:
        """通过上下文消息，检索viking 的user、Agent memory。

        首先检查用户是否存在，如不存在则初始化用户并返回空结果。
        用户存在时，再进行记忆检索。
        """
        # Step 1: 检查用户是否存在
        user_exists = await self._check_user_exists(user_id)

        # Step 2: 如果用户不存在，初始化用户并直接返回
        if not user_exists:
            await self._initialize_user(user_id)
            return {
                "user_memory": [],
                "agent_memory": [],
            }
        # Step 3: 用户存在，查询记忆
        uri_user_memory = f"viking://user/{user_id}/memories/"
        user_memory = await self.client.find(
            query=query,
            target_uri=uri_user_memory,
            limit=limit,
        )
        agent_space_name = self.get_agent_space_name(user_id)
        uri_agent_memory = f"viking://agent/{agent_space_name}/memories/"
        agent_memory = await self.client.find(
            query=query,
            target_uri=uri_agent_memory,
            limit=limit,
        )
        return {
            "user_memory": user_memory.memories if hasattr(user_memory, "memories") else [],
            "agent_memory": agent_memory.memories if hasattr(agent_memory, "memories") else [],
        }

    async def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict[str, Any]:
        """通过模式（正则表达式）搜索内容"""
        return await self.client.grep(uri, pattern, case_insensitive=case_insensitive)

    async def glob(self, pattern: str, uri: Optional[str] = None) -> Dict[str, Any]:
        """通过 glob 模式匹配文件"""
        return await self.client.glob(pattern, uri=uri)

    async def commit(self, session_id: str, messages: list[dict[str, Any]], user_id: str = None) -> None:
        """提交会话"""
        import uuid
        import re
        from openviking.message.part import TextPart, ToolPart, Part

        session = self.client.session(session_id)

        if self.mode == "local":
            for message in messages:
                # logger.debug(f"message === {message}")
                role = message.get("role")
                content = message.get("content")
                tools_used = message.get("tools_used") or []

                parts: list[Part] = []

                if content:
                    parts.append(TextPart(text=content))

                for tool_info in tools_used:
                    tool_name = tool_info.get("tool_name", "")
                    # logger.debug(f"tool_name === {tool_name}")
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
                            # logger.debug(f"skill_uri === {skill_uri}")

                    execute_success = tool_info.get("execute_success", True)
                    tool_status = "completed" if execute_success else "error"
                    # logger.debug(f"tool_info={tool_info}")
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
                    parts = [TextPart(text=content or "")]

                session.add_message(role=role, parts=parts)

            result = session.commit()
        else:
            for message in messages:
                await session.add_message(role=message.get("role"), content=message.get("content"))
            result = await session.commit()
        logger.debug(f"Message add ed to OpenViking session {session_id}")
        return {"success": result["status"]}

    def close(self):
        """关闭客户端"""
        self.client.close()

    def _parse_viking_memory(self, result: Any) -> str:
        if result and len(result) > 0:
            user_memories = []
            for idx, memory in enumerate(result, start=1):
                user_memories.append(
                    f"{idx}. {getattr(memory, 'abstract', '')}; "
                    f"uri: {getattr(memory, 'uri', '')}; "
                    f"isDir: {getattr(memory, 'is_leaf', False)}; "
                    f"related score: {getattr(memory, 'score', 0.0)}"
                )
            return "\n".join(user_memories)
        return ""

    async def get_viking_memory_context(
        self, session_id: str, current_message: str, history: list[dict[str, Any]]
    ) -> str:
        result = await self.search_memory(current_message, limit=5)
        if not result:
            return ""
        user_memory = self._parse_viking_memory(result["user_memory"])
        agent_memory = self._parse_viking_memory(result["agent_memory"])
        return (
            f"## Related openviking memories.Using tools to read more details.\n"
            f"### user memories:\n{user_memory}\n"
            f"### agent memories:\n{agent_memory}"
        )


async def main_test():
    client = await VikingClient.create(agent_id="shared")
    # res = client.list_resources()
    # res = await client.search("头有点疼", target_uri="viking://user/memories/")
    # res = await client.get_viking_memory_context("123", current_message="头疼", history=[])
    res = await client.search_memory("你好", "user_1")
    # res = await client.list_resources("viking://resources/")
    # res = await client.read_content("viking://user/memories/profile.md", level="read")
    # res = await client.add_resource("/Users/bytedance/Documents/论文/吉比特年报.pdf", "吉比特年报")
    # res = await client.commit(
    #     "123",
    #     [
    #         {"role": "user", "content": "我叫吴彦祖"},
    #         {
    #             "role": "assistant",
    #             "content": "好的吴彦祖😎，我已经记 住你的名字啦，之后随时都可以认出你~",
    #         },
    #     ],
    # )
    # res = await client.commit("1234", [{"role": "user", "content": "帮我搜索 Python asyncio 教程"}
    #                                    ,{"role": "assistant", "content": "我来帮你r搜索 Python asyncio 相关的教程。"}])
    print(res)

    print("等待后台处理完成...")
    await client.client.wait_processed(timeout=60)
    print("处理完成！")


async def account_test():
    client = ov.AsyncHTTPClient(url="http://localhost:1933", api_key="test")
    await client.initialize()

    res = await client.admin_list_users("default")
    # res = await client.admin_remove_user("default", "admin")
    print(res)


if __name__ == "__main__":
    # asyncio.run(main_test())
    asyncio.run(account_test())
