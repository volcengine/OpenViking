# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Minimal WebDAV adapter for resources scope."""

from __future__ import annotations

import mimetypes
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import format_datetime
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from fastapi.responses import Response as FastAPIResponse

from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.utils.uri import VikingURI

router = APIRouter(prefix="/webdav/resources", tags=["webdav"])

_DAV_NAMESPACE = "DAV:"
_RESERVED_FILENAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        ".path.ovlock",
    }
)
_WEBDAV_METHODS = "OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL, MOVE"
_TEXT_MEDIA_FALLBACK = "text/plain; charset=utf-8"


def _webdav_headers() -> dict[str, str]:
    return {
        "Allow": _WEBDAV_METHODS,
        "DAV": "1",
        "MS-Author-Via": "DAV",
    }


def _error(status_code: int, message: str) -> PlainTextResponse:
    return PlainTextResponse(message, status_code=status_code, headers=_webdav_headers())


def _normalized_resource_path(resource_path: str) -> str:
    decoded = unquote(resource_path or "").strip("/")
    if not decoded:
        return ""

    parts: list[str] = []
    for part in decoded.split("/"):
        if not part:
            continue
        if part in {".", ".."}:
            raise InvalidArgumentError(f"unsafe WebDAV path segment: {part}")
        if "\\" in part:
            raise InvalidArgumentError(f"unsafe WebDAV path separator in segment: {part}")
        if len(part) >= 2 and part[1] == ":" and part[0].isalpha():
            raise InvalidArgumentError(f"unsafe WebDAV drive-prefixed segment: {part}")
        parts.append(part)
    return "/".join(parts)


def _ensure_exposed_path(resource_path: str) -> None:
    if not resource_path:
        return
    parts = resource_path.split("/")
    if any(part in _RESERVED_FILENAMES for part in parts):
        raise NotFoundError(resource_path, "resource")


def _resource_uri(resource_path: str) -> str:
    return "viking://resources" if not resource_path else f"viking://resources/{resource_path}"


def _href_for_path(request: Request, resource_path: str, *, is_dir: bool) -> str:
    base = request.scope.get("root_path", "") + "/webdav/resources"
    if resource_path:
        href = f"{base}/{quote(resource_path, safe='/')}"
    else:
        href = base
    if is_dir and not href.endswith("/"):
        href += "/"
    return href


def _content_type_for_name(name: str, *, is_dir: bool) -> str:
    if is_dir:
        return "httpd/unix-directory"
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    return _TEXT_MEDIA_FALLBACK


def _http_last_modified(raw: str) -> Optional[str]:
    if not raw:
        return None
    try:
        dt = parse_iso_datetime(raw)
    except Exception:
        return None
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def _entry_from_stat(
    request: Request,
    resource_path: str,
    stat: dict[str, Any],
    *,
    root_name: str = "resources",
) -> dict[str, Any]:
    is_dir = bool(stat.get("isDir", False))
    name = stat.get("name") or (
        resource_path.rstrip("/").split("/")[-1] if resource_path else root_name
    )
    return {
        "href": _href_for_path(request, resource_path, is_dir=is_dir),
        "name": name,
        "is_dir": is_dir,
        "size": int(stat.get("size", 0) or 0),
        "mod_time": stat.get("modTime", ""),
        "content_type": _content_type_for_name(str(name), is_dir=is_dir),
    }


def _propfind_xml(entries: list[dict[str, Any]]) -> bytes:
    ET.register_namespace("d", _DAV_NAMESPACE)
    multistatus = ET.Element(f"{{{_DAV_NAMESPACE}}}multistatus")

    for entry in entries:
        response = ET.SubElement(multistatus, f"{{{_DAV_NAMESPACE}}}response")
        ET.SubElement(response, f"{{{_DAV_NAMESPACE}}}href").text = entry["href"]

        propstat = ET.SubElement(response, f"{{{_DAV_NAMESPACE}}}propstat")
        prop = ET.SubElement(propstat, f"{{{_DAV_NAMESPACE}}}prop")

        ET.SubElement(prop, f"{{{_DAV_NAMESPACE}}}displayname").text = entry["name"]
        resource_type = ET.SubElement(prop, f"{{{_DAV_NAMESPACE}}}resourcetype")
        if entry["is_dir"]:
            ET.SubElement(resource_type, f"{{{_DAV_NAMESPACE}}}collection")
        else:
            ET.SubElement(prop, f"{{{_DAV_NAMESPACE}}}getcontentlength").text = str(entry["size"])
        ET.SubElement(prop, f"{{{_DAV_NAMESPACE}}}getcontenttype").text = entry["content_type"]

        last_modified = _http_last_modified(entry["mod_time"])
        if last_modified:
            ET.SubElement(prop, f"{{{_DAV_NAMESPACE}}}getlastmodified").text = last_modified

        ET.SubElement(propstat, f"{{{_DAV_NAMESPACE}}}status").text = "HTTP/1.1 200 OK"

    return ET.tostring(multistatus, encoding="utf-8", xml_declaration=True)


def _depth_header(request: Request) -> int:
    depth = (request.headers.get("Depth", "1") or "1").strip().lower()
    if depth == "0":
        return 0
    return 1


def _destination_path(destination: str) -> str:
    parsed = urlparse(destination)
    raw_path = parsed.path if parsed.scheme else destination
    normalized = raw_path.rstrip("/")
    prefix = "/webdav/resources"
    if normalized == prefix:
        return ""
    if not normalized.startswith(prefix + "/"):
        raise InvalidArgumentError("Destination must stay under /webdav/resources")
    return _normalized_resource_path(normalized[len(prefix) + 1 :])


async def _write_text_resource(service, uri: str, content: str, ctx: RequestContext) -> None:
    """Persist UTF-8 text content and refresh derived summaries before returning."""
    await service.viking_fs.write_file(uri, content, ctx=ctx)
    await service.resources.summarize([uri], ctx=ctx)


async def _safe_stat(service, uri: str, ctx: RequestContext) -> Optional[dict[str, Any]]:
    try:
        return await service.fs.stat(uri, ctx=ctx)
    except (FileNotFoundError, NotFoundError):
        return None


async def _ensure_parent_directory(
    service, uri: str, ctx: RequestContext
) -> Optional[dict[str, Any]]:
    parent = VikingURI(uri).parent
    if parent is None:
        return None
    return await _safe_stat(service, parent.uri, ctx)


def _exposed_child_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exposed: list[dict[str, Any]] = []
    for entry in entries:
        name = str(entry.get("name", "") or "")
        if not name or name in {".", ".."}:
            continue
        if name in _RESERVED_FILENAMES:
            continue
        exposed.append(entry)
    return exposed


@router.api_route("", methods=["OPTIONS"])
@router.api_route("/{resource_path:path}", methods=["OPTIONS"])
async def options(resource_path: str = ""):
    return FastAPIResponse(status_code=204, headers=_webdav_headers())


@router.api_route("", methods=["PROPFIND"])
@router.api_route("/{resource_path:path}", methods=["PROPFIND"])
async def propfind(
    request: Request,
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    service = get_service()
    uri = _resource_uri(normalized_path)
    stat = await _safe_stat(service, uri, _ctx)
    if stat is None:
        return _error(404, "Not found")

    entries = [_entry_from_stat(request, normalized_path, stat)]
    if _depth_header(request) > 0 and stat.get("isDir", False):
        children = await service.fs.ls(
            uri,
            ctx=_ctx,
            output="original",
            show_all_hidden=True,
            node_limit=10000,
        )
        for child in _exposed_child_entries(children):
            child_name = str(child["name"])
            child_path = child_name if not normalized_path else f"{normalized_path}/{child_name}"
            entries.append(_entry_from_stat(request, child_path, child))

    return FastAPIResponse(
        content=_propfind_xml(entries),
        status_code=207,
        media_type="application/xml",
        headers=_webdav_headers(),
    )


@router.api_route("", methods=["GET", "HEAD"])
@router.api_route("/{resource_path:path}", methods=["GET", "HEAD"])
async def get_or_head(
    request: Request,
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    if not normalized_path:
        return _error(405, "GET is only supported for files")

    service = get_service()
    uri = _resource_uri(normalized_path)
    stat = await _safe_stat(service, uri, _ctx)
    if stat is None:
        return _error(404, "Not found")
    if stat.get("isDir", False):
        return _error(405, "GET is only supported for files")

    body = b"" if request.method == "HEAD" else await service.fs.read_file_bytes(uri, ctx=_ctx)
    headers = _webdav_headers()
    headers["Content-Length"] = str(int(stat.get("size", 0) or 0))
    last_modified = _http_last_modified(str(stat.get("modTime", "") or ""))
    if last_modified:
        headers["Last-Modified"] = last_modified

    return FastAPIResponse(
        content=body,
        media_type=_content_type_for_name(str(stat.get("name", "")), is_dir=False),
        headers=headers,
    )


@router.api_route("", methods=["PUT"])
@router.api_route("/{resource_path:path}", methods=["PUT"])
async def put(
    request: Request,
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    if not normalized_path:
        return _error(405, "PUT requires a file path")

    raw_body = await request.body()
    try:
        content = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return _error(415, "Phase 1 WebDAV only supports UTF-8 text content")

    service = get_service()
    uri = _resource_uri(normalized_path)
    stat = await _safe_stat(service, uri, _ctx)

    parent_stat = await _ensure_parent_directory(service, uri, _ctx)
    if parent_stat is None or not parent_stat.get("isDir", False):
        return _error(409, "Parent collection does not exist")

    if stat is not None:
        if stat.get("isDir", False):
            return _error(405, "PUT is only supported for files")
        await _write_text_resource(service, uri, content, _ctx)
        return FastAPIResponse(status_code=204, headers=_webdav_headers())

    await _write_text_resource(service, uri, content, _ctx)
    headers = _webdav_headers()
    headers["Location"] = _href_for_path(request, normalized_path, is_dir=False)
    return FastAPIResponse(status_code=201, headers=headers)


@router.api_route("", methods=["DELETE"])
@router.api_route("/{resource_path:path}", methods=["DELETE"])
async def delete(
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    if not normalized_path:
        return _error(405, "Deleting the resources root is not supported")

    service = get_service()
    uri = _resource_uri(normalized_path)
    stat = await _safe_stat(service, uri, _ctx)
    if stat is None:
        return _error(404, "Not found")

    await service.fs.rm(uri, ctx=_ctx, recursive=bool(stat.get("isDir", False)))
    return FastAPIResponse(status_code=204, headers=_webdav_headers())


@router.api_route("", methods=["MKCOL"])
@router.api_route("/{resource_path:path}", methods=["MKCOL"])
async def mkcol(
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    if not normalized_path:
        return _error(405, "MKCOL requires a collection path")

    service = get_service()
    uri = _resource_uri(normalized_path)
    stat = await _safe_stat(service, uri, _ctx)
    if stat is not None:
        return _error(405, "Collection already exists")

    parent_stat = await _ensure_parent_directory(service, uri, _ctx)
    if parent_stat is None or not parent_stat.get("isDir", False):
        return _error(409, "Parent collection does not exist")

    await service.fs.mkdir(uri, ctx=_ctx)
    return FastAPIResponse(status_code=201, headers=_webdav_headers())


@router.api_route("", methods=["MOVE"])
@router.api_route("/{resource_path:path}", methods=["MOVE"])
async def move(
    request: Request,
    resource_path: str = "",
    _ctx: RequestContext = Depends(get_request_context),
):
    destination = request.headers.get("Destination", "")
    if not destination:
        return _error(400, "Destination header is required")

    try:
        normalized_path = _normalized_resource_path(resource_path)
        _ensure_exposed_path(normalized_path)
        destination_path = _destination_path(destination)
        _ensure_exposed_path(destination_path)
    except InvalidArgumentError as exc:
        return _error(400, exc.message)
    except NotFoundError:
        return _error(404, "Not found")

    if not normalized_path:
        return _error(405, "Moving the resources root is not supported")

    service = get_service()
    src_uri = _resource_uri(normalized_path)
    dst_uri = _resource_uri(destination_path)
    src_stat = await _safe_stat(service, src_uri, _ctx)
    if src_stat is None:
        return _error(404, "Not found")

    dst_parent_stat = await _ensure_parent_directory(service, dst_uri, _ctx)
    if dst_parent_stat is None or not dst_parent_stat.get("isDir", False):
        return _error(409, "Destination parent collection does not exist")

    overwrite = (request.headers.get("Overwrite", "T") or "T").strip().upper() != "F"
    dst_stat = await _safe_stat(service, dst_uri, _ctx)
    if dst_stat is not None:
        if not overwrite:
            return _error(412, "Destination already exists")
        await service.fs.rm(dst_uri, ctx=_ctx, recursive=bool(dst_stat.get("isDir", False)))

    await service.fs.mv(src_uri, dst_uri, ctx=_ctx)
    status_code = 204 if dst_stat is not None else 201
    return FastAPIResponse(status_code=status_code, headers=_webdav_headers())
