"""OpenViking-managed MCP tool discovery and invocation."""

import base64
import json
from typing import Any

from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
from vikingbot.agent.tools.base import MultimodalToolResult, ToolContext
from vikingbot.agent.tools.ov_file import OVFileTool
from vikingbot.utils.image_format import sniff_image_format


class VikingFindMCPToolsTool(OVFileTool):
    """Discover MCP tools through the authenticated OpenViking server."""

    @property
    def name(self) -> str:
        return "openviking_find_mcp_tools"

    @property
    def description(self) -> str:
        return (
            "Semantically discover tools from external MCP Servers registered in the "
            "connected OpenViking service. This catalog is independent of MCP Servers "
            "configured directly under bot.tools.mcp_servers and does not contain their "
            "mcp_<server>_<tool> names. "
            "Prefer an already-arranged Live Context resource when one matches the task; "
            "use this tool for low-frequency or custom requests. The result contains each "
            "candidate's routing URI, inputSchema, and annotations. Select one candidate, "
            "construct arguments strictly from its inputSchema, then call "
            "openviking_invoke_mcp_tool with the returned URI. No extra read is needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise description of the capability needed.",
                    "minLength": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of ranked candidates to return.",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        query: str,
        limit: int = 10,
        **kwargs: Any,
    ) -> str:
        client = None
        try:
            client = await self._get_client(tool_context)
            result = await client.find_mcp_tools(query, limit=limit)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: discovering OpenViking MCP tools failed: {exc}"
        finally:
            await self._release_client(tool_context, client)


class VikingInvokeMCPTool(OVFileTool):
    """Invoke an MCP tool through the authenticated OpenViking server."""

    @staticmethod
    def _model_result(result: dict[str, Any]) -> str | MultimodalToolResult:
        content = result.get("content")
        if not isinstance(content, list):
            return json.dumps(result, ensure_ascii=False, indent=2)

        model_content: list[dict[str, Any]] = []
        text_blocks: list[dict[str, Any]] = []
        media_bytes = 0
        for block in content:
            if not isinstance(block, dict):
                text_blocks.append({"type": "unknown", "value": str(block)})
                continue
            if block.get("type") != "image":
                text_blocks.append(block)
                continue

            data = block.get("data")
            mime_type = block.get("mimeType")
            if not isinstance(data, str) or not isinstance(mime_type, str):
                text_blocks.append({"type": "image", "error": "invalid image content"})
                continue
            try:
                decoded = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                text_blocks.append({"type": "image", "error": "invalid base64 data"})
                continue
            image_format = sniff_image_format(decoded)
            if image_format is None or image_format.mime_type != mime_type:
                text_blocks.append(
                    {
                        "type": "image",
                        "mimeType": mime_type,
                        "error": "bytes do not match the declared image type",
                    }
                )
                continue
            decoded_size = len(decoded)
            if media_bytes + decoded_size > MAX_INLINE_TOOL_RESULT_MEDIA_BYTES:
                text_blocks.append(
                    {
                        "type": "image",
                        "mimeType": mime_type,
                        "error": "inline media limit exceeded",
                    }
                )
                continue
            media_bytes += decoded_size
            model_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{data}"},
                }
            )
            text_blocks.append(
                {
                    "type": "image",
                    "mimeType": mime_type,
                    "bytes": decoded_size,
                }
            )

        text_payload = {
            **result,
            "content": text_blocks,
        }
        text = json.dumps(text_payload, ensure_ascii=False, indent=2)
        if result.get("isError") is True:
            text = f"Error: OpenViking MCP tool returned isError=true:\n{text}"
        if not model_content:
            return text
        return MultimodalToolResult(
            text=text,
            content=[{"type": "text", "text": text}, *model_content],
        )

    @property
    def name(self) -> str:
        return "openviking_invoke_mcp_tool"

    @property
    def description(self) -> str:
        return (
            "Invoke one tool from an external MCP Server registered in OpenViking and selected "
            "by openviking_find_mcp_tools. This tool does not invoke VikingBot-connected "
            "mcp_<server>_<tool> tools. Use the returned URI unchanged and provide only "
            "arguments allowed by that candidate's inputSchema. Inspect its annotations before "
            "invocation because the upstream tool may have external side effects. OpenViking "
            "resolves credentials and validates arguments. Calls are not retried."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_uri": {
                    "type": "string",
                    "description": (
                        "Exact viking://agent/tools/... URI returned by openviking_find_mcp_tools."
                    ),
                    "minLength": 1,
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments conforming to the discovered tool inputSchema.",
                    "default": {},
                },
            },
            "required": ["tool_uri"],
        }

    async def execute(
        self,
        tool_context: ToolContext,
        tool_uri: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str | MultimodalToolResult:
        client = None
        try:
            client = await self._get_client(tool_context)
            result = await client.invoke_mcp_tool(tool_uri, arguments=arguments or {})
            if not isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False, indent=2)
            return self._model_result(result)
        except Exception as exc:
            return f"Error: invoking OpenViking MCP tool failed: {exc}"
        finally:
            await self._release_client(tool_context, client)
