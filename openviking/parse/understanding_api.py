# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
UnderstandingAPI: Integrate with Understanding API for parsing.

Workflow:
1. Upload local file to Files API (file_id) or submit URL directly
2. Submit a parse request to Responses API (response_id)
3. Poll Responses API until completed/failed
4. Download result zip (zip_url)
5. Materialize the result into VikingFS temp directory
6. Return ParseResult for downstream TreeBuilder/SemanticQueue processing
"""

import asyncio
import json
import mimetypes
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.media.constants import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class UnderstandingAPI(BaseParser):
    """
    UnderstandingAPI: Third-party parse client.
    """

    def __init__(self):
        from openviking_cli.utils.config.open_viking_config import get_openviking_config

        ov_config = get_openviking_config()
        parser_api = ov_config.parser_api
        raw_host = (parser_api.host or "").rstrip("/")
        self._api_host = raw_host
        self._api_base = raw_host if raw_host.endswith("/api/v3") else f"{raw_host}/api/v3"
        self._api_key = parser_api.api_key
        self._enable_resumable_upload = bool(parser_api.enable_resumable_upload)
        self._upload_simple_max_bytes = int(parser_api.upload_simple_max_bytes)
        self._upload_part_size_bytes = int(parser_api.upload_part_size_bytes)

        self._http_timeout_sec = float(getattr(parser_api, "http_timeout_seconds", 10.0))
        self._timeout_sec = int(getattr(parser_api, "response_timeout_seconds", 1800))
        self._default_poll_interval_ms = int(getattr(parser_api, "poll_interval_ms", 3000))

        if not self._api_host:
            raise ValueError("parser_api.host is required for UnderstandingAPI")
        if not self._api_key:
            raise ValueError("parser_api.api_key is required for UnderstandingAPI")

        self._video_exts = {e.lstrip(".") for e in VIDEO_EXTENSIONS}
        self._audio_exts = {e.lstrip(".") for e in AUDIO_EXTENSIONS}
        self._image_exts = {e.lstrip(".") for e in IMAGE_EXTENSIONS}

    @property
    def supported_extensions(self) -> List[str]:
        return [".pdf", ".docx", ".pptx", ".xlsx", ".mp4", ".mp3", ".wav", ".mov"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse via third-party API.

        - For local files: upload to Files API (file_id).
        - For URL: submit URL directly via Responses API.
        """
        source_str = str(source)
        original_source = kwargs.get("original_source")
        candidate = original_source if isinstance(original_source, str) else source_str

        url: Optional[str] = None
        local_path: Optional[Path] = None
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            url = candidate
            parsed = urlparse(url)
            doc_name = Path(parsed.path).stem or "resource"
            doc_type = Path(parsed.path).suffix.lower().lstrip(".") or "unknown"
        else:
            local_path = Path(candidate)
            if not local_path.is_file():
                raise ValueError(
                    "UnderstandingAPI supports http(s) URLs or local files. "
                    "Got an invalid local file path."
                )
            doc_name = local_path.stem or "resource"
            doc_type = local_path.suffix.lower().lstrip(".") or "unknown"

        task_meta: Dict[str, Any] = {}

        if url is None and local_path is not None:
            file_obj = await self._create_file(local_path=local_path)
            file_id = file_obj.get("id")
            if not file_id:
                raise RuntimeError(
                    f"files api missing file_id: {self._safe_error_summary(file_obj)}"
                )
            task_meta["file_id"] = file_id
            response_obj = await self._create_response_for_file(file_id=file_id)
        else:
            if url is None:
                raise RuntimeError("missing url for url mode")
            response_obj = await self._create_response_for_url(url=url, doc_type=doc_type)

        response_id = response_obj.get("id")
        if not response_id:
            raise RuntimeError(
                f"responses api missing id: {self._safe_error_summary(response_obj)}"
            )
        task_meta["response_id"] = response_id

        response_obj = await self._poll_response(response_id=response_id)
        zip_url = self._extract_zip_url(response_obj)
        if not zip_url:
            raise RuntimeError(
                f"understanding result missing zip_url: {self._safe_error_summary(response_obj)}"
            )

        zip_path = await self._download_zip(zip_url)
        try:
            temp_dir_path = await self._unpack_zip_to_temp_dir(
                zip_path=zip_path,
                resource_name=doc_name,
            )
        finally:
            try:
                zip_path.unlink()
            except Exception:
                pass

        content_type = (
            "video"
            if doc_type in self._video_exts
            else "audio"
            if doc_type in self._audio_exts
            else "image"
            if doc_type in self._image_exts
            else "text"
        )
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=doc_name,
            level=0,
            detail_file=None,
            content_path=None,
            meta={
                "source_title": doc_name,
                "semantic_name": doc_name,
                "original_filename": f"{doc_name}.{doc_type}" if doc_type else doc_name,
            },
            content_type=content_type,
        )

        result = ParseResult(
            root=root_node,
            source_path=url or source_str,
            source_format=doc_type,
            temp_dir_path=temp_dir_path,
            parser_name="UnderstandingAPI",
            meta=task_meta,
        )

        logger.info("[UnderstandingAPI] done")
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        raise NotImplementedError("UnderstandingAPI.parse_content is not supported")

    def _json_bytes(self, obj: Any) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _auth_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if extra:
            headers.update(extra)
        return headers

    def _safe_error_summary(self, obj: Any) -> Dict[str, Any]:
        if not isinstance(obj, dict):
            return {"kind": type(obj).__name__}
        summary: Dict[str, Any] = {}
        for key in ("id", "status", "message"):
            if key in obj:
                summary[key] = obj.get(key)
        err = obj.get("error")
        if isinstance(err, dict):
            summary["error"] = {k: err.get(k) for k in ("type", "code", "message") if k in err}
        return summary

    def _raise_if_error(self, obj: Any, *, context: str) -> None:
        if not isinstance(obj, dict):
            return
        err = obj.get("error")
        if isinstance(err, dict) and err.get("code"):
            raise RuntimeError(f"{context}: {self._safe_error_summary(obj)}")

    async def _create_file(self, *, local_path: Path) -> Dict[str, Any]:
        file_size = local_path.stat().st_size
        if file_size > self._upload_simple_max_bytes:
            if not self._enable_resumable_upload:
                raise ValueError(
                    f"file too large ({file_size} bytes), enable parser_api.enable_resumable_upload to continue"
                )
            return await self._multipart_create_file(local_path)

        data: Dict[str, Any] = {"purpose": "user_data"}

        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            files = {"file": (local_path.name, f, content_type)}
            async with httpx.AsyncClient(timeout=1200.0, follow_redirects=True) as client:
                rsp = await client.post(
                    f"{self._api_base}/files",
                    headers=self._auth_headers(),
                    data=data,
                    files=files,
                )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="files api error")
        return body

    async def _create_response_for_file(self, *, file_id: str) -> Dict[str, Any]:
        content: Dict[str, Any] = {"type": "file", "file": {"file_id": file_id}}
        payload = {
            "input": [{"role": "user", "content": [content]}],
            "tools": [{"type": "understanding"}],
            "store": True,
        }
        async with httpx.AsyncClient(
            timeout=self._http_timeout_sec, follow_redirects=True
        ) as client:
            rsp = await client.post(
                f"{self._api_base}/responses",
                content=self._json_bytes(payload),
                headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="responses api error")
        return body

    async def _create_response_for_url(self, *, url: str, doc_type: str) -> Dict[str, Any]:
        if doc_type in self._video_exts:
            content: Dict[str, Any] = {"type": "input_video", "video_url": url}
        elif doc_type in self._image_exts:
            content = {"type": "input_image", "image_url": url}
        elif doc_type in self._audio_exts:
            content = {"type": "input_audio", "audio_url": url}
        else:
            content = {"type": "input_file", "file_url": url}
        payload = {
            "input": [{"role": "user", "content": [content]}],
            "tools": [{"type": "understanding"}],
            "store": True,
        }
        async with httpx.AsyncClient(
            timeout=self._http_timeout_sec, follow_redirects=True
        ) as client:
            rsp = await client.post(
                f"{self._api_base}/responses",
                content=self._json_bytes(payload),
                headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="responses api error")
        return body

    async def _poll_response(self, *, response_id: str) -> Dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + float(self._timeout_sec)
        last_status = None
        async with httpx.AsyncClient(
            timeout=self._http_timeout_sec, follow_redirects=True
        ) as client:
            while True:
                rsp = await client.get(
                    f"{self._api_base}/responses/{response_id}",
                    headers=self._auth_headers(),
                )
                rsp.raise_for_status()
                body = rsp.json()
                self._raise_if_error(
                    body, context=f"responses api error: response_id={response_id}"
                )
                status = body.get("status")
                if status != last_status:
                    logger.info(f"[UnderstandingAPI] response_id={response_id} status={status}")
                    last_status = status
                if status == "completed":
                    return body
                if status == "failed":
                    raise RuntimeError(
                        f"understanding failed: response_id={response_id} body={self._safe_error_summary(body)}"
                    )
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError(
                        f"understanding timeout: response_id={response_id} last_status={last_status}"
                    )
                await asyncio.sleep(max(self._default_poll_interval_ms, 200) / 1000.0)

    def _extract_zip_url(self, response_obj: Dict[str, Any]) -> Optional[str]:
        result_obj = response_obj.get("result") or {}
        if isinstance(result_obj, dict) and result_obj.get("zip_url"):
            return str(result_obj["zip_url"])
        for output_item in response_obj.get("output") or []:
            if not isinstance(output_item, dict):
                continue
            for content_item in output_item.get("content") or []:
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") != "zip_url":
                    continue
                zip_obj = content_item.get("zip_url")
                if isinstance(zip_obj, dict) and zip_obj.get("url"):
                    return str(zip_obj["url"])
        return None

    async def _uploads_init(self, *, file_path: Path) -> Dict[str, Any]:
        payload = {
            "file_name": file_path.name,
            "file_size": file_path.stat().st_size,
            "content_type": mimetypes.guess_type(str(file_path))[0] or "application/octet-stream",
            "part_size": int(self._upload_part_size_bytes),
        }
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            rsp = await client.post(
                f"{self._api_base}/files?uploads",
                content=self._json_bytes(payload),
                headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="uploads init error")
        return body

    async def _uploads_status(self, *, upload_id: str, object_key: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            rsp = await client.get(
                f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}",
                headers=self._auth_headers(),
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="uploads status error")
        return body

    async def _uploads_put_part(
        self, *, upload_id: str, object_key: str, part_number: int, data: bytes
    ) -> Dict[str, Any]:
        headers = self._auth_headers({"Content-Type": "application/octet-stream"})
        async with httpx.AsyncClient(timeout=1200.0, follow_redirects=True) as client:
            rsp = await client.put(
                f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}&part_number={part_number}",
                headers=headers,
                content=data,
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="uploads part error")
        return body

    async def _uploads_complete(
        self, *, upload_id: str, object_key: str, parts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        payload = {"parts": parts}
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            rsp = await client.post(
                f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}",
                content=self._json_bytes(payload),
                headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
            )
        rsp.raise_for_status()
        body = rsp.json()
        self._raise_if_error(body, context="uploads complete error")
        return body

    async def _multipart_create_file(self, file_path: Path) -> Dict[str, Any]:
        init_obj = await self._uploads_init(file_path=file_path)
        upload_id = init_obj.get("upload_id") or init_obj.get("uploadId")
        object_key = init_obj.get("object_key") or init_obj.get("objectKey")
        part_size = int(
            init_obj.get("part_size") or init_obj.get("partSize") or self._upload_part_size_bytes
        )
        if not upload_id:
            raise RuntimeError(
                f"uploads init missing upload_id: {self._safe_error_summary(init_obj)}"
            )
        if not object_key:
            raise RuntimeError(
                f"uploads init missing object_key: {self._safe_error_summary(init_obj)}"
            )

        status_obj = await self._uploads_status(upload_id=upload_id, object_key=object_key)
        uploaded_parts = status_obj.get("parts") or []
        uploaded_map: Dict[int, str] = {}
        for p in uploaded_parts:
            try:
                pn = int(p.get("part_number") or p.get("partNumber"))
            except Exception:
                continue
            etag = p.get("etag")
            if isinstance(etag, str) and etag:
                uploaded_map[pn] = etag

        parts: Dict[int, str] = dict(uploaded_map)
        file_size = file_path.stat().st_size
        total_parts = (file_size + part_size - 1) // part_size

        with open(file_path, "rb") as f:
            for n in range(1, total_parts + 1):
                if n in parts:
                    continue
                f.seek((n - 1) * part_size)
                chunk = f.read(part_size)
                part_obj = await self._uploads_put_part(
                    upload_id=upload_id, object_key=object_key, part_number=n, data=chunk
                )
                etag = part_obj.get("etag")
                if not etag:
                    raise RuntimeError(
                        f"uploads part missing etag: part={n} resp={self._safe_error_summary(part_obj)}"
                    )
                parts[n] = etag

        complete_obj = await self._uploads_complete(
            upload_id=upload_id,
            object_key=object_key,
            parts=[{"part_number": n, "etag": e} for n, e in sorted(parts.items())],
        )
        if complete_obj.get("status") != "active" or not complete_obj.get("id"):
            raise RuntimeError(f"uploads complete did not return file object: {complete_obj}")
        return complete_obj

    async def _download_zip(self, zip_url: str) -> Path:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            rsp = await client.get(zip_url)
        rsp.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
            f.write(rsp.content)
            return Path(f.name)

    async def _unpack_zip_to_temp_dir(self, zip_path: Path, resource_name: str) -> str:
        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()
        await viking_fs.mkdir(temp_uri)

        temp_doc_uri = f"{temp_uri}/{resource_name}"
        await viking_fs.mkdir(temp_doc_uri)

        with tempfile.TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(zip_path, "r") as zf:
                safe_extract_zip(zf, extract_dir)
            extract_path = Path(extract_dir)
            items = [p for p in extract_path.iterdir() if p.name not in {".", ".."}]
            if len(items) == 1 and items[0].is_dir():
                root_dir = items[0]
            else:
                root_dir = extract_path

            for child in root_dir.iterdir():
                if child.name in {".", ".."}:
                    continue
                if child.is_dir():
                    sub_uri = f"{temp_doc_uri}/{child.name}"
                    await viking_fs.mkdir(sub_uri)
                    await self._copy_dir_to_fs(child, sub_uri)
                else:
                    await viking_fs.write_file_bytes(
                        f"{temp_doc_uri}/{child.name}", child.read_bytes()
                    )

        return temp_uri

    async def _copy_dir_to_fs(self, local_dir: Path, fs_uri: str):
        """
        Recursively copy a local directory to VikingFS.
        """
        viking_fs = get_viking_fs()

        for item in local_dir.iterdir():
            if item.name in [".", ".."]:
                continue

            if item.is_dir():
                sub_uri = f"{fs_uri}/{item.name}"
                await viking_fs.mkdir(sub_uri)
                await self._copy_dir_to_fs(item, sub_uri)
            else:
                file_content = item.read_bytes()
                file_uri = f"{fs_uri}/{item.name}"
                await viking_fs.write_file_bytes(file_uri, file_content)
