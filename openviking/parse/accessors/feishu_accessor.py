# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Feishu/Lark Accessor.

Fetches Feishu/Lark cloud documents using the lark-oapi SDK.

Note: This accessor requires the `lark-oapi` package.
Included by default in `openviking[bot]` installation.
"""

import asyncio
import json
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

from openviking.parse.base import format_table_to_markdown
from openviking.utils.exceptions import error_code_from_http_status
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils.logger import get_logger

from .base import DataAccessor, LocalResource, SourceType
from .mime_types import get_preferred_extension

logger = get_logger(__name__)

_FEISHU_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(feishu://image/([^)]+)\)")
_FEISHU_DOCUMENT_FORBIDDEN = 1770032
_FEISHU_BITABLE_PERMISSION_REQUIRED = 99991672
_MAX_MEDIA_DOWNLOAD_CONTEXTS = 8

_MediaDownloadExtras = Dict[str, List[Optional[str]]]


def _title_as_filename(title: str) -> str:
    """Keep a Feishu display title intact while making it one filename segment.

    Feishu titles may contain path separators.  ``original_filename`` is passed
    through filename-oriented helpers downstream, so leaving separators in that
    field makes ``Path(...).name`` silently discard the title prefix.
    """
    return title.replace("/", "_").replace("\\", "_")


def _getattr_safe(obj, key: str, default=None):
    """Get attribute from SDK object or dict, with safe fallback."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _response_http_status(response: Any) -> int | None:
    status = getattr(getattr(response, "raw", None), "status_code", None)
    return status if isinstance(status, int) else None


def _raise_from_lark_response(
    response: Any,
    *,
    operation: str,
    resource: str | None = None,
) -> NoReturn:
    code = getattr(response, "code", None)
    msg = getattr(response, "msg", None) or "Feishu API request failed"
    http_status = _response_http_status(response)
    details: dict[str, Any] = {
        "operation": operation,
        "feishu_code": code,
        "feishu_msg": msg,
        "http_status": http_status,
    }
    if resource:
        details["resource"] = resource

    logger.error(
        "[FeishuAPI] %s failed: code=%s msg=%s http=%s",
        operation,
        code,
        msg,
        http_status,
    )
    if code == _FEISHU_BITABLE_PERMISSION_REQUIRED:
        public_code = "FAILED_PRECONDITION"
        message = (
            f"Feishu application is missing required Bitable permissions: code={code}, msg={msg}"
        )
    else:
        public_code = (
            "PERMISSION_DENIED"
            if code == _FEISHU_DOCUMENT_FORBIDDEN
            else error_code_from_http_status(http_status)
        )
        message = f"Feishu {operation} failed: code={code}, msg={msg}"

    raise OpenVikingError(message, code=public_code, details=details)


@dataclass
class FeishuDocument:
    """Result from fetching a Feishu document."""

    doc_type: str
    token: str
    markdown_content: str
    title: str
    meta: Dict[str, Any]
    media_download_extras: _MediaDownloadExtras = field(default_factory=dict)


class FeishuAccessor(DataAccessor):
    """
    Accessor for Feishu/Lark cloud documents.

    Supports:
    - Documents: https://*.feishu.cn/docx/{document_id}
    - Wiki pages: https://*.feishu.cn/wiki/{token}
    - Spreadsheets: https://*.feishu.cn/sheets/{token}
    - Bitable: https://*.feishu.cn/base/{app_token}

    Requires:
    - lark-oapi package
    - FEISHU_APP_ID and FEISHU_APP_SECRET environment variables, or
      configuration in ov.conf, for app-token imports. One-time user-token
      imports can pass feishu_access_token instead.
    """

    PRIORITY = 100  # Higher than Git/HTTP, very specific

    # Wiki obj_type normalization (API returns short names)
    _WIKI_TYPE_MAP = {"doc": "docx", "sheet": "sheets", "bitable": "base"}
    _DOC_TYPE_HANDLERS = {
        "docx": "_parse_docx",
        "sheets": "_parse_sheets",
        "base": "_parse_bitable",
    }

    # Attributes that skip processing (structural containers or metadata)
    _SKIP_ATTRS = {"page", "table_cell", "quote_container", "grid", "grid_column"}

    # Attribute → special handler method (non-text blocks)
    _SPECIAL_BLOCK_HANDLERS = {
        "divider": "_handle_divider",
        "image": "_handle_image",
        "table": "_table_block_to_markdown",
        "sheet": "_embedded_sheet_to_markdown",
    }

    # Attribute → markdown prefix template for text-bearing blocks.
    # "{text}" is replaced with extracted text content.
    # Headings are handled dynamically (heading1-heading9 → # through #########).
    _TEXT_FORMAT = {
        "bullet": "- {text}",
        "quote": "> {text}",
    }

    # Known block_type integer → SDK attribute name mapping.
    # Primary dispatch mechanism for reliable block detection.
    # Source: Feishu OpenAPI documentation + lark-oapi SDK Block class.
    _BLOCK_TYPE_TO_ATTR = {
        1: "page",
        2: "text",
        3: "heading1",
        4: "heading2",
        5: "heading3",
        6: "heading4",
        7: "heading5",
        8: "heading6",
        9: "heading7",
        10: "heading8",
        11: "heading9",
        12: "bullet",
        13: "ordered",
        14: "code",
        15: "quote",
        17: "todo",
        19: "callout",
        22: "divider",
        27: "image",
        30: "sheet",
        31: "table",
        32: "table_cell",
        34: "quote_container",
    }

    # All known content attribute names on SDK Block objects (for fallback detection).
    _KNOWN_CONTENT_ATTRS = frozenset(
        {
            "page",
            "text",
            "heading1",
            "heading2",
            "heading3",
            "heading4",
            "heading5",
            "heading6",
            "heading7",
            "heading8",
            "heading9",
            "bullet",
            "ordered",
            "code",
            "quote",
            "todo",
            "callout",
            "divider",
            "image",
            "sheet",
            "table",
            "table_cell",
            "quote_container",
            "equation",
            "task",
            "grid",
            "grid_column",
        }
    )

    def __init__(self):
        """Initialize Feishu accessor."""
        self._client = None
        self._user_token_client = None
        self._config = None

    @property
    def priority(self) -> int:
        return self.PRIORITY

    def can_handle(self, source: Union[str, Path], **kwargs) -> bool:
        """
        Check if this accessor can handle the source.

        Handles Feishu/Lark cloud document URLs.
        """
        source_str = str(source)

        # Only handle http/https URLs
        if not source_str.startswith(("http://", "https://")):
            return False

        return self._is_feishu_url(source_str)

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """
        Fetch a Feishu document and save to a temporary Markdown file.

        Args:
            source: Feishu document URL
            **kwargs: Additional arguments

        Returns:
            LocalResource pointing to the temporary Markdown file
        """
        source_str = str(source)
        feishu_access_token = kwargs.get("feishu_access_token")

        try:
            # Fetch the document and convert to Markdown
            doc = await self._fetch_document(
                source_str,
                feishu_access_token=feishu_access_token,
            )

            # lark-oapi media downloads are synchronous; run them off the event
            # loop so a slow Feishu request cannot block unrelated async work.
            markdown_content, downloaded_images = await asyncio.to_thread(
                self._resolve_image_refs,
                doc.markdown_content,
                feishu_access_token=feishu_access_token,
                media_download_extras=doc.media_download_extras,
            )

            # Build metadata
            meta = {
                "feishu_doc_type": doc.doc_type,
                "feishu_token": doc.token,
                "feishu_title": doc.title,
                "original_filename": _title_as_filename(doc.title),
                **doc.meta,
            }

            if downloaded_images:
                temp_dir = Path(tempfile.mkdtemp(prefix="ov_feishu_"))
                markdown_path = temp_dir / "document.md"
                markdown_path.write_text(markdown_content, encoding="utf-8")
                for rel_path, image_bytes in downloaded_images.items():
                    image_path = temp_dir / rel_path
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    image_path.write_bytes(image_bytes)
                meta["_cleanup_path"] = str(temp_dir)
                local_path = markdown_path
            else:
                # Create temporary file
                temp_file = tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    prefix="ov_feishu_",
                    delete=False,
                    encoding="utf-8",
                )
                temp_file.write(markdown_content)
                temp_file.close()
                local_path = Path(temp_file.name)

            return LocalResource(
                path=local_path,
                source_type=SourceType.FEISHU,
                original_source=source_str,
                meta=meta,
                is_temporary=True,
            )

        except Exception as e:
            logger.error(f"[FeishuAccessor] Failed to access {source}: {e}", exc_info=True)
            raise

    async def _fetch_document(
        self,
        url: str,
        *,
        feishu_access_token: Optional[str] = None,
    ) -> FeishuDocument:
        """
        Fetch a Feishu document and convert to Markdown.

        The fetched document is materialized as Markdown for the standard parser chain.
        """
        doc_type, token = self._parse_feishu_url(url)
        query = parse_qs(urlparse(url).query)
        table_id = (query.get("table") or [None])[0]
        view_id = (query.get("view") or [None])[0]
        title = None
        meta = {}
        media_download_extras: _MediaDownloadExtras = {}

        if doc_type == "wiki":
            # Resolve wiki node to actual document type
            real_type, real_token, title = await asyncio.to_thread(
                self._resolve_wiki_node,
                token,
                feishu_access_token,
            )
            doc_type, token = real_type, real_token
            meta["wiki_resolved"] = True

        if doc_type != "base":
            table_id = view_id = None

        handler_name = self._DOC_TYPE_HANDLERS.get(doc_type)
        if handler_name is None:
            raise ValueError(
                f"Unsupported Feishu document type: {doc_type}. "
                f"Supported: {list(self._DOC_TYPE_HANDLERS)}"
            )

        handler_kwargs = {}
        if doc_type == "base":
            handler_kwargs = {
                "table_id": table_id,
                "view_id": view_id,
                "media_download_extras": media_download_extras,
            }
        elif doc_type == "sheets":
            handler_kwargs = {"media_download_extras": media_download_extras}

        # Feishu's SDK is synchronous; keep it off the event loop.
        markdown, doc_title = await asyncio.to_thread(
            getattr(self, handler_name),
            token,
            feishu_access_token,
            **handler_kwargs,
        )

        if title:
            scope = "/".join(value for value in (table_id, view_id) if value)
            doc_title = f"{title} ({scope})" if scope else title

        meta["original_url"] = url
        if table_id:
            meta["feishu_table_id"] = table_id
        if view_id:
            meta["feishu_view_id"] = view_id

        return FeishuDocument(
            doc_type=doc_type,
            token=token,
            markdown_content=markdown,
            title=doc_title,
            meta=meta,
            media_download_extras=media_download_extras,
        )

    @staticmethod
    def _is_feishu_url(url: str) -> bool:
        """Check if URL is a Feishu/Lark cloud document."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path
        is_feishu_domain = any(
            host == allowed_host or host.endswith(f".{allowed_host}")
            for allowed_host in ("feishu.cn", "larksuite.com", "larkoffice.com")
        )
        has_doc_path = any(
            path == f"/{t}" or path.startswith(f"/{t}/") for t in ("docx", "wiki", "sheets", "base")
        )
        return is_feishu_domain and has_doc_path

    @staticmethod
    def _parse_feishu_url(url: str) -> Tuple[str, str]:
        """
        Extract doc_type and token from Feishu URL.

        Returns:
            (doc_type, token) e.g. ("docx", "doxcnABC123")
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            raise ValueError(f"Cannot parse Feishu URL: {url}")
        doc_type = path_parts[0]  # docx, wiki, sheets, base
        token = path_parts[1]
        return doc_type, token

    # ========== Configuration & Client ==========

    def _get_config(self):
        """Get FeishuConfig from OpenViking config."""
        if self._config is None:
            from openviking_cli.utils.config import get_openviking_config

            self._config = get_openviking_config().feishu
        return self._config

    def _get_client(self, *, use_user_token: bool = False):
        """Lazy-init lark-oapi client."""
        cache_attr = "_user_token_client" if use_user_token else "_client"
        client = getattr(self, cache_attr)
        if client is None:
            try:
                import lark_oapi as lark
            except ImportError:
                raise ImportError(
                    "lark-oapi is required for Feishu document parsing. "
                    "Install it with: pip install lark-oapi>=1.0.0"
                )
            config = self._get_config()
            app_id = config.app_id or os.getenv("FEISHU_APP_ID", "")
            app_secret = config.app_secret or os.getenv("FEISHU_APP_SECRET", "")
            if (not app_id or not app_secret) and not use_user_token:
                raise ValueError(
                    "Feishu credentials not configured. Set FEISHU_APP_ID and "
                    "FEISHU_APP_SECRET environment variables, or configure in ov.conf."
                )
            domain = config.domain or "https://open.feishu.cn"
            builder = lark.Client.builder().domain(domain)
            if app_id and app_secret:
                builder = builder.app_id(app_id).app_secret(app_secret)
            if use_user_token:
                builder = builder.enable_set_token(True)
            client = builder.build()
            setattr(self, cache_attr, client)
        return client

    @staticmethod
    def _user_request_option(feishu_access_token: Optional[str]):
        if not feishu_access_token:
            return None
        from lark_oapi.core.model import RequestOption

        return RequestOption.builder().user_access_token(feishu_access_token).build()

    def _call_api(self, method, request, feishu_access_token: Optional[str] = None):
        option = self._user_request_option(feishu_access_token)
        return method(request) if option is None else method(request, option)

    # ========== Wiki Resolution ==========

    def _resolve_wiki_node(
        self,
        token: str,
        feishu_access_token: Optional[str] = None,
    ) -> Tuple[str, str, Optional[str]]:
        """
        Resolve wiki token to actual document type, token, and title.

        Returns:
            (doc_type, obj_token, title)
        """
        from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

        client = self._get_client(use_user_token=bool(feishu_access_token))
        request = GetNodeSpaceRequest.builder().token(token).build()
        response = self._call_api(
            client.wiki.v2.space.get_node,
            request,
            feishu_access_token,
        )
        if not response.success():
            _raise_from_lark_response(
                response,
                operation=f"resolve wiki node {token}",
                resource=token,
            )
        node = response.data.node
        obj_type = node.obj_type or ""
        obj_token = node.obj_token or ""
        title = node.title

        # Normalize type names
        doc_type = self._WIKI_TYPE_MAP.get(obj_type, obj_type)

        return doc_type, obj_token, title

    # ========== Wiki Space / Directory Batch Expansion ==========
    #
    # Issue #3120: a Feishu "wiki space settings" URL
    # (``https://*.feishu.cn/wiki/settings/<space_id>``) or a wiki node URL that
    # has children should be expanded into one import per descendant document.
    # The methods below reuse the same lark-oapi client + auth as ``_resolve_wiki_node``
    # and walk the wiki tree via ``client.wiki.v2.space.list`` (paginated).

    # Safety rails so a pathological space (cycles, very wide trees) cannot stall
    # the importer. ``WIKI_BATCH_*`` constants are the defaults; callers can pass
    # tighter limits via ``expand_feishu_url``.
    WIKI_BATCH_DEFAULT_MAX_DEPTH = 5
    WIKI_BATCH_DEFAULT_MAX_NODES = 500
    WIKI_BATCH_PAGE_SIZE = 50

    @staticmethod
    def classify_url(url: str) -> Optional[Tuple[str, str, Optional[str]]]:
        """Classify a Feishu URL for batch-import dispatch.

        Returns ``None`` for non-Feishu URLs. Otherwise returns a 3-tuple
        ``(kind, primary_token, secondary_token)`` where:

        * ``("single_doc", doc_type, token)`` — always a single document import.
          Covers ``/docx/<t>``, ``/sheets/<t>``, ``/base/<t>``.
        * ``("wiki_node", node_token, None)`` — a ``/wiki/<token>`` URL. May be a
          leaf document or a directory; the caller resolves ``has_child`` via the
          wiki API to decide.
        * ``("wiki_space_root", space_id, None)`` — a ``/wiki/settings/<space_id>``
          URL that lists every node in the space.
        """
        if not FeishuAccessor._is_feishu_url(url):
            return None
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            return None
        first, second = path_parts[0], path_parts[1]
        if first == "wiki" and second == "settings":
            # /wiki/settings/<space_id>(/...) — space root.
            if len(path_parts) < 3:
                return None
            return ("wiki_space_root", path_parts[2], None)
        if first == "wiki":
            # /wiki/<node_token> — may be a leaf doc or a directory.
            return ("wiki_node", second, None)
        # /docx/<t>, /sheets/<t>, /base/<t> — always single-doc.
        return ("single_doc", first, second)

    @staticmethod
    def is_batch_url(url: str) -> bool:
        """Return True if ``url`` definitely triggers batch import.

        A ``/wiki/settings/<space_id>`` URL always batches. A ``/wiki/<token>``
        URL *may* batch (depending on ``has_child``) so this helper returns False
        for it — the caller must run ``expand_feishu_url`` to find out.
        """
        classified = FeishuAccessor.classify_url(url)
        return classified is not None and classified[0] == "wiki_space_root"

    def _list_wiki_child_nodes(
        self,
        space_id: str,
        parent_node_token: Optional[str],
        *,
        feishu_access_token: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> Tuple[List["FeishuAccessor._WikiNode"], bool, Optional[str]]:
        """List the direct children of a wiki node, following pagination.

        Returns ``(nodes, has_more, next_page_token)``. ``nodes`` may be empty
        when the node is a leaf. Pure SDK call — wrap in ``asyncio.to_thread``
        from async callers.
        """
        from lark_oapi.api.wiki.v2 import ListSpaceNodeRequest

        client = self._get_client(use_user_token=bool(feishu_access_token))
        builder = ListSpaceNodeRequest.builder().space_id(space_id)
        if parent_node_token:
            builder = builder.parent_node_token(parent_node_token)
        if page_token:
            builder = builder.page_token(page_token)
        builder = builder.page_size(page_size or self.WIKI_BATCH_PAGE_SIZE)
        request = builder.build()
        response = self._call_api(client.wiki.v2.space.list, request, feishu_access_token)
        if not response.success():
            _raise_from_lark_response(
                response,
                operation=(
                    f"list wiki nodes space={space_id} "
                    f"parent={parent_node_token or '<root>'}"
                ),
                resource=space_id,
            )
        data = getattr(response, "data", None)
        raw_items = getattr(data, "items", None) or []
        nodes = [self._wiki_node_from_sdk(item) for item in raw_items]
        has_more = bool(getattr(data, "has_more", False))
        next_page_token = getattr(data, "page_token", None)
        return nodes, has_more, next_page_token

    @staticmethod
    def _wiki_node_from_sdk(item: Any) -> "FeishuAccessor._WikiNode":
        """Build a ``_WikiNode`` from a lark-oapi ``Node`` (SDK object or dict)."""
        return FeishuAccessor._WikiNode(
            node_token=_getattr_safe(item, "node_token", "") or "",
            obj_token=_getattr_safe(item, "obj_token", "") or "",
            obj_type=_getattr_safe(item, "obj_type", "") or "",
            title=_getattr_safe(item, "title", "") or "",
            has_child=bool(_getattr_safe(item, "has_child", False)),
            space_id=str(_getattr_safe(item, "space_id", "") or ""),
            parent_node_token=_getattr_safe(item, "parent_node_token", "") or None,
            url=_getattr_safe(item, "url", "") or "",
        )

    @dataclass
    class _WikiNode:
        """Resolved view of a wiki node used during subtree traversal."""
        node_token: str
        obj_token: str
        obj_type: str  # raw API type: docx/doc/sheet/bitable/wiki/...
        title: str
        has_child: bool
        space_id: str
        parent_node_token: Optional[str]
        url: str = ""

    def _resolve_wiki_node_full(
        self,
        node_token: str,
        *,
        feishu_access_token: Optional[str] = None,
    ) -> "_WikiNode":
        """Resolve a wiki node token to its full node info (incl. ``has_child``).

        Uses the same ``get_node`` API as ``_resolve_wiki_node`` but returns the
        extra fields the batch importer needs.
        """
        from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

        client = self._get_client(use_user_token=bool(feishu_access_token))
        request = GetNodeSpaceRequest.builder().token(node_token).build()
        response = self._call_api(
            client.wiki.v2.space.get_node,
            request,
            feishu_access_token,
        )
        if not response.success():
            _raise_from_lark_response(
                response,
                operation=f"resolve wiki node {node_token}",
                resource=node_token,
            )
        node = getattr(response.data, "node", None)
        return self._wiki_node_from_sdk(node)

    async def list_wiki_subtree(
        self,
        *,
        space_id: Optional[str] = None,
        root_node_token: Optional[str] = None,
        feishu_access_token: Optional[str] = None,
        max_depth: int = WIKI_BATCH_DEFAULT_MAX_DEPTH,
        max_nodes: int = WIKI_BATCH_DEFAULT_MAX_NODES,
    ) -> List[Tuple[str, str]]:
        """Walk a Feishu wiki subtree and return ``(doc_url, title)`` tuples.

        Exactly one of ``space_id`` (whole space) or ``root_node_token`` (a single
        wiki node + its descendants) must be provided. ``space_id`` lists every
        top-level node via ``space.list``; ``root_node_token`` first resolves the
        node via ``get_node`` and then lists its children.

        Bounds: ``max_depth`` caps recursion (the root counts as depth 0);
        ``max_nodes`` caps the total returned documents. Both bounds are enforced
        best-effort — when hit, traversal stops and a warning is logged.
        """
        if (space_id is None) == (root_node_token is None):
            raise ValueError(
                "list_wiki_subtree requires exactly one of space_id or root_node_token."
            )

        results: List[Tuple[str, str]] = []
        # ``seen_tokens`` breaks accidental cycles (parent_token loops in the API
        # response or shared subtrees).
        seen_tokens: set[str] = set()
        truncated = False

        def _record(node: "FeishuAccessor._WikiNode") -> bool:
            """Append a node's doc URL. Return False if the cap is hit.

            Nodes whose ``obj_type`` is not a parseable document type are skipped
            for emission (their URL would be unparseable), but the caller still
            recurses into them when ``has_child`` is set so folders of unknown
            type are walked.
            """
            nonlocal truncated
            if not node.obj_token or node.obj_token in seen_tokens:
                return True
            doc_url = self._build_doc_url(node)
            if doc_url is None:
                logger.debug(
                    "[FeishuAccessor] skipping wiki node %s with unsupported obj_type=%r",
                    node.node_token,
                    node.obj_type,
                )
                return True
            if len(results) >= max_nodes:
                truncated = True
                return False
            seen_tokens.add(node.obj_token)
            results.append((doc_url, node.title or ""))
            return True

        async def _walk(
            space: str,
            parent_node_token: Optional[str],
            depth: int,
        ) -> None:
            nonlocal truncated
            if depth >= max_depth:
                logger.warning(
                    "[FeishuAccessor] wiki subtree max_depth=%d hit at node=%s; "
                    "stopping recursion (increase to fetch more).",
                    max_depth,
                    parent_node_token or "<root>",
                )
                return
            page_token: Optional[str] = None
            while True:
                if len(results) >= max_nodes:
                    truncated = True
                    return
                nodes, has_more, next_page_token = await asyncio.to_thread(
                    self._list_wiki_child_nodes,
                    space,
                    parent_node_token,
                    feishu_access_token=feishu_access_token,
                    page_token=page_token,
                )
                for node in nodes:
                    if not _record(node):
                        return
                    if node.has_child and node.node_token:
                        await _walk(space, node.node_token, depth + 1)
                        if truncated:
                            return
                if not has_more or not next_page_token:
                    return
                if next_page_token == page_token:
                    # Defensive: avoid spinning on an unchanging page token.
                    return
                page_token = next_page_token

        if space_id:
            await _walk(space_id, None, 0)
        else:
            assert root_node_token is not None
            root = await asyncio.to_thread(
                self._resolve_wiki_node_full,
                root_node_token,
                feishu_access_token=feishu_access_token,
            )
            _record(root)
            if root.has_child and root.node_token and not truncated:
                # ``space_id`` from the node so child listings use the right space.
                await _walk(root.space_id or "", root.node_token, 1)

        if truncated:
            logger.warning(
                "[FeishuAccessor] wiki subtree truncated at max_nodes=%d "
                "(space=%s root=%s).",
                max_nodes,
                space_id,
                root_node_token,
            )
        return results

    @staticmethod
    def _build_doc_url(node: "FeishuAccessor._WikiNode") -> Optional[str]:
        """Build a canonical single-doc URL for a wiki node, or None if unparseable.

        Returns ``None`` for ``obj_type`` values outside ``_DOC_TYPE_HANDLERS``
        so the caller can skip emission while still recursing into the node's
        children. For parseable types we emit a *direct* doc URL
        (``/docx/<obj_token>``, ``/sheets/<obj_token>``, ``/base/<obj_token>``)
        so re-importing the child cannot re-trigger batch expansion (those URL
        kinds are classified as ``single_doc``).
        """
        doc_type = FeishuAccessor._WIKI_TYPE_MAP.get(node.obj_type, node.obj_type)
        if doc_type not in FeishuAccessor._DOC_TYPE_HANDLERS:
            return None
        if not node.obj_token:
            return None
        return f"https://feishu.cn/{doc_type}/{node.obj_token}"

    async def expand_feishu_url(
        self,
        url: str,
        *,
        feishu_access_token: Optional[str] = None,
        max_depth: int = WIKI_BATCH_DEFAULT_MAX_DEPTH,
        max_nodes: int = WIKI_BATCH_DEFAULT_MAX_NODES,
    ) -> List[Tuple[str, str]]:
        """Expand a Feishu URL into the list of documents to import.

        Returns a list of ``(doc_url, title)`` tuples:

        * Single-document URLs (``/docx/<t>``, ``/sheets/<t>``, ``/base/<t>``)
          always return ``[(url, "")]`` — single-doc imports are unchanged.
        * ``/wiki/settings/<space_id>`` returns every document in the space;
          an empty list means the space has no importable documents.
        * ``/wiki/<token>`` resolves the node: if it has children, returns the
          node itself plus every descendant (empty list if the subtree has no
          importable documents); otherwise returns ``[(url, "")]`` (unchanged
          single-doc behavior).

        A return of ``[]`` therefore means "recognised batch source with nothing
        to import" — the caller must surface this rather than fall back to a
        single-doc import (the original URL is not a valid doc URL).

        Raising on API failure is intentional — the caller surfaces the error
        instead of silently degrading to a single-doc import.
        """
        classified = self.classify_url(url)
        if classified is None:
            # Not a Feishu URL — let the caller handle it (existing path).
            return [(url, "")]
        kind, primary, _secondary = classified
        if kind == "single_doc":
            return [(url, "")]
        if kind == "wiki_space_root":
            children = await self.list_wiki_subtree(
                space_id=primary,
                feishu_access_token=feishu_access_token,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
            # Empty list is meaningful: caller surfaces "no documents" rather
            # than trying to import the /wiki/settings/<id> URL as a doc.
            return children
        if kind == "wiki_node":
            node = await asyncio.to_thread(
                self._resolve_wiki_node_full,
                primary,
                feishu_access_token=feishu_access_token,
            )
            if not node.has_child:
                return [(url, "")]
            children = await self.list_wiki_subtree(
                root_node_token=primary,
                feishu_access_token=feishu_access_token,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
            # ``list_wiki_subtree`` always records the root first, so an empty
            # result here means the root itself was deduped/filtered — surface
            # it as "no documents" rather than re-importing the wiki URL.
            return children
        # Defensive — classify_url only returns the kinds above.
        return [(url, "")]

    # ========== Docx Parsing ==========

    def _parse_docx(
        self,
        document_id: str,
        feishu_access_token: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Fetch all blocks and convert to Markdown.

        Returns:
            (markdown_content, document_title)
        """
        blocks = self._fetch_all_blocks(
            document_id,
            feishu_access_token=feishu_access_token,
        )
        if not blocks:
            return "", "Untitled"

        # Build block lookup by block_id
        block_map = {b.block_id: b for b in blocks}

        # Find title from page block
        doc_title = "Untitled"
        for b in blocks:
            if b.page is not None:
                if b.page.elements:
                    doc_title = self._extract_text_from_elements(b.page.elements)
                break

        # Convert blocks to markdown
        markdown_lines = []
        ordered_counter: Dict[str, int] = {}

        for block in blocks:
            if block.page is not None:
                continue  # Skip page container

            line = self._block_to_markdown(
                block,
                block_map,
                ordered_counter,
                document_id=document_id,
                feishu_access_token=feishu_access_token,
            )
            if line is not None:
                markdown_lines.append(line)

        markdown = "\n\n".join(markdown_lines)

        if doc_title and doc_title != "Untitled":
            markdown = f"# {doc_title}\n\n{markdown}"

        return markdown, doc_title

    def _fetch_all_blocks(
        self,
        document_id: str,
        *,
        feishu_access_token: Optional[str] = None,
    ) -> list:
        """Fetch all blocks with pagination. Returns list of SDK block objects."""
        from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

        client = self._get_client(use_user_token=bool(feishu_access_token))
        all_blocks = []
        page_token = None

        while True:
            builder = (
                ListDocumentBlockRequest.builder()
                .document_id(document_id)
                .page_size(500)
                .document_revision_id(-1)
            )
            if page_token:
                builder = builder.page_token(page_token)

            request = builder.build()
            response = self._call_api(
                client.docx.v1.document_block.list,
                request,
                feishu_access_token,
            )

            if not response.success():
                _raise_from_lark_response(
                    response,
                    operation=f"fetch blocks for {document_id}",
                    resource=document_id,
                )

            items = response.data.items or []
            all_blocks.extend(items)

            if not response.data.has_more:
                break
            page_token = response.data.page_token

        return all_blocks

    # ========== Block -> Markdown Conversion ==========

    def _detect_block_attr(self, block) -> Optional[str]:
        """Detect which content attribute is populated on a block object.

        Uses block_type integer as the primary dispatch (reliable), falling
        back to attribute inspection over a known whitelist for unknown types.
        """
        # Primary: lookup by block_type integer
        block_type = getattr(block, "block_type", None)
        if block_type is not None:
            attr = self._BLOCK_TYPE_TO_ATTR.get(block_type)
            if attr:
                return attr

        # Fallback: scan known content attributes for unknown block types
        for attr in self._KNOWN_CONTENT_ATTRS:
            if getattr(block, attr, None) is not None:
                return attr
        return None

    def _block_to_markdown(
        self,
        block,
        block_map: Dict,
        ordered_counter: Dict[str, int],
        document_id: str = "",
        feishu_access_token: Optional[str] = None,
    ) -> Optional[str]:
        """Convert a single SDK block object to markdown string.

        Uses block_type integer for primary dispatch, with attribute whitelist
        fallback for unknown types. Formatting is data-driven via _TEXT_FORMAT
        and _SPECIAL_BLOCK_HANDLERS tables.
        """
        attr = self._detect_block_attr(block)

        if attr is None:
            return None

        # Skip structural containers (processed via their children)
        if attr in self._SKIP_ATTRS:
            return None

        # Reset ordered list counter when any non-ordered block appears
        if attr != "ordered":
            parent_id = block.parent_id or ""
            if parent_id in ordered_counter:
                del ordered_counter[parent_id]

        # Special blocks (non-text: divider, image, table)
        special_handler = self._SPECIAL_BLOCK_HANDLERS.get(attr)
        if special_handler:
            return getattr(self, special_handler)(
                block,
                block_map,
                document_id=document_id,
                feishu_access_token=feishu_access_token,
            )

        # --- Text-bearing blocks: extract elements, apply formatting ---
        content_obj = getattr(block, attr, None)
        if not content_obj or not hasattr(content_obj, "elements") or not content_obj.elements:
            return None

        text = self._extract_text_from_elements(content_obj.elements)
        if not text:
            return None

        # Headings: heading1 -> #, heading2 -> ##, ...
        if attr.startswith("heading"):
            level = int(attr.replace("heading", "") or "1")
            return f"{'#' * level} {text}"

        # Ordered list (needs counter state)
        if attr == "ordered":
            parent_id = block.parent_id or ""
            counter = ordered_counter.get(parent_id, 0) + 1
            ordered_counter[parent_id] = counter
            return f"{counter}. {text}"

        # Code block (needs language from style)
        if attr == "code":
            lang = ""
            if hasattr(content_obj, "style") and content_obj.style:
                lang = str(getattr(content_obj.style, "language", "") or "")
            return f"```{lang}\n{text}\n```"

        # Todo (needs done state from style)
        if attr == "todo":
            done = False
            if hasattr(content_obj, "style") and content_obj.style:
                done = getattr(content_obj.style, "done", False)
            checkbox = "[x]" if done else "[ ]"
            return f"- {checkbox} {text}"

        # Simple template formatting (bullet, quote, etc.)
        fmt = self._TEXT_FORMAT.get(attr)
        if fmt:
            return fmt.format(text=text)

        # Default: return plain text (covers callout, equation, task, unknown, etc.)
        return text

    @staticmethod
    def _handle_divider(block, block_map: Dict = None, **_) -> str:
        """Convert divider block to markdown."""
        return "---"

    @staticmethod
    def _handle_image(block, block_map: Dict = None, **_) -> Optional[str]:
        """Convert image block to markdown."""
        image = block.image
        if not image:
            return None
        file_token = image.token or ""
        alt_text = getattr(image, "alt", "") or "image"
        return f"![{alt_text}](feishu://image/{file_token})"

    # Image byte-magic signatures → file extension. Sniffed from the raw bytes
    # first, since the actual content is authoritative over a (possibly generic
    # or wrong) Content-Type header.
    _IMAGE_MAGIC = (
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"\xff\xd8\xff", ".jpg"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"BM", ".bmp"),
    )

    @classmethod
    def _guess_image_ext(cls, content: bytes, content_type: Optional[str]) -> str:
        """Infer an image file extension from the bytes, then Content-Type.

        Feishu media are not guaranteed to be PNG, so we avoid a hardcoded
        extension that would misrepresent JPEG/WebP/GIF bytes to downstream
        consumers (e.g. emitting JPEG bytes as ``data:image/png``). Byte magic
        is checked first because the payload is authoritative; the response
        Content-Type is only a fallback for formats we do not sniff here.
        """
        # WebP: "RIFF....WEBP"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return ".webp"
        for magic, ext in cls._IMAGE_MAGIC:
            if content.startswith(magic):
                return ext
        if content_type:
            ext = get_preferred_extension(content_type)
            if ext:
                return ext
        return ".png"

    @staticmethod
    def _image_filename(file_token: str, ext: str = ".png") -> str:
        """Return a conservative local filename for a Feishu media token."""
        safe_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", file_token).strip("._")
        if not ext.startswith("."):
            ext = f".{ext}"
        return f"{safe_token or 'image'}{ext}"

    def _download_image(
        self,
        file_token: str,
        *,
        feishu_access_token: Optional[str] = None,
        extra: Optional[str] = None,
    ) -> Optional[Tuple[bytes, Optional[str]]]:
        """Download an image from Feishu Drive API by file token.

        Returns a ``(content, content_type)`` tuple, or ``None`` on failure.
        """
        import lark_oapi as lark

        client = self._get_client(use_user_token=bool(feishu_access_token))
        # Match the auth mode used to fetch the document: with a user access
        # token the request must advertise USER, otherwise lark-oapi never
        # injects it (see lark_oapi.core.token.auth.verify) and the download
        # silently fails — dropping images from user-token imports.
        token_type = (
            lark.AccessTokenType.USER if feishu_access_token else lark.AccessTokenType.TENANT
        )
        raw_req = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(f"/open-apis/drive/v1/medias/{file_token}/download")
            .token_types({token_type})
            .build()
        )
        if extra:
            raw_req.add_query("extra", extra)
        try:
            raw_resp = self._call_api(client.request, raw_req, feishu_access_token)
        except Exception as exc:
            logger.warning("[FeishuAccessor] Error downloading image %s: %s", file_token, exc)
            return None

        if not raw_resp.success():
            raw = getattr(raw_resp, "raw", None)
            http_status = getattr(raw, "status_code", None)
            detail = getattr(raw_resp, "msg", "") or f"HTTP {http_status}"
            if http_status == 403:
                detail = f"{detail} (missing Feishu permission docs:document.media:download)"
            logger.warning(
                "[FeishuAccessor] Failed to download image %s: code=%s, http=%s, msg=%s",
                file_token,
                getattr(raw_resp, "code", None),
                http_status,
                detail,
            )
            return None

        raw = getattr(raw_resp, "raw", None)
        content = getattr(raw, "content", None)
        if not content:
            logger.warning("[FeishuAccessor] Empty image response for %s", file_token)
            return None
        return content, self._response_content_type(raw)

    @staticmethod
    def _response_content_type(raw) -> Optional[str]:
        """Best-effort extraction of the Content-Type header from a lark raw response."""
        headers = getattr(raw, "headers", None)
        if not headers:
            return None
        # lark's raw.headers may be a plain dict or a case-insensitive mapping.
        try:
            get = headers.get
        except AttributeError:
            return None
        return get("Content-Type") or get("content-type")

    def _resolve_image_refs(
        self,
        markdown: str,
        *,
        feishu_access_token: Optional[str] = None,
        media_download_extras: Optional[_MediaDownloadExtras] = None,
    ) -> Tuple[str, Dict[str, bytes]]:
        """Download Feishu image refs and rewrite them to local relative paths."""
        config = self._get_config()
        if not getattr(config, "download_images", True):
            return markdown, {}

        matches = list(_FEISHU_IMAGE_RE.finditer(markdown))
        if not matches:
            return markdown, {}

        token_to_rel_path: Dict[str, str] = {}
        downloaded_images: Dict[str, bytes] = {}
        for match in matches:
            file_token = match.group(2)
            if file_token in token_to_rel_path:
                continue

            configured_extras = (media_download_extras or {}).get(file_token)
            if configured_extras:
                # Try protected contexts before the legacy token-only fallback.
                extras: List[Optional[str]] = list(
                    dict.fromkeys(extra for extra in configured_extras if extra)
                )[:_MAX_MEDIA_DOWNLOAD_CONTEXTS]
                extras.append(None)
            else:
                extras = [None]

            downloaded = None
            for extra in extras:
                downloaded = self._download_image(
                    file_token,
                    feishu_access_token=feishu_access_token,
                    extra=extra,
                )
                if downloaded is not None:
                    break
            if downloaded is None:
                continue
            image_bytes, content_type = downloaded

            ext = self._guess_image_ext(image_bytes, content_type)
            rel_path = f"images/{self._image_filename(file_token, ext)}"
            token_to_rel_path[file_token] = rel_path
            downloaded_images[rel_path] = image_bytes

        if not downloaded_images:
            return markdown, {}

        def _replace(match: re.Match[str]) -> str:
            alt_text = match.group(1)
            file_token = match.group(2)
            rel_path = token_to_rel_path.get(file_token)
            if not rel_path:
                return match.group(0)
            return f"![{alt_text}]({rel_path})"

        return _FEISHU_IMAGE_RE.sub(_replace, markdown), downloaded_images

    def _extract_block_text(self, block, attr_name: str) -> str:
        """Extract text from a block's named attribute (e.g. block.text, block.heading2)."""
        content_obj = getattr(block, attr_name, None)
        if content_obj and hasattr(content_obj, "elements") and content_obj.elements:
            return self._extract_text_from_elements(content_obj.elements)
        return ""

    def _extract_text_from_elements(self, elements) -> str:
        """Convert Feishu TextElement SDK objects to formatted text."""
        if not elements:
            return ""
        parts = []
        for element in elements:
            # TextRun
            text_run = element.text_run
            if text_run:
                content = text_run.content or ""
                style = text_run.text_element_style
                content = self._apply_text_style(content, style)
                parts.append(content)
                continue

            # MentionUser
            mention_user = element.mention_user
            if mention_user:
                user_id = _getattr_safe(mention_user, "user_id", "user")
                parts.append(f"@{user_id}")
                continue

            # MentionDoc
            mention_doc = element.mention_doc
            if mention_doc:
                title = _getattr_safe(mention_doc, "title", "document")
                url = _getattr_safe(mention_doc, "url", "")
                parts.append(f"[{title}]({url})" if url else str(title))
                continue

            # Equation
            equation = element.equation
            if equation:
                parts.append(f"${_getattr_safe(equation, 'content', '')}$")
                continue

        return "".join(parts)

    @staticmethod
    def _apply_text_style(text: str, style) -> str:
        """Apply markdown formatting based on TextElementStyle SDK object."""
        if not text or not style:
            return text
        # inline_code (SDK uses 'inline_code', not 'code_inline')
        if getattr(style, "inline_code", False):
            return f"`{text}`"
        # link
        link = getattr(style, "link", None)
        if link:
            url = _getattr_safe(link, "url", "")
            if url:
                text = f"[{text}]({url})"
        if getattr(style, "bold", False):
            text = f"**{text}**"
        if getattr(style, "italic", False):
            text = f"*{text}*"
        if getattr(style, "strikethrough", False):
            text = f"~~{text}~~"
        return text

    def _table_block_to_markdown(self, block, block_map: Dict, **_) -> Optional[str]:
        """Convert table block to markdown table."""
        table = block.table
        children = block.children
        if not table or not children:
            return None

        prop = table.property
        if not prop:
            return None
        row_size = prop.row_size or 0
        col_size = prop.column_size or 0
        if not row_size or not col_size:
            return None

        rows = []
        for row_idx in range(row_size):
            row = []
            for col_idx in range(col_size):
                cell_idx = row_idx * col_size + col_idx
                if cell_idx < len(children):
                    cell_block_id = children[cell_idx]
                    cell_block = block_map.get(cell_block_id)
                    cell_text = self._extract_cell_text(cell_block, block_map)
                    row.append(cell_text)
                else:
                    row.append("")
            rows.append(row)

        return format_table_to_markdown(rows, has_header=True) if rows else None

    def _extract_cell_text(self, cell_block, block_map: Dict) -> str:
        """Extract text from a table cell block by reading its children."""
        if not cell_block or not cell_block.children:
            return ""
        texts = []
        for child_id in cell_block.children:
            child = block_map.get(child_id)
            if not child:
                continue
            # Use attribute-driven detection to find text in any block type
            attr = self._detect_block_attr(child)
            if attr:
                text = self._extract_block_text(child, attr)
                if text:
                    texts.append(text)
        return " ".join(texts)

    def _embedded_sheet_to_markdown(
        self,
        block,
        block_map: Dict = None,
        *,
        document_id: str = "",
        feishu_access_token: Optional[str] = None,
        **_,
    ) -> Optional[str]:
        """Convert an embedded spreadsheet block in a docx document."""
        import lark_oapi as lark

        client = self._get_client(use_user_token=bool(feishu_access_token))
        token_type = (
            lark.AccessTokenType.USER if feishu_access_token else lark.AccessTokenType.TENANT
        )
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(
                f"/open-apis/docx/v1/documents/{document_id or block.parent_id}"
                f"/blocks/{block.block_id}"
            )
            .token_types({token_type})
            .build()
        )
        response = self._call_api(client.request, request, feishu_access_token)
        if not response.success():
            logger.warning(
                "[FeishuAccessor] Failed to inspect embedded sheet %s: code=%s msg=%s",
                block.block_id,
                getattr(response, "code", None),
                getattr(response, "msg", None),
            )
            return None

        data = json.loads(response.raw.content)
        sheet_token = data.get("data", {}).get("block", {}).get("sheet", {}).get("token", "")
        parts = sheet_token.rsplit("_", 1)
        if len(parts) != 2:
            return None

        spreadsheet_token, sheet_id = parts
        try:
            rows = self._read_sheet_range(
                spreadsheet_token,
                sheet_id,
                max_rows=100,
                max_cols=26,
                feishu_access_token=feishu_access_token,
            )
        except Exception as exc:
            logger.warning(
                "[FeishuAccessor] Failed to read embedded sheet %s: %s",
                sheet_token,
                exc,
            )
            return None

        rows = self._trim_empty_columns(rows)
        return format_table_to_markdown(rows, has_header=True) if rows else None

    @staticmethod
    def _trim_empty_columns(rows: List[List[str]]) -> List[List[str]]:
        """Remove trailing columns that are empty in every row."""
        if not rows:
            return rows
        last_col = 0
        for col in range(max(len(row) for row in rows)):
            if any(col < len(row) and row[col].strip() for row in rows):
                last_col = col + 1
        return [row[:last_col] for row in rows] if last_col else []

    def _parse_sheets(
        self,
        token: str,
        feishu_access_token: Optional[str] = None,
        *,
        media_download_extras: Optional[_MediaDownloadExtras] = None,
    ) -> Tuple[str, str]:
        """Fetch a Feishu spreadsheet and convert it to Markdown."""
        import lark_oapi as lark

        client = self._get_client(use_user_token=bool(feishu_access_token))
        config = self._get_config()
        token_type = (
            lark.AccessTokenType.USER if feishu_access_token else lark.AccessTokenType.TENANT
        )
        metadata_request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(f"/open-apis/sheets/v2/spreadsheets/{token}/metainfo")
            .token_types({token_type})
            .build()
        )
        metadata_response = self._call_api(
            client.request,
            metadata_request,
            feishu_access_token,
        )
        if not metadata_response.success():
            _raise_from_lark_response(
                metadata_response,
                operation=f"fetch spreadsheet metadata for {token}",
                resource=token,
            )
        metadata = json.loads(metadata_response.raw.content).get("data", {})
        title = (metadata.get("properties") or {}).get("title") or "Spreadsheet"
        sheets = metadata.get("sheets") or []
        markdown_parts = [f"# {title}", f"**Sheets:** {len(sheets)}"]
        for sheet in sheets:
            sheet_id = sheet.get("sheetId") or ""
            sheet_title = sheet.get("title") or sheet_id
            parts = [f"## Sheet: {sheet_title}"]

            block_info = sheet.get("blockInfo")
            if block_info:
                block_type = block_info.get("blockType") or "unknown"
                if block_type != "BITABLE_BLOCK":
                    parts.append(f"*Unsupported sheet block: {block_type}*")
                else:
                    block_token = block_info.get("blockToken") or ""
                    tokens = block_token.rsplit("_", 1)
                    if len(tokens) != 2 or not all(tokens):
                        parts.append("*Invalid embedded bitable token*")
                    else:
                        bitable, _ = self._parse_bitable(
                            tokens[0],
                            feishu_access_token,
                            table_id=tokens[1],
                            table_name=sheet_title,
                            media_download_extras=media_download_extras,
                        )
                        parts.append(bitable or "*Empty bitable*")
                markdown_parts.append("\n\n".join(parts))
                continue

            row_count = int(sheet.get("rowCount") or 0)
            col_count = int(sheet.get("columnCount") or 0)
            if not row_count or not col_count:
                parts.append("*Empty sheet*")
                markdown_parts.append("\n\n".join(parts))
                continue

            parts.append(f"**Dimensions:** {row_count} rows x {col_count} columns")
            rows_to_read = min(row_count, config.max_rows_per_sheet)
            rows = self._read_sheet_range(
                token,
                sheet_id,
                rows_to_read,
                col_count,
                feishu_access_token=feishu_access_token,
            )
            if rows:
                parts.append(format_table_to_markdown(rows, has_header=True))
            if row_count > config.max_rows_per_sheet:
                parts.append(
                    f"\n*... {row_count - config.max_rows_per_sheet} more rows truncated ...*"
                )
            if col_count > 26:
                parts.append(f"\n*... {col_count - 26} columns after Z omitted ...*")
            markdown_parts.append("\n\n".join(parts))

        return "\n\n".join(markdown_parts), title

    def _read_sheet_range(
        self,
        token: str,
        sheet_id: str,
        max_rows: int,
        max_cols: int,
        feishu_access_token: Optional[str] = None,
    ) -> List[List[str]]:
        """Read a bounded cell range from a Feishu spreadsheet."""
        import lark_oapi as lark

        client = self._get_client(use_user_token=bool(feishu_access_token))
        # ponytail: the existing importer reads A:Z only; add chunked ranges if wider
        # spreadsheet imports become a real requirement.
        end_col = self._col_number_to_letter(min(max_cols, 26))
        cell_range = f"{sheet_id}!A1:{end_col}{max_rows}"
        token_type = (
            lark.AccessTokenType.USER if feishu_access_token else lark.AccessTokenType.TENANT
        )
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri(f"/open-apis/sheets/v2/spreadsheets/{token}/values/{cell_range}")
            .token_types({token_type})
            .build()
        )
        response = self._call_api(client.request, request, feishu_access_token)
        if not response.success():
            _raise_from_lark_response(
                response,
                operation=f"read spreadsheet range {cell_range}",
                resource=token,
            )

        data = json.loads(response.raw.content)
        values = data.get("data", {}).get("valueRange", {}).get("values", [])
        return [[str(cell) if cell is not None else "" for cell in row] for row in values]

    @staticmethod
    def _col_number_to_letter(number: int) -> str:
        return chr(ord("A") + number - 1) if 1 <= number <= 26 else "Z"

    def _parse_bitable(
        self,
        app_token: str,
        feishu_access_token: Optional[str] = None,
        *,
        table_id: Optional[str] = None,
        table_name: Optional[str] = None,
        view_id: Optional[str] = None,
        media_download_extras: Optional[_MediaDownloadExtras] = None,
    ) -> Tuple[str, str]:
        """Fetch a Feishu bitable app and convert it to Markdown."""
        if view_id and not table_id:
            raise ValueError("Feishu Base URL with 'view' must also include 'table'")

        from lark_oapi.api.bitable.v1 import (
            ListAppTableFieldRequest,
            ListAppTableRecordRequest,
            ListAppTableRequest,
        )

        client = self._get_client(use_user_token=bool(feishu_access_token))
        config = self._get_config()
        if table_id:
            tables = [(table_id, table_name or table_id)]
            title = table_name or table_id
            if view_id:
                title = f"{title} ({view_id})"
            markdown_parts = []
            heading = "###"
        else:
            table_models = []
            page_token = None
            while True:
                builder = ListAppTableRequest.builder().app_token(app_token).page_size(100)
                if page_token:
                    builder = builder.page_token(page_token)
                tables_response = self._call_api(
                    client.bitable.v1.app_table.list,
                    builder.build(),
                    feishu_access_token,
                )
                if not tables_response.success():
                    _raise_from_lark_response(
                        tables_response,
                        operation=f"list bitable tables for {app_token}",
                        resource=app_token,
                    )
                table_models.extend(tables_response.data.items or [])
                if not getattr(tables_response.data, "has_more", False):
                    break
                page_token = getattr(tables_response.data, "page_token", None)
                if not page_token:
                    raise RuntimeError("Feishu returned more bitable tables without a page token")

            tables = [(table.table_id, table.name or table.table_id) for table in table_models]
            title = f"Bitable ({len(tables)} tables)"
            markdown_parts = [f"# {title}"]
            heading = "##"

        for current_table_id, current_table_name in tables:
            fields = []
            page_token = None
            while True:
                builder = (
                    ListAppTableFieldRequest.builder()
                    .app_token(app_token)
                    .table_id(current_table_id)
                    .page_size(100)
                )
                if page_token:
                    builder = builder.page_token(page_token)
                fields_response = self._call_api(
                    client.bitable.v1.app_table_field.list,
                    builder.build(),
                    feishu_access_token,
                )
                if not fields_response.success():
                    _raise_from_lark_response(
                        fields_response,
                        operation=f"list fields for bitable table {current_table_id}",
                        resource=app_token,
                    )
                fields.extend(fields_response.data.items or [])
                if not getattr(fields_response.data, "has_more", False):
                    break
                page_token = getattr(fields_response.data, "page_token", None)
                if not page_token:
                    raise RuntimeError(
                        f"Feishu returned more fields for table {current_table_id} "
                        "without a page token"
                    )
            field_names = [field.field_name for field in fields]
            field_ids = {
                field.field_name: field.field_id
                for field in fields
                if getattr(field, "field_name", None) and getattr(field, "field_id", None)
            }

            records = []
            page_token = None
            records_truncated = False
            while len(records) < config.max_records_per_table:
                remaining = config.max_records_per_table - len(records)
                builder = (
                    ListAppTableRecordRequest.builder()
                    .app_token(app_token)
                    .table_id(current_table_id)
                    .page_size(min(remaining, 500))
                )
                if view_id:
                    builder = builder.view_id(view_id)
                if page_token:
                    builder = builder.page_token(page_token)
                records_response = self._call_api(
                    client.bitable.v1.app_table_record.list,
                    builder.build(),
                    feishu_access_token,
                )
                if not records_response.success():
                    _raise_from_lark_response(
                        records_response,
                        operation=f"list records for bitable table {current_table_id}",
                        resource=app_token,
                    )
                items = records_response.data.items or []
                records.extend(items[:remaining])
                has_more = bool(records_response.data.has_more)
                if len(items) > remaining:
                    records_truncated = True
                    break
                if not has_more:
                    break
                if len(records) >= config.max_records_per_table:
                    records_truncated = True
                    break
                page_token = records_response.data.page_token
                if not page_token:
                    raise RuntimeError(
                        f"Feishu returned more records for table {current_table_id} "
                        "without a page token"
                    )

            parts = [f"{heading} {current_table_name}", f"**Records:** {len(records)}"]
            if field_names and records:
                rows = [field_names]
                for record in records:
                    record_fields = record.fields or {}
                    row = []
                    for name in field_names:
                        value = record_fields.get(name, "")
                        row.append(self._format_bitable_field(value))
                        if media_download_extras is not None:
                            self._collect_bitable_media_extras(
                                value,
                                table_id=current_table_id,
                                field_id=field_ids.get(name),
                                record_id=getattr(record, "record_id", None),
                                media_download_extras=media_download_extras,
                            )
                    rows.append(row)
                parts.append(format_table_to_markdown(rows, has_header=True))
            if records_truncated:
                parts.append(f"\n*... records truncated at {config.max_records_per_table} ...*")
            markdown_parts.append("\n\n".join(parts))

        return "\n\n".join(markdown_parts), title

    @classmethod
    def _collect_bitable_media_extras(
        cls,
        value: Any,
        *,
        table_id: str,
        field_id: Optional[str],
        record_id: Optional[str],
        media_download_extras: _MediaDownloadExtras,
    ) -> None:
        """Collect transient permission contexts for image refs emitted from a cell."""
        if isinstance(value, list):
            for item in value:
                cls._collect_bitable_media_extras(
                    item,
                    table_id=table_id,
                    field_id=field_id,
                    record_id=record_id,
                    media_download_extras=media_download_extras,
                )
            return
        if not isinstance(value, dict):
            return

        file_token = value.get("file_token")
        name = str(value.get("name") or "image")
        media_type = value.get("type") or mimetypes.guess_type(name)[0]
        if not file_token or not str(media_type).lower().startswith("image/"):
            return

        contexts = media_download_extras.setdefault(str(file_token), [])
        if field_id and record_id:
            extra = json.dumps(
                {
                    "bitablePerm": {
                        "tableId": table_id,
                        "attachments": {field_id: {record_id: [str(file_token)]}},
                    }
                },
                separators=(",", ":"),
            )
            context_count = sum(context is not None for context in contexts)
            if extra not in contexts and context_count < _MAX_MEDIA_DOWNLOAD_CONTEXTS:
                contexts.append(extra)
        elif None not in contexts:
            contexts.append(None)

    @classmethod
    def _format_bitable_field(cls, value: Any) -> str:
        """Render the common structured values returned by bitable fields."""
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(cls._format_bitable_field(item) for item in value)
        if isinstance(value, dict):
            file_token = value.get("file_token")
            name = str(value.get("name") or "image")
            media_type = value.get("type") or mimetypes.guess_type(name)[0]
            if file_token and str(media_type).lower().startswith("image/"):
                return f"![{name}](feishu://image/{file_token})"
            return str(value.get("text", value.get("name", value)))
        return str(value)
