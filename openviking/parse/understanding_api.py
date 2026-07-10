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
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx

from openviking.parse.base import NodeType, ParseResult, ResourceNode
from openviking.parse.parsers.base_parser import BaseParser
from openviking.storage.viking_fs import get_viking_fs
from openviking.utils.zip_safe import safe_extract_zip
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

# 轮询 GET 遇到瞬时错误（网络抖动 / 5xx / 429）时的最大连续重试次数与退避上限；
# base server 内部调 pre_process 已有重试，这里为 OV -> base server 这一跳补上重试，
# 避免单次抖动让整个解析任务失败。
_POLL_MAX_TRANSIENT_RETRIES = 3
_POLL_MAX_BACKOFF_SEC = 30.0
_UPLOAD_PART_MAX_RETRIES = 3
_UPLOAD_PART_RETRY_BACKOFF_SEC = 2.0


class UnderstandingAPIError(RuntimeError):
    """Parser API failure carrying the remote identifiers already observed."""

    def __init__(self, message: str, meta: Optional[Dict[str, Any]] = None):
        self.meta = dict(meta or {})
        super().__init__(message)

    def __str__(self) -> str:
        message = super().__str__()
        if not self.meta:
            return message
        fields = (
            "doc_name",
            "doc_type",
            "source_name",
            "file_name",
            "file_id",
            "response_id",
        )
        compact = {key: self.meta[key] for key in fields if self.meta.get(key)}
        if not compact:
            return message
        return f"{message} meta={json.dumps(compact, ensure_ascii=False, sort_keys=True)}"


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
        self._upload_part_max_concurrent = int(getattr(parser_api, "upload_part_max_concurrent", 3))

        self._http_timeout_sec = float(getattr(parser_api, "http_timeout_seconds", 10.0))
        self._timeout_sec = int(getattr(parser_api, "response_timeout_seconds", 1800))
        self._default_poll_interval_ms = int(getattr(parser_api, "poll_interval_ms", 3000))

        if not self._api_host:
            raise ValueError("parser_api.host is required for UnderstandingAPI")
        if not self._api_key:
            raise ValueError("parser_api.api_key is required for UnderstandingAPI")

        self._video_exts = {"mp4", "mov", "avi", "flv", "mkv", "wmv", "webm"}
        self._audio_exts = {"mp3", "wav", "m4a", "flac", "aac", "ogg"}
        self._image_exts = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}

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
        source_local_path: Optional[Path] = None
        source_path = Path(source_str)
        if source_path.is_file():
            source_local_path = source_path

        url: Optional[str] = None
        local_path: Optional[Path] = None
        if isinstance(original_source, str) and original_source.startswith(("http://", "https://")):
            parsed = urlparse(original_source)
            original_ext = Path(parsed.path).suffix.lower().lstrip(".")
            if source_local_path is None or original_ext:
                url = original_source
                doc_name = Path(parsed.path).stem or "resource"
                doc_type = original_ext or "unknown"
            else:
                local_path = source_local_path
                doc_name = local_path.stem or "resource"
                doc_type = local_path.suffix.lower().lstrip(".") or "unknown"
        elif source_str.startswith(("http://", "https://")):
            url = source_str
            parsed = urlparse(url)
            doc_name = Path(parsed.path).stem or "resource"
            doc_type = Path(parsed.path).suffix.lower().lstrip(".") or "unknown"
        else:
            local_path = source_local_path or source_path
            if not local_path.is_file():
                raise ValueError(
                    "UnderstandingAPI supports http(s) URLs or local files. "
                    "Got an invalid local file path."
                )
            doc_name = local_path.stem or "resource"
            doc_type = local_path.suffix.lower().lstrip(".") or "unknown"

        # 优先用上游（MediaProcessor）算好的资源名作为目录名/标题；URL stem 或本地临时文件名
        # （如 temp 上传的 upload_<hash>）仅作 fallback。resource_name 已由 MediaProcessor 处理；
        # source_name 是原始文件名，需去掉扩展名。
        override_name = kwargs.get("resource_name")
        if not override_name:
            src_name = kwargs.get("source_name")
            if isinstance(src_name, str) and src_name:
                override_name = Path(src_name).stem
        if override_name:
            doc_name = str(override_name)

        task_meta: Dict[str, Any] = {"doc_name": doc_name, "doc_type": doc_type}
        if isinstance(kwargs.get("source_name"), str) and kwargs["source_name"]:
            task_meta["source_name"] = kwargs["source_name"]
        if local_path is not None:
            task_meta["file_name"] = local_path.name

        try:
            if url is None and local_path is not None:
                upload_name = (
                    "{}.{}".format(doc_name, doc_type)
                    if doc_type and doc_type != "unknown"
                    else doc_name
                )
                file_obj = await self._create_file(local_path=local_path, upload_name=upload_name)
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
        except UnderstandingAPIError:
            raise
        except Exception as exc:
            raise UnderstandingAPIError(str(exc), task_meta) from exc

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
        for key in ("id", "status", "message", "output"):
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

    @staticmethod
    def _exception_summary(exc: Exception) -> str:
        message = str(exc) or repr(exc)
        return f"{type(exc).__name__}: {message}"

    async def _create_file(
        self, *, local_path: Path, upload_name: Optional[str] = None
    ) -> Dict[str, Any]:
        file_size = local_path.stat().st_size
        file_name = upload_name or local_path.name
        if file_size > self._upload_simple_max_bytes:
            if not self._enable_resumable_upload:
                raise ValueError(
                    f"file too large ({file_size} bytes), enable parser_api.enable_resumable_upload to continue"
                )
            return await self._multipart_create_file(local_path, upload_name=file_name)

        data: Dict[str, Any] = {"purpose": "user_data"}

        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        with open(local_path, "rb") as f:
            files = {"file": (file_name, f, content_type)}
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

    @staticmethod
    def _is_transient_poll_error(exc: Exception) -> bool:
        """网络错误、5xx 和 429 视为瞬时错误（可重试）；其余 4xx 视为终态。"""
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code == 429 or 500 <= code < 600
        return False

    @staticmethod
    def _is_transient_upload_error(exc: Exception) -> bool:
        """Retry transport errors, 408, 429, and 5xx, including wrapped causes."""
        current: Optional[BaseException] = exc
        while current is not None:
            if isinstance(current, httpx.TransportError):
                return True
            if isinstance(current, httpx.HTTPStatusError):
                code = current.response.status_code
                return code in {408, 429} or 500 <= code < 600
            current = current.__cause__
        return False

    async def _poll_response(self, *, response_id: str) -> Dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + float(self._timeout_sec)
        last_status = None
        consecutive_errors = 0
        poll_interval = max(self._default_poll_interval_ms, 200) / 1000.0
        async with httpx.AsyncClient(
            timeout=self._http_timeout_sec, follow_redirects=True
        ) as client:
            while True:
                try:
                    rsp = await client.get(
                        f"{self._api_base}/responses/{response_id}",
                        headers=self._auth_headers(),
                    )
                    rsp.raise_for_status()
                    body = rsp.json()
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    if not self._is_transient_poll_error(e):
                        raise
                    consecutive_errors += 1
                    if consecutive_errors > _POLL_MAX_TRANSIENT_RETRIES:
                        raise RuntimeError(
                            f"understanding poll failed after {consecutive_errors} transient errors: "
                            f"response_id={response_id} err={e}"
                        )
                    if asyncio.get_running_loop().time() > deadline:
                        raise TimeoutError(
                            f"understanding timeout during transient errors: response_id={response_id}"
                        )
                    backoff = min(
                        poll_interval * (2 ** (consecutive_errors - 1)), _POLL_MAX_BACKOFF_SEC
                    )
                    logger.warning(
                        f"[UnderstandingAPI] poll transient error response_id={response_id} "
                        f"attempt={consecutive_errors} backoff={backoff:.1f}s err={e}"
                    )
                    await asyncio.sleep(backoff)
                    continue

                consecutive_errors = 0
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
                await asyncio.sleep(poll_interval)

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

    async def _uploads_init(
        self, *, file_path: Path, upload_name: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "file_name": upload_name or file_path.name,
            "file_size": file_path.stat().st_size,
            "content_type": mimetypes.guess_type(str(file_path))[0] or "application/octet-stream",
            "part_size": int(self._upload_part_size_bytes),
        }
        started_at = time.monotonic()
        logger.info(
            "[UnderstandingAPI] uploads_init start file_name=%s file_size=%d part_size=%d",
            payload["file_name"],
            payload["file_size"],
            payload["part_size"],
        )
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                rsp = await client.post(
                    f"{self._api_base}/files?uploads",
                    content=self._json_bytes(payload),
                    headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
                )
            rsp.raise_for_status()
            body = rsp.json()
            self._raise_if_error(body, context="uploads init error")
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.error(
                "[UnderstandingAPI] uploads_init failed file_name=%s file_size=%d "
                "part_size=%d elapsed_ms=%.2f error=%s",
                payload["file_name"],
                payload["file_size"],
                payload["part_size"],
                elapsed_ms,
                self._exception_summary(exc),
                exc_info=True,
            )
            raise RuntimeError(
                "uploads init failed: "
                f"file_name={payload['file_name']} file_size={payload['file_size']} "
                f"part_size={payload['part_size']} error={self._exception_summary(exc)}"
            ) from exc
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "[UnderstandingAPI] uploads_init done file_name=%s upload_id=%s object_key=%s "
            "part_size=%s elapsed_ms=%.2f",
            payload["file_name"],
            body.get("upload_id") or body.get("uploadId"),
            body.get("object_key") or body.get("objectKey"),
            body.get("part_size") or body.get("partSize"),
            elapsed_ms,
        )
        return body

    async def _uploads_status(self, *, upload_id: str, object_key: str) -> Dict[str, Any]:
        started_at = time.monotonic()
        logger.info(
            "[UnderstandingAPI] uploads_status start upload_id=%s object_key=%s",
            upload_id,
            object_key,
        )
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                rsp = await client.get(
                    f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}",
                    headers=self._auth_headers(),
                )
            rsp.raise_for_status()
            body = rsp.json()
            self._raise_if_error(body, context="uploads status error")
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.error(
                "[UnderstandingAPI] uploads_status failed upload_id=%s object_key=%s "
                "elapsed_ms=%.2f error=%s",
                upload_id,
                object_key,
                elapsed_ms,
                self._exception_summary(exc),
                exc_info=True,
            )
            raise RuntimeError(
                "uploads status failed: "
                f"upload_id={upload_id} object_key={object_key} "
                f"error={self._exception_summary(exc)}"
            ) from exc
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "[UnderstandingAPI] uploads_status done upload_id=%s object_key=%s "
            "parts=%d elapsed_ms=%.2f",
            upload_id,
            object_key,
            len(body.get("parts") or []),
            elapsed_ms,
        )
        return body

    async def _uploads_put_part(
        self, *, upload_id: str, object_key: str, part_number: int, data: bytes
    ) -> Dict[str, Any]:
        headers = self._auth_headers({"Content-Type": "application/octet-stream"})
        started_at = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=1200.0, follow_redirects=True) as client:
                rsp = await client.put(
                    f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}&part_number={part_number}",
                    headers=headers,
                    content=data,
                )
            rsp.raise_for_status()
            body = rsp.json()
            self._raise_if_error(body, context="uploads part error")
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            raise RuntimeError(
                "uploads part failed: "
                f"upload_id={upload_id} object_key={object_key} "
                f"part_number={part_number} part_size={len(data)} elapsed_ms={elapsed_ms:.2f} "
                f"error={self._exception_summary(exc)}"
            ) from exc
        return body

    async def _uploads_complete(
        self, *, upload_id: str, object_key: str, parts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        payload = {"parts": parts}
        started_at = time.monotonic()
        logger.info(
            "[UnderstandingAPI] uploads_complete start upload_id=%s object_key=%s parts=%d",
            upload_id,
            object_key,
            len(parts),
        )
        try:
            async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                rsp = await client.post(
                    f"{self._api_base}/files?upload_id={upload_id}&object_key={object_key}",
                    content=self._json_bytes(payload),
                    headers=self._auth_headers({"Content-Type": "application/json;charset=UTF-8"}),
                )
            rsp.raise_for_status()
            body = rsp.json()
            self._raise_if_error(body, context="uploads complete error")
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.error(
                "[UnderstandingAPI] uploads_complete failed upload_id=%s object_key=%s "
                "parts=%d elapsed_ms=%.2f error=%s",
                upload_id,
                object_key,
                len(parts),
                elapsed_ms,
                self._exception_summary(exc),
                exc_info=True,
            )
            raise RuntimeError(
                "uploads complete failed: "
                f"upload_id={upload_id} object_key={object_key} parts={len(parts)} "
                f"error={self._exception_summary(exc)}"
            ) from exc
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "[UnderstandingAPI] uploads_complete done upload_id=%s object_key=%s "
            "file_id=%s status=%s elapsed_ms=%.2f",
            upload_id,
            object_key,
            body.get("id"),
            body.get("status"),
            elapsed_ms,
        )
        return body

    async def _multipart_create_file(
        self, file_path: Path, upload_name: Optional[str] = None
    ) -> Dict[str, Any]:
        init_obj = await self._uploads_init(file_path=file_path, upload_name=upload_name)
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

        missing_parts = [n for n in range(1, total_parts + 1) if n not in parts]
        logger.info(
            "[UnderstandingAPI] uploads_multipart plan upload_id=%s object_key=%s "
            "file_size=%d part_size=%d total_parts=%d uploaded_parts=%d "
            "missing_parts=%d upload_part_max_concurrent=%d",
            upload_id,
            object_key,
            file_size,
            part_size,
            total_parts,
            len(parts),
            len(missing_parts),
            self._upload_part_max_concurrent,
        )

        if missing_parts:
            uploaded_new = await self._uploads_put_parts_concurrently(
                file_path=file_path,
                upload_id=upload_id,
                object_key=object_key,
                part_size=part_size,
                part_numbers=missing_parts,
            )
            parts.update(uploaded_new)

        complete_obj = await self._uploads_complete(
            upload_id=upload_id,
            object_key=object_key,
            parts=[{"part_number": n, "etag": e} for n, e in sorted(parts.items())],
        )
        if complete_obj.get("status") != "active" or not complete_obj.get("id"):
            raise RuntimeError(f"uploads complete did not return file object: {complete_obj}")
        return complete_obj

    async def _uploads_put_parts_concurrently(
        self,
        *,
        file_path: Path,
        upload_id: str,
        object_key: str,
        part_size: int,
        part_numbers: List[int],
    ) -> Dict[int, str]:
        sem = asyncio.Semaphore(max(1, self._upload_part_max_concurrent))

        async def _upload_one(part_number: int) -> tuple[int, str]:
            offset = (part_number - 1) * part_size
            async with sem:
                with open(file_path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read(part_size)
                for attempt in range(1, _UPLOAD_PART_MAX_RETRIES + 1):
                    try:
                        part_obj = await self._uploads_put_part(
                            upload_id=upload_id,
                            object_key=object_key,
                            part_number=part_number,
                            data=chunk,
                        )
                        etag = part_obj.get("etag")
                        if not etag:
                            raise RuntimeError(
                                "uploads part missing etag: "
                                f"part={part_number} resp={self._safe_error_summary(part_obj)}"
                            )
                        return part_number, etag
                    except Exception as exc:
                        retryable = self._is_transient_upload_error(exc)
                        if not retryable or attempt >= _UPLOAD_PART_MAX_RETRIES:
                            logger.error(
                                "[UnderstandingAPI] uploads_part failed upload_id=%s object_key=%s "
                                "part_number=%d part_size=%d attempts=%d retryable=%s error=%s",
                                upload_id,
                                object_key,
                                part_number,
                                len(chunk),
                                attempt,
                                retryable,
                                self._exception_summary(exc),
                            )
                            raise
                        backoff = min(
                            _UPLOAD_PART_RETRY_BACKOFF_SEC * attempt, _POLL_MAX_BACKOFF_SEC
                        )
                        logger.warning(
                            "[UnderstandingAPI] uploads_part retry upload_id=%s object_key=%s "
                            "part_number=%d attempt=%d/%d backoff=%.1fs error=%s",
                            upload_id,
                            object_key,
                            part_number,
                            attempt,
                            _UPLOAD_PART_MAX_RETRIES,
                            backoff,
                            self._exception_summary(exc),
                        )
                        await asyncio.sleep(backoff)
                raise RuntimeError(
                    f"uploads part failed without result: upload_id={upload_id} "
                    f"object_key={object_key} part_number={part_number}"
                )

        tasks = [asyncio.create_task(_upload_one(n)) for n in part_numbers]
        uploaded: Dict[int, str] = {}
        try:
            for task in asyncio.as_completed(tasks):
                part_number, etag = await task
                uploaded[part_number] = etag
        except BaseException:
            # asyncio.CancelledError inherits BaseException on supported Python
            # versions, so cleanup must also cover cancellation from wait_for().
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return uploaded

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
