# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped MCP server registry and tool catalog management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx
import yaml
from jsonschema import SchemaError, ValidationError, validate
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, InitializeResult, Tool

from openviking.core.uri_validation import validate_request_viking_uri
from openviking.pyagfs import AGFSNotFoundError, AsyncAGFSClient
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import (
    AlreadyExistsError,
    InvalidArgumentError,
    NotFoundError,
    ProcessingError,
    UnavailableError,
)

_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_TOOL_URI_RE = re.compile(
    r"^viking://agent/tools/(?P<server>[A-Za-z0-9_-]{1,64})/"
    r"(?P<tool>[A-Za-z0-9_.-]{1,128})\.json$"
)
_TOOLS_ROOT = "viking://agent/tools"
_REGISTRY_VERSION = 1
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_context_nodes (
    node_uri TEXT PRIMARY KEY,
    mcp_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_uri TEXT NOT NULL,
    input_revision TEXT NOT NULL,
    args_json TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_context_nodes_tool
ON live_context_nodes (mcp_name, tool_name);
"""


@dataclass(frozen=True, slots=True)
class MCPToolMount:
    tool_uri: str
    node_uri: str
    title: str
    description: str | None
    timeout_seconds: int
    arguments: dict[str, Any]
    tags: list[str] | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_read_result(result: Any) -> bytes:
    if isinstance(result, bytes):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(result, str):
        return result.encode("utf-8")
    raise ValueError("MCP registry content must be bytes or JSON text")


class MCPService:
    """Manage remote Streamable HTTP MCP servers for one OpenViking account."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self._async_agfs: AsyncAGFSClient | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    def _agfs(self) -> AsyncAGFSClient:
        if self._async_agfs is None:
            agfs = self._service._agfs
            if agfs is None:
                raise RuntimeError("AGFS is not initialized")
            self._async_agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        return self._async_agfs

    def _lock(self, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault(account_id, asyncio.Lock())

    @staticmethod
    def _registry_path(account_id: str) -> str:
        return f"/local/{account_id}/_system/mcps.json"

    @staticmethod
    def _mounts_path(account_id: str) -> str:
        return f"/local/{account_id}/_system/live_context_nodes.dat"

    @staticmethod
    def _empty_document() -> dict[str, Any]:
        return {"version": _REGISTRY_VERSION, "servers": {}}

    async def _read_document(self, path: str) -> dict[str, Any]:
        try:
            raw = _decode_read_result(await self._agfs().read(path))
        except (AGFSNotFoundError, FileNotFoundError):
            return self._empty_document()
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid MCP registry document: {path}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("servers"), dict):
            raise RuntimeError(f"Invalid MCP registry document: {path}")
        return document

    async def _write_document(self, path: str, document: dict[str, Any]) -> None:
        await self._agfs().ensure_parent_dirs(path)
        payload = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        await self._agfs().write(path, payload)

    async def _open_mount_database(self, ctx: RequestContext) -> sqlite3.Connection:
        path = self._mounts_path(ctx.account_id)
        try:
            raw = _decode_read_result(await self._agfs().read(path))
        except (AGFSNotFoundError, FileNotFoundError):
            raw = b""
        temporary = tempfile.NamedTemporaryFile(
            prefix="openviking-mounts-",
            suffix=".sqlite3",
            delete=False,
        )
        temporary_path = temporary.name
        if raw:
            temporary.write(raw)
            temporary.flush()
        temporary.close()
        connection = sqlite3.connect(temporary_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(_MOUNT_SCHEMA)
            return connection
        except sqlite3.DatabaseError as exc:
            connection.close()
            Path(temporary_path).unlink(missing_ok=True)
            raise RuntimeError(f"Invalid live context mount database: {path}") from exc

    @staticmethod
    def _close_mount_database(connection: sqlite3.Connection) -> None:
        row = connection.execute("PRAGMA database_list").fetchone()
        temporary_path = row[2] if row and len(row) > 2 else ""
        connection.close()
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)

    async def _write_mount_database(
        self,
        connection: sqlite3.Connection,
        ctx: RequestContext,
    ) -> None:
        connection.commit()
        row = connection.execute("PRAGMA database_list").fetchone()
        database_path = row[2] if row and len(row) > 2 else ""
        if not database_path:
            raise RuntimeError("Live context mount database has no backing file")
        payload = Path(database_path).read_bytes()
        path = self._mounts_path(ctx.account_id)
        await self._agfs().ensure_parent_dirs(path)
        await self._agfs().write(path, payload)

    async def _load(self, ctx: RequestContext) -> dict[str, Any]:
        registry = await self._read_document(self._registry_path(ctx.account_id))
        for server in registry["servers"].values():
            if isinstance(server, dict):
                server.pop("tools", None)
        return registry

    @staticmethod
    def _validate_server_name(name: str) -> str:
        if not _SERVER_NAME_RE.fullmatch(name):
            raise InvalidArgumentError(
                "MCP server name must contain only letters, numbers, '_' or '-' (max 64)"
            )
        return name

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.fragment:
            raise InvalidArgumentError("MCP URL must not contain credentials or a fragment")
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return url
        raise InvalidArgumentError("MCP URL must use HTTP or HTTPS and include a host")

    @staticmethod
    def _validate_headers(headers: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_name, raw_value in headers.items():
            name = raw_name.strip()
            value = raw_value.strip()
            if (
                not name
                or not value
                or any(char in name for char in "\r\n:")
                or any(char in value for char in "\r\n")
            ):
                raise InvalidArgumentError("Invalid MCP HTTP header")
            normalized[name] = value
        return normalized

    @staticmethod
    def _public_server(server: dict[str, Any], tool_count: int) -> dict[str, Any]:
        return {
            key: value
            for key, value in server.items()
            if key not in {"header_names", "headers", "tools"}
        } | {
            "header_names": sorted(server.get("headers", {})),
            "tool_count": tool_count,
        }

    @staticmethod
    def _input_revision(tool: dict[str, Any]) -> str:
        revision_input = {
            "name": tool.get("name"),
            "inputSchema": tool.get("inputSchema", {}),
        }
        payload = json.dumps(
            revision_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _tool_revision(tool: dict[str, Any]) -> str:
        payload = json.dumps(
            tool,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _tool_document(server: dict[str, Any], tool: Tool) -> dict[str, Any]:
        raw_tool = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {
            "kind": "mcp_tool",
            "server": server["name"],
            "address": server["url"],
            "revision": MCPService._tool_revision(raw_tool),
            "input_revision": MCPService._input_revision(raw_tool),
            "tool": raw_tool,
        }

    @asynccontextmanager
    async def _session(
        self,
        server: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> AsyncIterator[ClientSession]:
        timeout = httpx.Timeout(timeout_seconds, read=timeout_seconds)
        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout) as http_client:
                async with streamable_http_client(
                    server["url"],
                    http_client=http_client,
                ) as (read, write, _):
                    async with ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(seconds=timeout_seconds),
                    ) as session:
                        yield session
        except (InvalidArgumentError, NotFoundError, UnavailableError):
            raise
        except Exception as exc:
            raise UnavailableError(
                "MCP server",
                f"connection failed ({type(exc).__name__}); check endpoint and credentials",
            ) from exc

    async def _discover(
        self,
        server: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[InitializeResult, list[Tool]]:
        async with self._session(server, headers) as session:
            initialized = await session.initialize()
            tools: list[Tool] = []
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(100):
                result = await session.list_tools(cursor=cursor)
                tools.extend(result.tools)
                cursor = result.nextCursor
                if not cursor:
                    return initialized, tools
                if cursor in seen:
                    raise InvalidArgumentError(
                        "MCP server returned a repeated tool pagination cursor"
                    )
                seen.add(cursor)
            raise InvalidArgumentError("MCP tool catalog exceeded 100 pages")

    async def _ensure_directory(self, uri: str, ctx: RequestContext) -> None:
        if await self._service.viking_fs.exists(uri, ctx=ctx):
            return
        try:
            await self._service.fs.mkdir(uri, ctx=ctx)
        except AlreadyExistsError:
            return
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise

    async def _publish_catalog(
        self,
        server_name: str,
        tools: list[dict[str, Any]],
        removed_names: set[str],
        ctx: RequestContext,
        *,
        wait: bool = True,
    ) -> dict[str, Any] | None:
        server_root = f"{_TOOLS_ROOT}/{server_name}"
        await self._ensure_directory(_TOOLS_ROOT, ctx)
        await self._ensure_directory(server_root, ctx)

        operations = [
            {
                "uri": f"{server_root}/{item['tool']['name']}.json",
                "content": json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True),
                "mode": "upsert",
            }
            for item in tools
        ]
        result = None
        if operations:
            result = await self._service.fs.batch_write(
                root_uri=server_root,
                operations=operations,
                ctx=ctx,
                wait=wait,
            )

        for name in sorted(removed_names):
            await self._service.fs.rm(
                f"{server_root}/{name}.json",
                ctx=ctx,
                recursive=False,
                wait=wait,
            )
        return result

    async def _catalog_uris(self, server_name: str, ctx: RequestContext) -> list[str]:
        root = f"{_TOOLS_ROOT}/{server_name}"
        uris: list[str] = []
        offset = 0
        while True:
            try:
                page = await self._service.fs.ls(
                    root,
                    ctx=ctx,
                    output="original",
                    node_limit=1000,
                    offset=offset,
                )
            except (AGFSNotFoundError, NotFoundError):
                return []
            entries = page.entries
            uris.extend(
                entry["uri"]
                for entry in entries
                if not entry.get("isDir", False)
                and isinstance(entry.get("uri"), str)
                and entry["uri"].endswith(".json")
            )
            if not page.has_more:
                return uris
            if not entries:
                raise RuntimeError(f"Unable to paginate MCP tool catalog: {root}")
            offset += len(entries)

    async def _catalog_names(self, server_name: str, ctx: RequestContext) -> set[str]:
        prefix = f"{_TOOLS_ROOT}/{server_name}/"
        return {
            uri[len(prefix) : -len(".json")]
            for uri in await self._catalog_uris(server_name, ctx)
            if uri.startswith(prefix)
        }

    async def _catalog(self, server_name: str, ctx: RequestContext) -> list[dict[str, Any]]:
        uris = await self._catalog_uris(server_name, ctx)
        contents = await asyncio.gather(*(self._service.fs.read(uri, ctx=ctx) for uri in uris))
        documents: list[dict[str, Any]] = []
        for uri, content in zip(uris, contents, strict=True):
            try:
                document = json.loads(content)
                tool = document["tool"]
                tool_name = tool["name"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Invalid MCP tool document: {uri}") from exc
            expected_uri = f"{_TOOLS_ROOT}/{server_name}/{tool_name}.json"
            if (
                not isinstance(document, dict)
                or not isinstance(tool, dict)
                or not isinstance(tool_name, str)
                or document.get("server") != server_name
                or uri != expected_uri
            ):
                raise RuntimeError(f"Invalid MCP tool document: {uri}")
            documents.append({"uri": uri, **document})
        return documents

    async def _tool(self, uri: str, ctx: RequestContext) -> dict[str, Any]:
        match = _TOOL_URI_RE.fullmatch(uri)
        if match is None:
            raise InvalidArgumentError(
                "Tool URI must match viking://agent/tools/<server>/<tool>.json"
            )
        server_name = match.group("server")
        tool_name = match.group("tool")
        await self._registered(server_name, ctx)
        try:
            content = await self._service.fs.read(uri, ctx=ctx)
            document = json.loads(content)
            tool = document["tool"]
        except (AGFSNotFoundError, NotFoundError, FileNotFoundError) as exc:
            raise NotFoundError(uri, "MCP tool") from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid MCP tool document: {uri}") from exc
        if (
            not isinstance(document, dict)
            or document.get("server") != server_name
            or not isinstance(document.get("revision"), str)
            or not isinstance(document.get("input_revision"), str)
            or not isinstance(tool, dict)
            or tool.get("name") != tool_name
        ):
            raise RuntimeError(f"Invalid MCP tool document: {uri}")
        return document

    @staticmethod
    def _validate_tool_arguments(tool_document: dict[str, Any], arguments: dict[str, Any]) -> None:
        schema = tool_document["tool"].get("inputSchema", {"type": "object"})
        try:
            validate(instance=arguments, schema=schema)
        except SchemaError as exc:
            raise RuntimeError("Invalid inputSchema in MCP tool document") from exc
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path)
            prefix = (
                f"Invalid MCP tool arguments at '{location}'"
                if location
                else ("Invalid MCP tool arguments")
            )
            raise InvalidArgumentError(f"{prefix}: {exc.message}") from exc

    @staticmethod
    def _tool_is_read_only(tool_document: dict[str, Any]) -> bool:
        annotations = tool_document["tool"].get("annotations")
        if annotations is None:
            return True
        return (
            isinstance(annotations, dict)
            and annotations.get("readOnlyHint") is not False
            and annotations.get("destructiveHint") is not True
        )

    async def _readable_mount_rows(
        self,
        rows: list[sqlite3.Row],
        ctx: RequestContext,
    ) -> list[sqlite3.Row]:
        if not rows:
            return []
        access = await self._service.fs.can_read_many(
            [row["node_uri"] for row in rows],
            ctx,
        )
        return [row for row in rows if access.get(row["node_uri"], False)]

    async def _affected_mounts(
        self,
        server_name: str,
        current_tools: dict[str, dict[str, Any]],
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        connection = await self._open_mount_database(ctx)
        try:
            rows = connection.execute(
                """
                SELECT * FROM live_context_nodes
                WHERE mcp_name = ?
                ORDER BY tool_name, node_uri
                """,
                (server_name,),
            ).fetchall()
        finally:
            self._close_mount_database(connection)

        rows = await self._readable_mount_rows(rows, ctx)
        affected: list[dict[str, Any]] = []
        for row in rows:
            current = current_tools.get(row["tool_name"])
            item = {
                "node_uri": row["node_uri"],
                "tool_uri": row["tool_uri"],
                "tool": row["tool_name"],
                "mounted_input_revision": row["input_revision"],
                "current_input_revision": (
                    current["input_revision"] if current is not None else None
                ),
            }
            if current is None:
                affected.append(
                    {
                        **item,
                        "status": "REMOVED",
                        "reason": "Tool is no longer provided by the MCP server",
                    }
                )
                continue
            if row["input_revision"] == current["input_revision"]:
                continue

            try:
                arguments = json.loads(row["args_json"])
                if not isinstance(arguments, dict):
                    raise ValueError("mounted arguments are not a JSON object")
                self._validate_tool_arguments(current, arguments)
            except (InvalidArgumentError, RuntimeError, ValueError) as exc:
                affected.append(
                    {
                        **item,
                        "status": "INVALID",
                        "reason": f"Tool input revision changed; {exc}",
                    }
                )
            else:
                affected.append(
                    {
                        **item,
                        "status": "REVIEW",
                        "reason": (
                            "Tool input revision changed; mounted arguments remain valid "
                            "under the current input schema"
                        ),
                    }
                )
        return affected

    @staticmethod
    def _validate_node_uri(uri: str, ctx: RequestContext) -> str:
        normalized = validate_request_viking_uri(
            uri,
            ctx,
            field_name="live context node URI",
        )
        if not normalized.endswith(".md"):
            raise InvalidArgumentError("Live context node URI must end with .md")
        return normalized

    @staticmethod
    def _batch_mount_root(node_uri: str) -> str | None:
        prefix = "viking://resources/"
        if not node_uri.startswith(prefix):
            return None
        relative = node_uri[len(prefix) :]
        top_level, separator, _ = relative.partition("/")
        if not separator or not top_level:
            return None
        return f"{prefix}{top_level}"

    @staticmethod
    def _render_live_context_node(
        tool_uri: str,
        tool_document: dict[str, Any],
        title: str,
        description: str | None,
        timeout_seconds: int,
        arguments: dict[str, Any],
    ) -> str:
        metadata: dict[str, Any] = {
            "type": "OpenViking Live Context",
            "title": title,
        }
        if description is not None:
            metadata["description"] = description
        metadata.update(
            {
                "x-openviking": {
                    "schema_version": "v1",
                    "type": "mcp",
                    "definition": tool_uri,
                    "input_revision": tool_document["input_revision"],
                    "args": arguments,
                    "timeout_seconds": timeout_seconds,
                    "retry": {
                        "max_attempts": 3,
                        "backoff_seconds": 1,
                        "backoff_multiplier": 2,
                        "max_backoff_seconds": 5,
                    },
                },
            }
        )
        frontmatter = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).rstrip()
        return f"---\n{frontmatter}\n---\n"

    async def add(
        self,
        name: str,
        url: str,
        headers: dict[str, str],
        ctx: RequestContext,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        name = self._validate_server_name(name)
        url = self._validate_url(url)
        headers = self._validate_headers(headers)
        candidate = {"name": name, "url": url}
        initialized, discovered = await self._discover(candidate, headers)
        tools = [self._tool_document(candidate, tool) for tool in discovered]
        invalid_names = [
            tool["tool"]["name"]
            for tool in tools
            if not self._valid_tool_name(tool["tool"]["name"])
        ]
        if invalid_names:
            raise InvalidArgumentError(
                f"MCP tool names are not valid OpenViking filenames: {', '.join(invalid_names)}"
            )

        async with self._lock(ctx.account_id):
            registry = await self._load(ctx)
            if name in registry["servers"]:
                raise AlreadyExistsError(name, "MCP server")
            timestamp = _now()
            server = {
                "name": name,
                "url": url,
                "headers": headers,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            processing = await self._publish_catalog(name, tools, set(), ctx, wait=wait)
            registry["servers"][name] = server
            await self._write_document(self._registry_path(ctx.account_id), registry)

        result = {
            **self._public_server(server, len(tools)),
            "protocol_version": initialized.protocolVersion,
        }
        if processing is not None:
            result["processing"] = processing
        return result

    async def remove(self, name: str, ctx: RequestContext) -> dict[str, Any]:
        name = self._validate_server_name(name)
        async with self._lock(ctx.account_id):
            registry = await self._load(ctx)
            if name not in registry["servers"]:
                raise NotFoundError(name, "MCP server")
            await self._service.fs.rm(
                f"{_TOOLS_ROOT}/{name}",
                ctx=ctx,
                recursive=True,
                wait=True,
            )
            registry["servers"].pop(name)
            await self._write_document(self._registry_path(ctx.account_id), registry)
        return {"name": name, "removed": True}

    async def list(self, ctx: RequestContext) -> dict[str, Any]:
        registry = await self._load(ctx)
        items = sorted(registry["servers"].items())
        counts = await asyncio.gather(*(self._catalog_names(name, ctx) for name, _ in items))
        servers = [
            self._public_server(server, len(tool_names))
            for (_, server), tool_names in zip(items, counts, strict=True)
        ]
        return {"servers": servers, "total": len(servers)}

    async def show(self, name: str, ctx: RequestContext) -> dict[str, Any]:
        name = self._validate_server_name(name)
        registry = await self._load(ctx)
        server = registry["servers"].get(name)
        if server is None:
            raise NotFoundError(name, "MCP server")
        tools = await self._catalog(name, ctx)
        return {
            **self._public_server(server, len(tools)),
            "tools": tools,
        }

    async def find(
        self,
        query: str,
        limit: int,
        ctx: RequestContext,
        *,
        include_definition: bool = False,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise InvalidArgumentError("Tool search query must not be empty")

        search_result = await self._service.search.find(
            query=query,
            ctx=ctx,
            target_uri=_TOOLS_ROOT,
            limit=limit,
            level=[2],
        )
        result_dict = (
            search_result.to_dict()
            if hasattr(search_result, "to_dict")
            else dict(search_result or {})
        )
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in result_dict.get("resources", []):
            uri = hit.get("uri")
            match = _TOOL_URI_RE.fullmatch(uri) if isinstance(uri, str) else None
            if match is None or uri in seen:
                continue
            try:
                document = await self._tool(uri, ctx)
            except NotFoundError:
                continue
            seen.add(uri)
            tool = document["tool"]
            results.append(
                {
                    "uri": uri,
                    "server": match.group("server"),
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "revision": document["revision"],
                    "input_revision": document["input_revision"],
                    "score": hit.get("score", 0.0),
                }
            )
            if include_definition:
                results[-1]["tool"] = tool
        return {"tools": results, "total": len(results)}

    async def _registered(
        self,
        name: str,
        ctx: RequestContext,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        name = self._validate_server_name(name)
        registry = await self._load(ctx)
        server = registry["servers"].get(name)
        if server is None:
            raise NotFoundError(name, "MCP server")
        headers = server.get("headers", {})
        return server, headers

    async def validate(
        self,
        name: str,
        url: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        name = self._validate_server_name(name)
        server = {"name": name, "url": self._validate_url(url)}
        headers = self._validate_headers(headers)
        initialized, tools = await self._discover(server, headers)
        return {
            "name": name,
            "valid": True,
            "protocol_version": initialized.protocolVersion,
            "server_info": initialized.serverInfo.model_dump(mode="json"),
            "tool_count": len(tools),
        }

    async def sync(
        self,
        name: str,
        ctx: RequestContext,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        async with self._lock(ctx.account_id):
            server, headers = await self._registered(name, ctx)
            initialized, discovered = await self._discover(server, headers)
            tools = [self._tool_document(server, tool) for tool in discovered]
            invalid_names = [
                tool["tool"]["name"]
                for tool in tools
                if not self._valid_tool_name(tool["tool"]["name"])
            ]
            if invalid_names:
                raise InvalidArgumentError(
                    f"MCP tool names are not valid OpenViking filenames: {', '.join(invalid_names)}"
                )
            existing_tools = {
                document["tool"]["name"]: document for document in await self._catalog(name, ctx)
            }
            current_tools = {document["tool"]["name"]: document for document in tools}
            existing_names = set(existing_tools)
            current_names = set(current_tools)
            added = sorted(current_names - existing_names)
            removed = sorted(existing_names - current_names)
            changed = sorted(
                tool_name
                for tool_name in existing_names & current_names
                if existing_tools[tool_name]["input_revision"]
                != current_tools[tool_name]["input_revision"]
            )
            unchanged = sorted((existing_names & current_names) - set(changed))
            definitions_to_publish = sorted(
                tool_name
                for tool_name in current_names
                if tool_name not in existing_tools
                or existing_tools[tool_name]["revision"] != current_tools[tool_name]["revision"]
            )
            await self._publish_catalog(
                name,
                [current_tools[tool_name] for tool_name in definitions_to_publish],
                set(removed),
                ctx,
                wait=wait,
            )
            affected_mounts = await self._affected_mounts(name, current_tools, ctx)
            registry = await self._load(ctx)
            current = registry["servers"][name]
            current.pop("tools", None)
            current["updated_at"] = _now()
            await self._write_document(self._registry_path(ctx.account_id), registry)
        return {
            "name": name,
            "synced": True,
            "protocol_version": initialized.protocolVersion,
            "tool_count": len(tools),
            "changes": {
                "added": added,
                "changed": changed,
                "unchanged": unchanged,
                "removed": removed,
            },
            "affected_mounts": affected_mounts,
        }

    @staticmethod
    def _valid_tool_name(name: str) -> bool:
        return name not in {".", ".."} and _TOOL_NAME_RE.fullmatch(name) is not None

    async def status(self, name: str, ctx: RequestContext) -> dict[str, Any]:
        name = self._validate_server_name(name)
        registry = await self._load(ctx)
        server = registry["servers"].get(name)
        if server is None:
            raise NotFoundError(name, "MCP server")
        headers = server.get("headers", {})
        try:
            initialized, tools = await self._discover(server, headers)
            return {
                "name": name,
                "status": "running",
                "protocol_version": initialized.protocolVersion,
                "tool_count": len(tools),
            }
        except (InvalidArgumentError, UnavailableError) as exc:
            tool_names = await self._catalog_names(name, ctx)
            return {
                "name": name,
                "status": "error",
                "error": str(exc),
                "tool_count": len(tool_names),
            }

    async def mount(
        self,
        tool_uri: str,
        node_uri: str,
        title: str,
        description: str | None,
        timeout_seconds: int,
        arguments: dict[str, Any],
        ctx: RequestContext,
        *,
        tags: list[str] | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        if not title.strip():
            raise InvalidArgumentError("Live context node title must not be empty")
        if timeout_seconds <= 0:
            raise InvalidArgumentError("Live context node timeout_seconds must be positive")
        node_uri = self._validate_node_uri(node_uri, ctx)
        async with self._lock(ctx.account_id):
            tool_document = await self._tool(tool_uri, ctx)
            self._validate_tool_arguments(tool_document, arguments)
            match = _TOOL_URI_RE.fullmatch(tool_uri)
            assert match is not None
            server_name = match.group("server")
            tool_name = match.group("tool")

            connection = await self._open_mount_database(ctx)
            try:
                existing = connection.execute(
                    "SELECT * FROM live_context_nodes WHERE node_uri = ?",
                    (node_uri,),
                ).fetchone()
                content = self._render_live_context_node(
                    tool_uri,
                    tool_document,
                    title,
                    description,
                    timeout_seconds,
                    arguments,
                )
                arguments_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                created_at = existing["created_at"] if existing is not None else _now()
                existing_data = json.loads(existing["data"]) if existing is not None else {}
                mount_data = {
                    "title": title,
                    "description": description,
                    "timeout_seconds": timeout_seconds,
                    "tags": tags if tags is not None else existing_data.get("tags", []),
                }
                await self._service.fs.write(
                    node_uri,
                    content,
                    ctx=ctx,
                    mode="replace",
                    wait=wait,
                    tags=tags,
                )
                connection.execute(
                    """
                    INSERT INTO live_context_nodes (
                        node_uri, mcp_name, tool_name, tool_uri, input_revision, args_json,
                        data, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_uri) DO UPDATE SET
                        mcp_name = excluded.mcp_name,
                        tool_name = excluded.tool_name,
                        tool_uri = excluded.tool_uri,
                        input_revision = excluded.input_revision,
                        args_json = excluded.args_json,
                        data = excluded.data
                    """,
                    (
                        node_uri,
                        server_name,
                        tool_name,
                        tool_uri,
                        tool_document["input_revision"],
                        arguments_json,
                        json.dumps(mount_data, ensure_ascii=False, sort_keys=True),
                        created_at,
                    ),
                )
                await self._write_mount_database(connection, ctx)
            finally:
                self._close_mount_database(connection)

        return {
            "node_uri": node_uri,
            "mcp": server_name,
            "tool": tool_name,
            "tool_uri": tool_uri,
            "input_revision": tool_document["input_revision"],
            "title": title,
            "description": description,
            "timeout_seconds": timeout_seconds,
            "arguments": arguments,
            "tags": mount_data["tags"],
            "created_at": created_at,
            "mounted": True,
        }

    async def batch_mount(
        self,
        mounts: list[MCPToolMount],
        ctx: RequestContext,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        if not mounts:
            raise InvalidArgumentError("Batch mount must contain at least one mount")

        async with self._lock(ctx.account_id):
            prepared: list[dict[str, Any]] = []
            seen_node_uris: set[str] = set()
            tool_documents: dict[str, dict[str, Any]] = {}
            for mount in mounts:
                title = mount.title
                timeout_seconds = mount.timeout_seconds
                arguments = mount.arguments
                if not title.strip():
                    raise InvalidArgumentError("Live context node title must not be empty")
                if timeout_seconds <= 0:
                    raise InvalidArgumentError("Live context node timeout_seconds must be positive")
                node_uri = self._validate_node_uri(mount.node_uri, ctx)
                if node_uri in seen_node_uris:
                    raise InvalidArgumentError(f"Duplicate batch mount node URI: {node_uri}")
                seen_node_uris.add(node_uri)

                tool_uri = mount.tool_uri
                tool_document = tool_documents.get(tool_uri)
                if tool_document is None:
                    tool_document = await self._tool(tool_uri, ctx)
                    tool_documents[tool_uri] = tool_document
                self._validate_tool_arguments(tool_document, arguments)
                match = _TOOL_URI_RE.fullmatch(tool_uri)
                assert match is not None
                prepared.append(
                    {
                        "tool_uri": mount.tool_uri,
                        "node_uri": node_uri,
                        "title": mount.title,
                        "description": mount.description,
                        "timeout_seconds": mount.timeout_seconds,
                        "arguments": mount.arguments,
                        "tags": mount.tags,
                        "tool_document": tool_document,
                        "server_name": match.group("server"),
                        "tool_name": match.group("tool"),
                        "content": self._render_live_context_node(
                            tool_uri,
                            tool_document,
                            title,
                            mount.description,
                            timeout_seconds,
                            arguments,
                        ),
                    }
                )

            connection = await self._open_mount_database(ctx)
            try:
                for item in prepared:
                    existing = connection.execute(
                        "SELECT created_at, data FROM live_context_nodes WHERE node_uri = ?",
                        (item["node_uri"],),
                    ).fetchone()
                    item["created_at"] = existing["created_at"] if existing is not None else _now()
                    existing_data = json.loads(existing["data"]) if existing is not None else {}
                    item["data"] = {
                        "title": item["title"],
                        "description": item["description"],
                        "timeout_seconds": item["timeout_seconds"],
                        "tags": (
                            item["tags"]
                            if item["tags"] is not None
                            else existing_data.get("tags", [])
                        ),
                    }

                operations_by_root: dict[str, list[dict[str, Any]]] = {}
                direct_writes: list[dict[str, Any]] = []
                for item in prepared:
                    root_uri = self._batch_mount_root(item["node_uri"])
                    if root_uri is None:
                        direct_writes.append(item)
                        continue
                    operation = {
                        "uri": item["node_uri"],
                        "content": item["content"],
                        "mode": "replace",
                    }
                    if item.get("tags") is not None:
                        operation["tags"] = item["tags"]
                    operations_by_root.setdefault(root_uri, []).append(operation)

                for root_uri, operations in operations_by_root.items():
                    await self._ensure_directory(root_uri, ctx)
                    await self._service.fs.batch_write(
                        root_uri=root_uri,
                        operations=operations,
                        ctx=ctx,
                        wait=wait,
                    )
                for item in direct_writes:
                    await self._service.fs.write(
                        item["node_uri"],
                        item["content"],
                        ctx=ctx,
                        mode="replace",
                        wait=wait,
                        tags=item.get("tags"),
                    )

                for item in prepared:
                    connection.execute(
                        """
                        INSERT INTO live_context_nodes (
                            node_uri, mcp_name, tool_name, tool_uri, input_revision, args_json,
                            data, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(node_uri) DO UPDATE SET
                            mcp_name = excluded.mcp_name,
                            tool_name = excluded.tool_name,
                            tool_uri = excluded.tool_uri,
                            input_revision = excluded.input_revision,
                            args_json = excluded.args_json,
                            data = excluded.data
                        """,
                        (
                            item["node_uri"],
                            item["server_name"],
                            item["tool_name"],
                            item["tool_uri"],
                            item["tool_document"]["input_revision"],
                            json.dumps(
                                item["arguments"],
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            json.dumps(item["data"], ensure_ascii=False, sort_keys=True),
                            item["created_at"],
                        ),
                    )
                await self._write_mount_database(connection, ctx)
            finally:
                self._close_mount_database(connection)

        results = [
            {
                "node_uri": item["node_uri"],
                "mcp": item["server_name"],
                "tool": item["tool_name"],
                "tool_uri": item["tool_uri"],
                "input_revision": item["tool_document"]["input_revision"],
                "title": item["title"],
                "description": item.get("description"),
                "timeout_seconds": item["timeout_seconds"],
                "arguments": item["arguments"],
                "tags": item["data"]["tags"],
                "created_at": item["created_at"],
                "mounted": True,
            }
            for item in prepared
        ]
        return {"mounts": results, "total": len(results)}

    async def unmount(
        self,
        node_uri: str,
        ctx: RequestContext,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        node_uri = self._validate_node_uri(node_uri, ctx)
        async with self._lock(ctx.account_id):
            connection = await self._open_mount_database(ctx)
            try:
                row = connection.execute(
                    "SELECT * FROM live_context_nodes WHERE node_uri = ?",
                    (node_uri,),
                ).fetchone()
                if row is None:
                    raise NotFoundError(node_uri, "live context mount")
                try:
                    content = await self._service.fs.read(node_uri, ctx=ctx)
                except (AGFSNotFoundError, NotFoundError, FileNotFoundError):
                    content = None
                await self._service.fs.rm(node_uri, ctx=ctx, recursive=False, wait=wait)
                connection.execute(
                    "DELETE FROM live_context_nodes WHERE node_uri = ?",
                    (node_uri,),
                )
                try:
                    await self._write_mount_database(connection, ctx)
                except Exception:
                    if content is not None:
                        await self._service.fs.write(
                            node_uri,
                            content,
                            ctx=ctx,
                            mode="create",
                            wait=True,
                        )
                    raise
            finally:
                self._close_mount_database(connection)
        return {
            "node_uri": node_uri,
            "mcp": row["mcp_name"],
            "tool": row["tool_name"],
            "tool_uri": row["tool_uri"],
            "unmounted": True,
        }

    async def batch_unmount(
        self,
        node_uris: list[str],
        ctx: RequestContext,
        *,
        wait: bool = False,
    ) -> dict[str, Any]:
        if not node_uris:
            raise InvalidArgumentError("Batch unmount must contain at least one node URI")

        normalized_node_uris: list[str] = []
        seen_node_uris: set[str] = set()
        for raw_node_uri in node_uris:
            node_uri = self._validate_node_uri(raw_node_uri, ctx)
            if node_uri in seen_node_uris:
                raise InvalidArgumentError(f"Duplicate batch unmount node URI: {node_uri}")
            seen_node_uris.add(node_uri)
            normalized_node_uris.append(node_uri)

        async with self._lock(ctx.account_id):
            connection = await self._open_mount_database(ctx)
            try:
                rows = []
                contents: dict[str, str | None] = {}
                for node_uri in normalized_node_uris:
                    row = connection.execute(
                        "SELECT * FROM live_context_nodes WHERE node_uri = ?",
                        (node_uri,),
                    ).fetchone()
                    if row is None:
                        raise NotFoundError(node_uri, "live context mount")
                    rows.append(row)
                    try:
                        contents[node_uri] = await self._service.fs.read(node_uri, ctx=ctx)
                    except (AGFSNotFoundError, NotFoundError, FileNotFoundError):
                        contents[node_uri] = None

                deleted_node_uris: list[str] = []
                try:
                    for node_uri in normalized_node_uris:
                        await self._service.fs.rm(
                            node_uri,
                            ctx=ctx,
                            recursive=False,
                            wait=wait,
                        )
                        deleted_node_uris.append(node_uri)
                except Exception:
                    for node_uri in deleted_node_uris:
                        content = contents[node_uri]
                        if content is not None:
                            await self._service.fs.write(
                                node_uri,
                                content,
                                ctx=ctx,
                                mode="create",
                                wait=True,
                            )
                    raise
                connection.executemany(
                    "DELETE FROM live_context_nodes WHERE node_uri = ?",
                    [(node_uri,) for node_uri in normalized_node_uris],
                )
                try:
                    await self._write_mount_database(connection, ctx)
                except Exception:
                    for node_uri, content in contents.items():
                        if content is not None:
                            await self._service.fs.write(
                                node_uri,
                                content,
                                ctx=ctx,
                                mode="create",
                                wait=True,
                            )
                    raise
            finally:
                self._close_mount_database(connection)

        results = [
            {
                "node_uri": row["node_uri"],
                "mcp": row["mcp_name"],
                "tool": row["tool_name"],
                "tool_uri": row["tool_uri"],
                "unmounted": True,
            }
            for row in rows
        ]
        return {"unmounts": results, "total": len(results)}

    async def mounts(
        self,
        mcp_name: str,
        tool_name: str | None,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        mcp_name = self._validate_server_name(mcp_name)
        await self._registered(mcp_name, ctx)
        if tool_name is not None and not self._valid_tool_name(tool_name):
            raise InvalidArgumentError("Invalid MCP tool name")

        connection = await self._open_mount_database(ctx)
        try:
            query = "SELECT * FROM live_context_nodes WHERE mcp_name = ?"
            parameters: tuple[str, ...] = (mcp_name,)
            if tool_name is not None:
                query += " AND tool_name = ?"
                parameters = (mcp_name, tool_name)
            query += " ORDER BY tool_name, node_uri"
            rows = connection.execute(query, parameters).fetchall()
        finally:
            self._close_mount_database(connection)

        rows = await self._readable_mount_rows(rows, ctx)
        items = []
        for row in rows:
            data = json.loads(row["data"])
            items.append(
                {
                    "node_uri": row["node_uri"],
                    "mcp": row["mcp_name"],
                    "tool": row["tool_name"],
                    "tool_uri": row["tool_uri"],
                    "input_revision": row["input_revision"],
                    "arguments": json.loads(row["args_json"]),
                    "title": data["title"],
                    "description": data.get("description"),
                    "timeout_seconds": data["timeout_seconds"],
                    "tags": data.get("tags", []),
                    "created_at": row["created_at"],
                }
            )
        return {"mounts": items, "total": len(items)}

    @staticmethod
    def _parse_live_context_node(content: str) -> tuple[str, dict[str, Any], int] | None:
        lines = content.splitlines()
        if not lines or lines[0] != "---":
            return None
        try:
            closing_index = lines[1:].index("---") + 1
            metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
        except (ValueError, yaml.YAMLError):
            return None
        if not isinstance(metadata, dict) or metadata.get("type") != "OpenViking Live Context":
            return None
        extension = metadata.get("x-openviking")
        if not isinstance(extension, dict) or extension.get("type") != "mcp":
            return None
        tool_uri = extension.get("definition")
        arguments = extension.get("args", {})
        timeout_seconds = extension.get("timeout_seconds", 60)
        if (
            not isinstance(tool_uri, str)
            or not isinstance(arguments, dict)
            or not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise InvalidArgumentError(
                "Live context node requires string x-openviking.definition "
                "object x-openviking.args, and positive integer "
                "x-openviking.timeout_seconds"
            )
        return tool_uri, arguments, timeout_seconds

    async def invoke_live_context(
        self,
        content: str,
        ctx: RequestContext,
    ) -> dict[str, Any] | None:
        invocation = self._parse_live_context_node(content)
        if invocation is None:
            return None
        tool_uri, arguments, timeout_seconds = invocation
        return await self.invoke(
            tool_uri,
            arguments,
            ctx,
            timeout_seconds=timeout_seconds,
        )

    async def read_live_context(
        self,
        content: str,
        ctx: RequestContext,
    ) -> str | None:
        invocation = self._parse_live_context_node(content)
        if invocation is None:
            return None
        tool_uri, arguments, timeout_seconds = invocation
        result = await self.invoke(
            tool_uri,
            arguments,
            ctx,
            timeout_seconds=timeout_seconds,
            require_read_only=True,
        )
        return self._live_context_read_value(result, tool_uri)

    @staticmethod
    def _live_context_read_value(
        result: dict[str, Any],
        tool_uri: str,
    ) -> str:
        content = result.get("content")
        text_blocks = (
            [
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            if isinstance(content, list)
            else []
        )

        if result.get("isError") is True:
            detail = "\n".join(text_blocks).strip() or "upstream MCP tool returned an error"
            raise ProcessingError(
                f"Live context read failed: {detail}",
                source=tool_uri,
            )

        if isinstance(content, list):
            if text_blocks:
                return "\n".join(text_blocks)

        structured = result.get("structuredContent")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, indent=2)

        if isinstance(content, list) and not content:
            return ""

        raise ProcessingError(
            "Live context read only supports text or structured MCP results",
            source=tool_uri,
        )

    async def invoke(
        self,
        uri: str,
        arguments: dict[str, Any],
        ctx: RequestContext,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        *,
        require_read_only: bool = False,
    ) -> dict[str, Any]:
        match = _TOOL_URI_RE.fullmatch(uri)
        if match is None:
            raise InvalidArgumentError(
                "Tool URI must match viking://agent/tools/<server>/<tool>.json"
            )
        server_name = match.group("server")
        tool_name = match.group("tool")
        server, headers = await self._registered(server_name, ctx)
        if tool_name not in await self._catalog_names(server_name, ctx):
            raise NotFoundError(uri, "MCP tool")
        tool_document = await self._tool(uri, ctx)
        if require_read_only and not self._tool_is_read_only(tool_document):
            invocation = json.dumps(
                {"tool_uri": uri, "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
            )
            raise ProcessingError(
                "Live context read cannot execute a tool explicitly marked as "
                "non-read-only or destructive. Invoke the MCP tool explicitly with "
                f"{invocation}",
                source=uri,
            )
        self._validate_tool_arguments(tool_document, arguments)

        async with self._session(server, headers, timeout_seconds) as session:
            await session.initialize()
            try:
                result: CallToolResult = await session.call_tool(
                    tool_name,
                    arguments=arguments,
                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                )
            except Exception as exc:
                raise UnavailableError(
                    "MCP tool",
                    f"call failed ({type(exc).__name__}); outcome may be unknown and was not retried",
                ) from exc
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
