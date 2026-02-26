import asyncio
import os
from typing import List, Dict, Any, Optional

import openviking as ov
from openviking.message.part import TextPart, ToolPart
import tos
from loguru import logger

from vikingbot.config.loader import get_data_dir
from vikingbot.config.loader import load_config

viking_resource_prefix = "viking://resources"
uri_user_memory = "viking://user/memories/"
uri_agent_memory = "viking://agent/memories/"


class VikingClient:
    def __init__(self, viking_path: str = "/"):
        config = load_config()
        openviking_config = config.openviking
        if openviking_config.mode == "local":
            ov_data_path = get_data_dir() / "ov_data"
            ov_data_path.mkdir(parents=True, exist_ok=True)
            self.client = ov.AsyncOpenViking(path=str(ov_data_path))
        else:
            self.client = ov.AsyncHTTPClient(url=openviking_config.server_url)
        self.tos_client = tos.TosClientV2(
            openviking_config.tos_ak,
            openviking_config.tos_sk,
            openviking_config.tos_endpoint,
            openviking_config.tos_region,
        )
        self.viking_path = viking_path
        self.mode = openviking_config.mode

    async def _initialize(self):
        """Initialize the client (must be called after construction)"""
        await self.client.initialize()

    @classmethod
    async def create(cls, viking_path: str = "/"):
        """Factory method to create and initialize a VikingClient instance"""
        instance = cls(viking_path)
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

    async def find(self, query: str, target_uri: Optional[str] = None):
        """搜索资源"""
        if target_uri:
            return await self.client.find(query, target_uri=target_uri)
        return await self.client.find(query)

    async def add_resource(
        self, local_path: str, desc: str, target_path: Optional[str] = None, wait: bool = False
    ) -> Optional[Dict[str, Any]]:
        """添加资源到 Viking"""
        viking_target = f"{viking_resource_prefix}{self.viking_path}"
        if target_path:
            viking_target = f"{viking_resource_prefix}{target_path}"

        file_name = os.path.basename(local_path)
        object_key = f"{file_name}"
        config = load_config()
        openviking_config = config.openviking

        try:
            self.tos_client.put_object_from_file(
                openviking_config.tos_bucket, object_key, local_path
            )
            self.tos_client.set_object_expires(openviking_config.tos_bucket, object_key, 1)
        except tos.exceptions.TosClientError as e:
            print(f"Failed to upload {local_path} to TOS: {e}")
            return None

        try:
            pre_signed_url = self.tos_client.pre_signed_url(
                tos.HttpMethodType.Http_Method_Get,
                openviking_config.tos_bucket,
                object_key,
                expires=300,
            ).signed_url
        except tos.exceptions.TosClientError as e:
            print(f"Failed to generate pre-signed URL for {object_key}: {e}")
            return None

        result = await self.client.add_resource(
            path=pre_signed_url, target=viking_target, reason=desc, wait=wait
        )
        return result

    async def list_resources(
        self, path: Optional[str] = None, recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """列出资源"""
        if path is None or path == "":
            path = f"{viking_resource_prefix}{self.viking_path}"
        entries = await self.client.ls(path, recursive=recursive)
        return entries

    async def read_content(self, uri: str, level: str = "abstract") -> str:
        """读取内容

        Args:
            uri: Viking URI
            level: 读取级别 ("abstract" - L0摘要, "overview" - L1概览, "read" - L2完整内容)
        """
        if level == "abstract":
            return await self.client.abstract(uri)
        elif level == "overview":
            return await self.client.overview(uri)
        elif level == "read":
            return await self.client.read(uri)
        else:
            raise ValueError(f"Unsupported level: {level}")

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

    async def search_user_memory(self, query: str) -> list[Any]:
        result = await self.client.search(query, target_uri=uri_user_memory)
        return (
            [self._matched_context_to_dict(m) for m in result.memories]
            if hasattr(result, "memories")
            else []
        )

    async def search_memory(
        self, query: str, session_id: str, messages: list[dict[str, Any]], limit: int = 10
    ) -> dict[str, list[Any]]:
        """通过上下文消息，检索viking 的user、Agent memory"""
        session = self.client.session(session_id)
        if self.mode == "local":
            for message in messages:
                session.add_message(role=message.get("role"), parts=[TextPart(text=message.get("content"))])
        else:
            for message in messages:
                await session.add_message(role=message.get("role"), content=message.get("content"))
        user_memory = await self.client.search(
            query=query,
            session=session,
            target_uri=uri_user_memory,
            limit=limit,
        )
        agent_memory = await self.client.search(
            query=query,
            session=session,
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

    async def commit(self, session_id: str, messages: list[dict[str, Any]]):
        """提交会话"""
        session = self.client.session(session_id)

        if self.mode == "local":
            for message in messages:
                # logger.debug(f"message = {message}")
                session.add_message(role=message.get("role"), parts=[TextPart(text=message.get("content"))])
            # session.add_message(role="assistant", parts=[TextPart(text="搜索完成！"),ToolPart(
            #     tool_id="tool_web_search_001",
            #     tool_name="web_search",
            #     tool_uri=f"viking://session/{session_id}/tools/tool_web_search_001",
            #     tool_input={"query": "Python asyncio tutorial", "max_results": 10},
            #     tool_output="找到 3 篇高质量教程：1. FastAPI官方文档 2. Real Python教程 3. 菜鸟教程",
            #     tool_status="completed",
            #     skill_uri="",
            #     duration_ms=2500,
            # )])
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
        result = await self.search_memory(current_message, session_id, history)
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
    client = await VikingClient.create()
    # res = client.list_resources()
    # res = await client.search("头有点疼", target_uri="viking://user/memories/")
    # res = await client.get_viking_memory_context("123", current_message="头疼", history=[])
    # res = await client.list_resources("viking://user/memories/events")
    # res = await client.read_content("viking://user/memories/events", level="overview")
    # res = await client.add_resource("/Users/bytedance/Documents/论文/吉比特年报.pdf", "吉比特年报")
    # res = await client.commit("123", [{"role": "user", "content": "我叫王大锤"}])
    res = await client.commit("1234", [{"role": "user", "content": "帮我搜索 Python asyncio 教程"}
                                       ,{"role": "assistant", "content": "我来帮你搜索 Python asyncio 相关的教程。"}])
    print(res)

    print("等待后台处理完成...")
    await client.client.wait_processed(timeout=60)
    print("处理完成！")

    client.close()


async def account_test():
    client = ov.AsyncHTTPClient(url="")
    await client.initialize()
    res = await client.search("test", target_uri="viking://memories/")
    print(res)


if __name__ == "__main__":
    asyncio.run(main_test())
    # asyncio.run(account_test())
