from __future__ import annotations

import inspect
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

import httpx

from ._utils import run_async
from .config import resolve_client_config
from .errors import (
    AbortedError,
    AlreadyExistsError,
    ConflictError,
    DeadlineExceededError,
    EmbeddingFailedError,
    FailedPreconditionError,
    InternalError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    NotInitializedError,
    OpenVikingError,
    PermissionDeniedError,
    ProcessingError,
    ResourceExhaustedError,
    SessionExpiredError,
    UnauthenticatedError,
    UnavailableError,
    UnimplementedError,
    VLMFailedError,
)

ERROR_CODE_TO_EXCEPTION = {
    "INVALID_ARGUMENT": InvalidArgumentError,
    "INVALID_URI": InvalidURIError,
    "NOT_FOUND": NotFoundError,
    "ALREADY_EXISTS": AlreadyExistsError,
    "CONFLICT": ConflictError,
    "FAILED_PRECONDITION": FailedPreconditionError,
    "ABORTED": AbortedError,
    "UNAUTHENTICATED": UnauthenticatedError,
    "PERMISSION_DENIED": PermissionDeniedError,
    "RESOURCE_EXHAUSTED": ResourceExhaustedError,
    "UNAVAILABLE": UnavailableError,
    "INTERNAL": InternalError,
    "DEADLINE_EXCEEDED": DeadlineExceededError,
    "UNIMPLEMENTED": UnimplementedError,
    "NOT_INITIALIZED": NotInitializedError,
    "PROCESSING_ERROR": ProcessingError,
    "EMBEDDING_FAILED": EmbeddingFailedError,
    "VLM_FAILED": VLMFailedError,
    "SESSION_EXPIRED": SessionExpiredError,
    "UNKNOWN": OpenVikingError,
}


class VikingURI:
    @staticmethod
    def normalize(uri: str) -> str:
        if not uri:
            return uri
        if uri.startswith("viking://"):
            return uri
        if uri == "/":
            return "viking://"
        cleaned = uri.strip()
        if cleaned.startswith("/"):
            cleaned = cleaned[1:]
        return f"viking://{cleaned}"


class Session:
    def __init__(self, client: "AsyncHTTPClient", session_id: str):
        self._client = client
        self.session_id = session_id

    async def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        peer_id: str | None = None,
    ) -> Dict[str, Any]:
        return await self._client.add_message(
            self.session_id,
            role=role,
            content=content,
            parts=parts,
            created_at=created_at,
            peer_id=peer_id,
        )

    async def batch_add_messages(self, messages: list[dict]) -> Dict[str, Any]:
        return await self._client.batch_add_messages(self.session_id, messages)

    async def commit(self, keep_recent_count: int = 0) -> Dict[str, Any]:
        return await self._client.commit_session(
            self.session_id, keep_recent_count=keep_recent_count
        )

    async def delete(self) -> None:
        await self._client.delete_session(self.session_id)

    async def load(self) -> Dict[str, Any]:
        return await self._client.get_session(self.session_id)

    async def get_session_context(self, token_budget: int = 128_000) -> Dict[str, Any]:
        return await self._client.get_session_context(self.session_id, token_budget)

    async def get_archive(self, archive_id: str) -> Dict[str, Any]:
        return await self._client.get_session_archive(self.session_id, archive_id)


class SyncSession:
    def __init__(self, client: "SyncHTTPClient", session_id: str):
        self._client = client
        self.session_id = session_id

    def add_message(
        self,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        peer_id: str | None = None,
    ) -> Dict[str, Any]:
        return self._client.add_message(
            self.session_id,
            role=role,
            content=content,
            parts=parts,
            created_at=created_at,
            peer_id=peer_id,
        )

    def batch_add_messages(self, messages: list[dict]) -> Dict[str, Any]:
        return self._client.batch_add_messages(self.session_id, messages)

    def commit(
        self,
        telemetry: Any = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        return self._client.commit_session(
            self.session_id,
            telemetry=telemetry,
            keep_recent_count=keep_recent_count,
        )

    def commit_async(
        self,
        telemetry: Any = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        return self.commit(telemetry=telemetry, keep_recent_count=keep_recent_count)

    def delete(self) -> None:
        self._client.delete_session(self.session_id)

    def load(self) -> Dict[str, Any]:
        return self._client.get_session(self.session_id)

    def get_session_context(self, token_budget: int = 128_000) -> Dict[str, Any]:
        return self._client.get_session_context(self.session_id, token_budget)

    def get_archive(self, archive_id: str) -> Dict[str, Any]:
        return self._client.get_session_archive(self.session_id, archive_id)

    def __repr__(self) -> str:
        return f"SyncSession(id={self.session_id})"


class _HTTPObserver:
    def __init__(self, client: "AsyncHTTPClient"):
        self._client = client

    @property
    def queue(self) -> Dict[str, Any]:
        return run_async(self._client._get_queue_status())

    @property
    def vikingdb(self) -> Dict[str, Any]:
        return run_async(self._client._get_vikingdb_status())

    @property
    def models(self) -> Dict[str, Any]:
        return run_async(self._client._get_models_status())

    @property
    def system(self) -> Dict[str, Any]:
        return run_async(self._client._get_system_status())

    def is_healthy(self) -> bool:
        return self.system.get("is_healthy", False)


class AsyncHTTPClient:
    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        account: Optional[str] = None,
        user: Optional[str] = None,
        actor_peer_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout: float = 60.0,
        extra_headers: Optional[Dict[str, str]] = None,
        profile_enabled: Optional[bool] = None,
        upload_mode: Optional[str] = None,
    ):
        if actor_peer_id and agent_id:
            raise ValueError("actor_peer_id cannot be used with agent_id")
        effective_user = user if user is not None else user_id
        effective_actor = actor_peer_id if actor_peer_id is not None else agent_id
        config = resolve_client_config(
            url=url,
            api_key=api_key,
            account=account,
            user=effective_user,
            actor_peer_id=effective_actor,
            timeout=timeout,
            extra_headers=extra_headers,
            profile_enabled=profile_enabled,
            upload_mode=upload_mode,
        )
        self._url = config.url
        self._api_key = config.api_key
        self._account = config.account
        self._user_id = config.user
        self._actor_peer_id = config.actor_peer_id
        self._timeout = config.timeout
        self._extra_headers = config.extra_headers
        self._profile_enabled = config.profile_enabled
        self._upload_mode = config.upload_mode
        self._http: Optional[httpx.AsyncClient] = None
        self._observer: Optional[_HTTPObserver] = None
        self._snapshot: Optional["AsyncHTTPSnapshotNamespace"] = None

    async def initialize(self) -> None:
        headers: Dict[str, str] = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if self._account:
            headers["X-OpenViking-Account"] = self._account
        if self._user_id:
            headers["X-OpenViking-User"] = self._user_id
        if self._actor_peer_id:
            headers["X-OpenViking-Actor-Peer"] = self._actor_peer_id
        headers.update(self._extra_headers)
        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=self._timeout,
            params={"profile": "1"} if self._profile_enabled else None,
        )
        self._observer = _HTTPObserver(self)

    async def close(self) -> None:
        if self._http:
            try:
                await self._http.aclose()
            except RuntimeError:
                pass
            self._http = None

    @staticmethod
    def _path_segment(value: str) -> str:
        return quote(value, safe="")

    @staticmethod
    def _normalize_target_uri(
        target_uri: Union[str, List[str]],
    ) -> Union[str, List[str]]:
        if isinstance(target_uri, list):
            return [VikingURI.normalize(u) if u else u for u in target_uri]
        if target_uri:
            return VikingURI.normalize(target_uri)
        return target_uri

    @staticmethod
    def _compact_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
        """Drop None-valued keys (and an empty ``args`` object) from a request body.

        Older, stricter servers use ``model_config = ConfigDict(extra="forbid")`` and
        reject any field they do not yet define, so unconditionally attaching optional
        fields (even as ``null``/``{}``) breaks against instances that predate that
        field — e.g. ``body.tags`` against a pre-#2706 ``find`` route, or ``body.args``
        against a pre-#2549 ``resources`` route. Omitting them is safe for read/create
        routes where a missing optional field and an explicit ``null`` are equivalent.
        Do NOT use this for update/PATCH bodies where ``null`` may mean "clear this
        field". Mirrors the CLI's ``compact_request_body`` (see PR #2799).
        """
        compacted: Dict[str, Any] = {}
        for key, value in body.items():
            if value is None:
                continue
            # `args` is always attached by callers but absent from pre-#2549 models;
            # only forward it when arguments were actually provided.
            if key == "args" and isinstance(value, dict) and not value:
                continue
            compacted[key] = value
        return compacted

    def _handle_response_data(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            data = response.json()
        except Exception:
            if hasattr(response, "is_success") and not response.is_success:
                raise OpenVikingError(
                    f"HTTP {response.status_code}: {response.text or 'empty response'}",
                    code="INTERNAL",
                )
            return {}
        if data.get("status") == "error":
            self._raise_exception(data.get("error", {}))
        if hasattr(response, "is_success") and not response.is_success:
            raise OpenVikingError(
                data.get("detail", f"HTTP {response.status_code}"),
                code="UNKNOWN",
            )
        return data

    def _handle_response(self, response: httpx.Response) -> Any:
        return self._handle_response_data(response).get("result")

    def _raise_exception(self, error: Dict[str, Any]) -> None:
        code = error.get("code", "UNKNOWN")
        message = error.get("message", "Unknown error")
        details = error.get("details")
        exc_class = ERROR_CODE_TO_EXCEPTION.get(code, OpenVikingError)

        if exc_class == OpenVikingError:
            raise exc_class(message, code=code, details=details)
        if exc_class in (
            InvalidArgumentError,
            FailedPreconditionError,
            ResourceExhaustedError,
            AbortedError,
            UnimplementedError,
        ):
            raise exc_class(message, details=details)
        if exc_class == InvalidURIError:
            uri = details.get("uri", "") if details else ""
            reason = details.get("reason", "") if details else ""
            raise exc_class(uri, reason)
        if exc_class == NotFoundError:
            resource = details.get("resource", "") if details else ""
            resource_type = details.get("type", "resource") if details else "resource"
            raise exc_class(resource, resource_type)
        if exc_class == AlreadyExistsError:
            resource = details.get("resource", "") if details else ""
            resource_type = details.get("type", "resource") if details else "resource"
            raise exc_class(resource, resource_type)
        raise exc_class(message)

    def _zip_directory(self, dir_path: str) -> str:
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise ValueError(f"Path {dir_path} is not a directory")

        root = dir_path.resolve()
        zip_path = Path(tempfile.gettempdir()) / f"temp_upload_{uuid.uuid4().hex}.zip"
        entry_count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in dir_path.rglob("*"):
                if file_path.is_symlink():
                    continue
                if file_path.is_file():
                    if not file_path.resolve().is_relative_to(root):
                        continue
                    arcname = str(file_path.relative_to(dir_path)).replace("\\", "/")
                    zipf.write(file_path, arcname=arcname)
                    entry_count += 1
        return str(zip_path)

    async def _upload_temp_file(self, file_path: str) -> str:
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/octet-stream")}
            data = {"upload_mode": self._upload_mode} if self._upload_mode else None
            response = await self._http.post(
                "/api/v1/resources/temp_upload",
                files=files,
                data=data,
            )
        result = self._handle_response(response)
        return result.get("temp_file_id", "")

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> Session:
        return Session(self, session_id or "")

    async def session_exists(self, session_id: str) -> bool:
        try:
            await self.get_session(session_id)
            return True
        except NotFoundError:
            return False

    async def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        strict: bool = False,
        ignore_dirs: Optional[str] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
        directly_upload_media: bool = True,
        preserve_structure: Optional[bool] = None,
        watch_interval: float = 0,
        args: Optional[Dict[str, Any]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        if to and parent:
            raise ValueError("Cannot specify both 'to' and 'parent' at the same time.")

        request_data = {
            "to": to,
            "parent": parent,
            "reason": reason,
            "instruction": instruction,
            "wait": wait,
            "timeout": timeout,
            "strict": strict,
            "ignore_dirs": ignore_dirs,
            "include": include,
            "exclude": exclude,
            "directly_upload_media": directly_upload_media,
            "watch_interval": watch_interval,
            "args": args or {},
            "telemetry": telemetry,
        }
        if preserve_structure is not None:
            request_data["preserve_structure"] = preserve_structure

        path_obj = Path(path)
        if path_obj.exists():
            if path_obj.is_dir():
                request_data["source_name"] = path_obj.name
                zip_path = self._zip_directory(path)
                try:
                    request_data["temp_file_id"] = await self._upload_temp_file(zip_path)
                finally:
                    Path(zip_path).unlink(missing_ok=True)
            elif path_obj.is_file():
                request_data["source_name"] = path_obj.name
                request_data["temp_file_id"] = await self._upload_temp_file(path)
            else:
                request_data["path"] = path
        else:
            request_data["path"] = path

        request_data = self._compact_request_body(request_data)
        response = await self._http.post("/api/v1/resources", json=request_data)
        return self._handle_response_data(response).get("result", {})

    async def batch_add_messages(
        self,
        session_id: str,
        messages: list[dict],
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        session_path = self._path_segment(session_id)
        payload: Dict[str, Any] = {"messages": messages}
        if telemetry is not False:
            payload["telemetry"] = telemetry
        response = await self._http.post(
            f"/api/v1/sessions/{session_path}/messages/batch",
            json=payload,
        )
        return self._handle_response_data(response).get("result", {})

    async def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_data = {"wait": wait, "timeout": timeout, "telemetry": telemetry}
        if target_uri is not None:
            request_data["target_uri"] = target_uri
        if isinstance(data, str):
            path_obj = Path(data)
            if path_obj.exists():
                if path_obj.is_dir():
                    zip_path = self._zip_directory(data)
                    try:
                        request_data["temp_file_id"] = await self._upload_temp_file(zip_path)
                    finally:
                        Path(zip_path).unlink(missing_ok=True)
                elif path_obj.is_file():
                    request_data["temp_file_id"] = await self._upload_temp_file(data)
                else:
                    request_data["data"] = data
            else:
                request_data["data"] = data
        else:
            request_data["data"] = data
        response = await self._http.post("/api/v1/skills", json=request_data)
        return self._handle_response_data(response).get("result", {})

    async def list_skills(
        self,
        node_limit: int = 1000,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"node_limit": node_limit}
        if target_uri is not None:
            params["target_uri"] = target_uri
        response = await self._http.get("/api/v1/skills", params=params)
        return self._handle_response(response)

    async def find_skills(
        self,
        query: str,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        level: Optional[List[int]] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "query": query,
            "limit": limit,
            "score_threshold": score_threshold,
            "level": level,
            "telemetry": telemetry,
        }
        if target_uri is not None:
            payload["target_uri"] = target_uri
        response = await self._http.post("/api/v1/skills/find", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def validate_skill(
        self,
        data: Any,
        strict: bool = False,
        source_path: Optional[str] = None,
        skill_dir_name: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": data, "strict": strict}
        if source_path is not None:
            payload["source_path"] = source_path
        if skill_dir_name is not None:
            payload["skill_dir_name"] = skill_dir_name
        if target_uri is not None:
            payload["target_uri"] = target_uri
        response = await self._http.post("/api/v1/skills/validate", json=payload)
        return self._handle_response(response)

    async def get_skill(
        self,
        skill_name: str,
        include_content: Optional[bool] = None,
        include_files: bool = True,
        include_source: bool = False,
        level: Optional[int] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "include_files": include_files,
            "include_source": include_source,
        }
        if include_content is not None:
            params["include_content"] = include_content
        if level is not None:
            params["level"] = level
        if target_uri is not None:
            params["target_uri"] = target_uri
        response = await self._http.get(f"/api/v1/skills/{skill_name}", params=params)
        return self._handle_response(response)

    async def update_skill(
        self,
        skill_name: str,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_data: Dict[str, Any] = {
            "wait": wait,
            "timeout": timeout,
            "source_metadata": source_metadata,
            "telemetry": telemetry,
        }
        if target_uri is not None:
            request_data["target_uri"] = target_uri
        if isinstance(data, str):
            path_obj = Path(data)
            if path_obj.exists():
                if path_obj.is_dir():
                    zip_path = self._zip_directory(data)
                    try:
                        request_data["temp_file_id"] = await self._upload_temp_file(zip_path)
                    finally:
                        Path(zip_path).unlink(missing_ok=True)
                elif path_obj.is_file():
                    request_data["temp_file_id"] = await self._upload_temp_file(data)
                else:
                    request_data["data"] = data
            else:
                request_data["data"] = data
        else:
            request_data["data"] = data
        response = await self._http.put(f"/api/v1/skills/{skill_name}", json=request_data)
        return self._handle_response_data(response).get("result", {})

    async def delete_skill(
        self,
        skill_name: str,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if target_uri is not None:
            params["target_uri"] = target_uri
        response = await self._http.delete(f"/api/v1/skills/{skill_name}", params=params)
        return self._handle_response(response)

    async def list_watches(
        self,
        active_only: bool = False,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"active_only": active_only}
        if to_uri is not None:
            params["to_uri"] = VikingURI.normalize(to_uri)
        response = await self._http.get("/api/v1/watches", params=params)
        return self._handle_response(response)

    async def get_watch(
        self,
        task_id: str,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        params = {}
        if to_uri is not None:
            params["to_uri"] = VikingURI.normalize(to_uri)
        response = await self._http.get(f"/api/v1/watches/{task_id}", params=params)
        return self._handle_response(response)

    async def update_watch(
        self,
        task_id: Optional[str] = None,
        *,
        to_uri: Optional[str] = None,
        watch_interval: Optional[float] = None,
        is_active: Optional[bool] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not task_id and not to_uri:
            raise ValueError("Either task_id or to_uri is required")
        payload: Dict[str, Any] = {}
        if watch_interval is not None:
            payload["watch_interval"] = watch_interval
        if is_active is not None:
            payload["is_active"] = is_active
        if reason is not None:
            payload["reason"] = reason
        if instruction is not None:
            payload["instruction"] = instruction
        if task_id:
            params = {}
            if to_uri is not None:
                params["to_uri"] = VikingURI.normalize(to_uri)
            response = await self._http.patch(
                f"/api/v1/watches/{task_id}", params=params, json=payload
            )
        else:
            response = await self._http.patch(
                "/api/v1/watches",
                params={"to_uri": VikingURI.normalize(to_uri)},
                json=payload,
            )
        return self._handle_response(response)

    async def delete_watch(
        self, task_id: Optional[str] = None, *, to_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        if not task_id and not to_uri:
            raise ValueError("Either task_id or to_uri is required")
        if task_id:
            params = {}
            if to_uri is not None:
                params["to_uri"] = VikingURI.normalize(to_uri)
            response = await self._http.delete(f"/api/v1/watches/{task_id}", params=params)
        else:
            response = await self._http.delete(
                "/api/v1/watches", params={"to_uri": VikingURI.normalize(to_uri)}
            )
        return self._handle_response(response)

    async def trigger_watch(
        self, task_id: Optional[str] = None, *, to_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        if not task_id and not to_uri:
            raise ValueError("Either task_id or to_uri is required")
        if task_id:
            params = {}
            if to_uri is not None:
                params["to_uri"] = VikingURI.normalize(to_uri)
            response = await self._http.post(f"/api/v1/watches/{task_id}/trigger", params=params)
        else:
            response = await self._http.post(
                "/api/v1/watches/trigger", params={"to_uri": VikingURI.normalize(to_uri)}
            )
        return self._handle_response(response)

    async def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        http_timeout = timeout if timeout else 600.0
        response = await self._http.post(
            "/api/v1/system/wait",
            json={"timeout": timeout},
            timeout=http_timeout,
        )
        return self._handle_response(response)

    async def ls(
        self,
        uri: str,
        simple: bool = False,
        recursive: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Any]:
        response = await self._http.get(
            "/api/v1/fs/ls",
            params={
                "uri": VikingURI.normalize(uri),
                "simple": simple,
                "recursive": recursive,
                "output": output,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
        )
        return self._handle_response(response)

    async def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        response = await self._http.get(
            "/api/v1/fs/tree",
            params={
                "uri": VikingURI.normalize(uri),
                "output": output,
                "abs_limit": abs_limit,
                "show_all_hidden": show_all_hidden,
                "node_limit": node_limit,
            },
        )
        return self._handle_response(response)

    async def stat(self, uri: str) -> Dict[str, Any]:
        response = await self._http.get("/api/v1/fs/stat", params={"uri": VikingURI.normalize(uri)})
        return self._handle_response(response)

    async def attrs(self, uri: str) -> Dict[str, Any]:
        response = await self._http.get(
            "/api/v1/fs/attrs", params={"uri": VikingURI.normalize(uri)}
        )
        return self._handle_response(response)

    async def mkdir(self, uri: str, description: Optional[str] = None) -> None:
        payload = {"uri": VikingURI.normalize(uri)}
        if description is not None:
            payload["description"] = description
        response = await self._http.post("/api/v1/fs/mkdir", json=payload)
        self._handle_response(response)

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        params = {"uri": VikingURI.normalize(uri), "recursive": recursive, "wait": wait}
        if timeout is not None:
            params["timeout"] = timeout
        response = await self._http.request("DELETE", "/api/v1/fs", params=params)
        self._handle_response(response)

    async def mv(self, from_uri: str, to_uri: str) -> None:
        response = await self._http.post(
            "/api/v1/fs/mv",
            json={"from_uri": VikingURI.normalize(from_uri), "to_uri": VikingURI.normalize(to_uri)},
        )
        self._handle_response(response)

    async def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        response = await self._http.get(
            "/api/v1/content/read",
            params={"uri": VikingURI.normalize(uri), "offset": offset, "limit": limit},
        )
        return self._handle_response(response)

    async def abstract(self, uri: str) -> str:
        response = await self._http.get(
            "/api/v1/content/abstract", params={"uri": VikingURI.normalize(uri)}
        )
        return self._handle_response(response)

    async def overview(self, uri: str) -> str:
        response = await self._http.get(
            "/api/v1/content/overview", params={"uri": VikingURI.normalize(uri)}
        )
        return self._handle_response(response)

    async def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        response = await self._http.post(
            "/api/v1/content/write",
            json={
                "uri": VikingURI.normalize(uri),
                "content": content,
                "mode": mode,
                "wait": wait,
                "timeout": timeout,
                "telemetry": telemetry,
            },
        )
        return self._handle_response_data(response).get("result", {})

    async def set_tags(
        self,
        uri: str,
        tags: List[str],
        mode: str = "replace",
        recursive: bool = False,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        response = await self._http.post(
            "/api/v1/fs/attrs/set_tags",
            json={
                "uri": VikingURI.normalize(uri),
                "tags": tags,
                "mode": mode,
                "recursive": recursive,
                "telemetry": telemetry,
            },
        )
        return self._handle_response_data(response).get("result", {})

    async def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        actual_limit = node_limit if node_limit is not None else limit
        payload = {
            "query": query,
            "target_uri": self._normalize_target_uri(target_uri),
            "limit": actual_limit,
            "score_threshold": score_threshold,
            "filter": filter,
            "context_type": context_type,
            "tags": tags,
            "telemetry": telemetry,
        }
        payload = self._compact_request_body(payload)
        response = await self._http.post("/api/v1/search/find", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        actual_limit = node_limit if node_limit is not None else limit
        sid = session_id or (session.session_id if session else None)
        payload = {
            "query": query,
            "target_uri": self._normalize_target_uri(target_uri),
            "session_id": sid,
            "limit": actual_limit,
            "score_threshold": score_threshold,
            "filter": filter,
            "context_type": context_type,
            "tags": tags,
            "telemetry": telemetry,
        }
        payload = self._compact_request_body(payload)
        response = await self._http.post("/api/v1/search/search", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_json = {
            "uri": VikingURI.normalize(uri),
            "pattern": pattern,
            "case_insensitive": case_insensitive,
        }
        if node_limit is not None:
            request_json["node_limit"] = node_limit
        if exclude_uri is not None:
            request_json["exclude_uri"] = VikingURI.normalize(exclude_uri)
        response = await self._http.post("/api/v1/search/grep", json=request_json)
        return self._handle_response(response)

    async def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        response = await self._http.post(
            "/api/v1/search/glob",
            json={"pattern": pattern, "uri": VikingURI.normalize(uri)},
        )
        return self._handle_response(response)

    async def relations(self, uri: str) -> List[Any]:
        response = await self._http.get(
            "/api/v1/relations", params={"uri": VikingURI.normalize(uri)}
        )
        return self._handle_response(response)

    async def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        if isinstance(to_uris, str):
            to_uris = VikingURI.normalize(to_uris)
        else:
            to_uris = [VikingURI.normalize(u) for u in to_uris]
        response = await self._http.post(
            "/api/v1/relations/link",
            json={
                "from_uri": VikingURI.normalize(from_uri),
                "to_uris": to_uris,
                "reason": reason,
            },
        )
        self._handle_response(response)

    async def unlink(self, from_uri: str, to_uri: str) -> None:
        response = await self._http.request(
            "DELETE",
            "/api/v1/relations/link",
            json={
                "from_uri": VikingURI.normalize(from_uri),
                "to_uri": VikingURI.normalize(to_uri),
            },
        )
        self._handle_response(response)

    async def create_session(
        self,
        session_id: Optional[str] = None,
        telemetry: Any = False,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        json_body: Dict[str, Any] = {}
        if session_id is not None:
            json_body["session_id"] = session_id
        if memory_policy is not None:
            json_body["memory_policy"] = memory_policy
        if telemetry is not False:
            json_body["telemetry"] = telemetry
        response = await self._http.post("/api/v1/sessions", json=json_body)
        return self._handle_response_data(response).get("result", {})

    async def list_sessions(self) -> List[Any]:
        response = await self._http.get("/api/v1/sessions")
        return self._handle_response(response)

    async def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        params = {"auto_create": "true"} if auto_create else {}
        session_path = self._path_segment(session_id)
        response = await self._http.get(f"/api/v1/sessions/{session_path}", params=params)
        return self._handle_response(response)

    async def get_session_context(
        self, session_id: str, token_budget: int = 128_000
    ) -> Dict[str, Any]:
        session_path = self._path_segment(session_id)
        response = await self._http.get(
            f"/api/v1/sessions/{session_path}/context",
            params={"token_budget": token_budget},
        )
        return self._handle_response(response)

    async def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        session_path = self._path_segment(session_id)
        archive_path = self._path_segment(archive_id)
        response = await self._http.get(f"/api/v1/sessions/{session_path}/archives/{archive_path}")
        return self._handle_response(response)

    async def delete_session(self, session_id: str) -> None:
        session_path = self._path_segment(session_id)
        response = await self._http.delete(f"/api/v1/sessions/{session_path}")
        self._handle_response(response)

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        response = await self._http.get(f"/api/v1/tasks/{task_id}")
        if response.status_code == 404:
            return None
        return self._handle_response(response)

    async def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if task_type is not None:
            params["task_type"] = task_type
        if status is not None:
            params["status"] = status
        if resource_id is not None:
            params["resource_id"] = resource_id
        response = await self._http.get("/api/v1/tasks", params=params)
        return self._handle_response(response)

    async def commit_session(
        self,
        session_id: str,
        telemetry: Any = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        session_path = self._path_segment(session_id)
        response = await self._http.post(
            f"/api/v1/sessions/{session_path}/commit",
            json={"keep_recent_count": keep_recent_count, "telemetry": telemetry},
        )
        return self._handle_response_data(response).get("result", {})

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        peer_id: str | None = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"role": role}
        if parts is not None:
            payload["parts"] = parts
        elif content is not None:
            payload["content"] = content
        else:
            raise ValueError("Either content or parts must be provided")
        if created_at is not None:
            payload["created_at"] = created_at
        if peer_id is not None:
            payload["peer_id"] = peer_id
        if telemetry is not False:
            payload["telemetry"] = telemetry
        session_path = self._path_segment(session_id)
        response = await self._http.post(f"/api/v1/sessions/{session_path}/messages", json=payload)
        return self._handle_response_data(response).get("result", {})

    async def export_ovpack(
        self,
        uri: str,
        to: str,
        include_vectors: bool = False,
    ) -> str:
        uri = VikingURI.normalize(uri)
        to_path = Path(to)
        if to_path.is_dir():
            base_name = uri.strip().rstrip("/").split("/")[-1] or "export"
            to_path = to_path / f"{base_name}.ovpack"
        elif not str(to_path).endswith(".ovpack"):
            to_path = Path(str(to_path) + ".ovpack")
        to_path.parent.mkdir(parents=True, exist_ok=True)
        response = await self._http.post(
            "/api/v1/pack/export",
            json={"uri": uri, "include_vectors": include_vectors},
        )
        if not response.is_success:
            self._handle_response(response)
        with open(to_path, "wb") as f:
            f.write(response.content)
        return str(to_path)

    async def backup_ovpack(self, to: str, include_vectors: bool = False) -> str:
        to_path = Path(to)
        if to_path.is_dir():
            to_path = to_path / "openviking-backup.ovpack"
        elif not str(to_path).endswith(".ovpack"):
            to_path = Path(str(to_path) + ".ovpack")
        to_path.parent.mkdir(parents=True, exist_ok=True)
        response = await self._http.post(
            "/api/v1/pack/backup", json={"include_vectors": include_vectors}
        )
        if not response.is_success:
            self._handle_response(response)
        with open(to_path, "wb") as f:
            f.write(response.content)
        return str(to_path)

    async def import_ovpack(
        self,
        file_path: str,
        parent: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        request_data = {"parent": VikingURI.normalize(parent)}
        if on_conflict is not None:
            request_data["on_conflict"] = on_conflict
        if vector_mode is not None:
            request_data["vector_mode"] = vector_mode
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"Local ovpack file not found: {file_path}")
        if not file_path_obj.is_file():
            raise ValueError(f"Path {file_path} is not a file")
        request_data["temp_file_id"] = await self._upload_temp_file(file_path)
        response = await self._http.post("/api/v1/pack/import", json=request_data)
        result = self._handle_response(response)
        return result.get("uri", "")

    async def restore_ovpack(
        self,
        file_path: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        request_data = {}
        if on_conflict is not None:
            request_data["on_conflict"] = on_conflict
        if vector_mode is not None:
            request_data["vector_mode"] = vector_mode
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"Local ovpack file not found: {file_path}")
        if not file_path_obj.is_file():
            raise ValueError(f"Path {file_path} is not a file")
        request_data["temp_file_id"] = await self._upload_temp_file(file_path)
        response = await self._http.post("/api/v1/pack/restore", json=request_data)
        result = self._handle_response(response)
        return result.get("uri", "")

    async def check_consistency(self, uri: str) -> Dict[str, Any]:
        response = await self._http.post(
            "/api/v1/system/consistency",
            json={"uri": VikingURI.normalize(uri)},
        )
        return self._handle_response(response)

    async def health(self) -> bool:
        try:
            response = await self._http.get("/health")
            data = response.json()
            return data.get("status") == "ok"
        except Exception:
            return False

    async def reindex(
        self,
        uri: str,
        mode: str = "vectors_only",
        wait: bool = True,
    ) -> Dict[str, Any]:
        response = await self._http.post(
            "/api/v1/content/reindex",
            json={"uri": uri, "mode": mode, "wait": wait},
        )
        return self._handle_response(response)

    async def _get_queue_status(self) -> Dict[str, Any]:
        response = await self._http.get("/api/v1/observer/queue")
        return self._handle_response(response)

    async def _get_vikingdb_status(self) -> Dict[str, Any]:
        response = await self._http.get("/api/v1/observer/vikingdb")
        return self._handle_response(response)

    async def _get_models_status(self) -> Dict[str, Any]:
        response = await self._http.get("/api/v1/observer/models")
        return self._handle_response(response)

    async def _get_system_status(self) -> Dict[str, Any]:
        response = await self._http.get("/api/v1/observer/system")
        return self._handle_response(response)

    async def admin_create_account(
        self,
        account_id: str,
        admin_user_id: str,
        user_config: Optional[Dict[str, Any]] = None,
        seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"account_id": account_id, "admin_user_id": admin_user_id}
        if seed is not None:
            payload["seed"] = seed
        if user_config is not None:
            payload["user_config"] = user_config
        response = await self._http.post(
            "/api/v1/admin/accounts",
            json=payload,
        )
        return self._handle_response(response)

    async def admin_list_accounts(self) -> List[Any]:
        response = await self._http.get("/api/v1/admin/accounts")
        return self._handle_response(response)

    async def admin_delete_account(self, account_id: str) -> Dict[str, Any]:
        response = await self._http.delete(f"/api/v1/admin/accounts/{account_id}")
        return self._handle_response(response)

    async def admin_register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        user_config: Optional[Dict[str, Any]] = None,
        seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"user_id": user_id, "role": role}
        if seed is not None:
            payload["seed"] = seed
        if user_config is not None:
            payload["user_config"] = user_config
        response = await self._http.post(
            f"/api/v1/admin/accounts/{account_id}/users",
            json=payload,
        )
        return self._handle_response(response)

    async def admin_list_users(self, account_id: str) -> List[Any]:
        response = await self._http.get(f"/api/v1/admin/accounts/{account_id}/users")
        return self._handle_response(response)

    async def admin_remove_user(self, account_id: str, user_id: str) -> Dict[str, Any]:
        response = await self._http.delete(f"/api/v1/admin/accounts/{account_id}/users/{user_id}")
        return self._handle_response(response)

    async def admin_set_role(self, account_id: str, user_id: str, role: str) -> Dict[str, Any]:
        response = await self._http.put(
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/role",
            json={"role": role},
        )
        return self._handle_response(response)

    async def admin_regenerate_key(
        self, account_id: str, user_id: str, seed: Optional[str] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if seed is not None:
            payload["seed"] = seed
        response = await self._http.post(
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/key",
            json=payload,
        )
        return self._handle_response(response)

    async def admin_migrate(self, cleanup: bool = False) -> Dict[str, Any]:
        response = await self._http.post("/api/v1/admin/migrate", json={"cleanup": cleanup})
        return self._handle_response(response)

    def get_status(self) -> Dict[str, Any]:
        return run_async(self._get_system_status())

    def is_healthy(self) -> bool:
        return self.observer.is_healthy()

    @property
    def observer(self) -> _HTTPObserver:
        if self._observer is None:
            self._observer = _HTTPObserver(self)
        return self._observer

    # ============= Git Version Control =============

    async def git_commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a snapshot of the current workspace state."""
        body: Dict[str, Any] = {"message": message, "branch": branch}
        if paths is not None:
            body["paths"] = paths
        if author_name is not None:
            body["author_name"] = author_name
        if author_email is not None:
            body["author_email"] = author_email
        response = await self._http.post("/api/v1/snapshot/commit", json=body)
        return self._handle_response(response)

    async def git_restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forward-commit restore of a subtree, or the full account tree when project_dir is omitted."""
        body: Dict[str, Any] = {
            "source_commit": source_commit,
            "branch": branch,
            "dry_run": dry_run,
        }
        if project_dir is not None:
            body["project_dir"] = project_dir
        if message is not None:
            body["message"] = message
        if author_name is not None:
            body["author_name"] = author_name
        if author_email is not None:
            body["author_email"] = author_email
        response = await self._http.post("/api/v1/snapshot/restore", json=body)
        return self._handle_response(response)

    async def git_show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        """Fetch commit metadata (path=None) or a blob's {oid, size, bytes} (path=<uri>)."""
        params: Dict[str, Any] = {"target_ref": target_ref}
        if path is not None:
            params["path"] = path
        response = await self._http.get("/api/v1/snapshot/show", params=params)

        if path is None:
            return self._handle_response(response)

        # Binary branch: server sets application/octet-stream + X-Snapshot-* headers.
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/octet-stream"):
            return {
                "oid": response.headers.get("x-snapshot-oid", ""),
                "size": int(response.headers.get("x-snapshot-size", "0")),
                "bytes": response.content,
            }
        # Fallback: server returned a JSON error envelope. Let the standard handler raise.
        return self._handle_response(response)

    async def git_log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Walk commit history newest-first."""
        response = await self._http.get(
            "/api/v1/snapshot/log",
            params={"branch": branch, "limit": limit},
        )
        return self._handle_response(response)

    @property
    def snapshot(self) -> "AsyncHTTPSnapshotNamespace":
        """Snapshot version control namespace (async HTTP)."""
        if self._snapshot is None:
            self._snapshot = AsyncHTTPSnapshotNamespace(self)
        return self._snapshot


class SyncHTTPClient:
    def __init__(self, *args, **kwargs):
        self._async_client = AsyncHTTPClient(*args, **kwargs)
        self._initialized = False
        self._snapshot: Optional["SyncHTTPSnapshotNamespace"] = None

    def initialize(self) -> None:
        run_async(self._async_client.initialize())
        self._initialized = True

    def close(self) -> None:
        run_async(self._async_client.close())
        self._initialized = False

    def session(self, session_id: Optional[str] = None, must_exist: bool = False) -> SyncSession:
        if session_id and must_exist:
            self.get_session(session_id)
        return SyncSession(self, session_id or "")

    def session_exists(self, session_id: str) -> bool:
        return run_async(self._async_client.session_exists(session_id))

    def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        reason: str = "",
        instruction: str = "",
        wait: bool = False,
        timeout: Optional[float] = None,
        strict: bool = False,
        ignore_dirs: Optional[str] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
        directly_upload_media: bool = True,
        preserve_structure: Optional[bool] = None,
        watch_interval: float = 0,
        args: Optional[Dict[str, Any]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.add_resource(
                path=path,
                to=to,
                parent=parent,
                reason=reason,
                instruction=instruction,
                wait=wait,
                timeout=timeout,
                strict=strict,
                ignore_dirs=ignore_dirs,
                include=include,
                exclude=exclude,
                directly_upload_media=directly_upload_media,
                preserve_structure=preserve_structure,
                watch_interval=watch_interval,
                args=args,
                telemetry=telemetry,
            )
        )

    def batch_add_messages(
        self,
        session_id: str,
        messages: list[dict],
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        if telemetry is False:
            return run_async(self._async_client.batch_add_messages(session_id, messages))
        return run_async(self._async_client.batch_add_messages(session_id, messages, telemetry))

    def add_skill(
        self,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.add_skill(
                data,
                wait=wait,
                timeout=timeout,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def list_skills(
        self,
        node_limit: int = 1000,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.list_skills(node_limit=node_limit, target_uri=target_uri)
        )

    def find_skills(
        self,
        query: str,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        level: Optional[List[int]] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.find_skills(
                query=query,
                limit=limit,
                score_threshold=score_threshold,
                level=level,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def validate_skill(
        self,
        data: Any,
        strict: bool = False,
        source_path: Optional[str] = None,
        skill_dir_name: Optional[str] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.validate_skill(
                data=data,
                strict=strict,
                source_path=source_path,
                skill_dir_name=skill_dir_name,
                target_uri=target_uri,
            )
        )

    def get_skill(
        self,
        skill_name: str,
        include_content: Optional[bool] = None,
        include_files: bool = True,
        include_source: bool = False,
        level: Optional[int] = None,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.get_skill(
                skill_name,
                include_content=include_content,
                include_files=include_files,
                include_source=include_source,
                level=level,
                target_uri=target_uri,
            )
        )

    def update_skill(
        self,
        skill_name: str,
        data: Any,
        wait: bool = False,
        timeout: Optional[float] = None,
        source_metadata: Optional[Dict[str, Any]] = None,
        telemetry: Any = False,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.update_skill(
                skill_name,
                data,
                wait=wait,
                timeout=timeout,
                source_metadata=source_metadata,
                telemetry=telemetry,
                target_uri=target_uri,
            )
        )

    def delete_skill(
        self,
        skill_name: str,
        target_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.delete_skill(skill_name, target_uri=target_uri)
        )

    def list_watches(
        self,
        active_only: bool = False,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(self._async_client.list_watches(active_only=active_only, to_uri=to_uri))

    def get_watch(
        self,
        task_id: str,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(self._async_client.get_watch(task_id, to_uri=to_uri))

    def update_watch(
        self,
        task_id: Optional[str] = None,
        *,
        to_uri: Optional[str] = None,
        watch_interval: Optional[float] = None,
        is_active: Optional[bool] = None,
        reason: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.update_watch(
                task_id,
                to_uri=to_uri,
                watch_interval=watch_interval,
                is_active=is_active,
                reason=reason,
                instruction=instruction,
            )
        )

    def delete_watch(
        self,
        task_id: Optional[str] = None,
        *,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(self._async_client.delete_watch(task_id, to_uri=to_uri))

    def trigger_watch(
        self,
        task_id: Optional[str] = None,
        *,
        to_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(self._async_client.trigger_watch(task_id, to_uri=to_uri))

    def wait_processed(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        return run_async(self._async_client.wait_processed(timeout))

    def ls(
        self,
        uri: str,
        simple: bool = False,
        recursive: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Any]:
        return run_async(
            self._async_client.ls(
                uri,
                simple=simple,
                recursive=recursive,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
            )
        )

    def tree(
        self,
        uri: str,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        return run_async(
            self._async_client.tree(
                uri,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
            )
        )

    def stat(self, uri: str) -> Dict[str, Any]:
        return run_async(self._async_client.stat(uri))

    def attrs(self, uri: str) -> Dict[str, Any]:
        return run_async(self._async_client.attrs(uri))

    def mkdir(self, uri: str, description: Optional[str] = None) -> None:
        run_async(self._async_client.mkdir(uri, description=description))

    def rm(
        self,
        uri: str,
        recursive: bool = False,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        run_async(self._async_client.rm(uri, recursive=recursive, wait=wait, timeout=timeout))

    def mv(self, from_uri: str, to_uri: str) -> None:
        run_async(self._async_client.mv(from_uri, to_uri))

    def read(self, uri: str, offset: int = 0, limit: int = -1) -> str:
        return run_async(self._async_client.read(uri, offset=offset, limit=limit))

    def abstract(self, uri: str) -> str:
        return run_async(self._async_client.abstract(uri))

    def overview(self, uri: str) -> str:
        return run_async(self._async_client.overview(uri))

    def write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.write(
                uri=uri,
                content=content,
                mode=mode,
                wait=wait,
                timeout=timeout,
                telemetry=telemetry,
            )
        )

    def set_tags(
        self,
        uri: str,
        tags: List[str],
        mode: str = "replace",
        recursive: bool = False,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.set_tags(
                uri=uri,
                tags=tags,
                mode=mode,
                recursive=recursive,
                telemetry=telemetry,
            )
        )

    def find(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.find(
                query=query,
                target_uri=target_uri,
                limit=limit,
                node_limit=node_limit,
                score_threshold=score_threshold,
                filter=filter,
                context_type=context_type,
                tags=tags,
                telemetry=telemetry,
            )
        )

    def search(
        self,
        query: str,
        target_uri: Union[str, List[str]] = "",
        session: Optional[Any] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        node_limit: Optional[int] = None,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
        context_type: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        actual_session_id = session_id
        if actual_session_id is None and session is not None:
            actual_session_id = getattr(session, "session_id", None)
        return run_async(
            self._async_client.search(
                query=query,
                target_uri=target_uri,
                session_id=actual_session_id,
                limit=limit,
                node_limit=node_limit,
                score_threshold=score_threshold,
                filter=filter,
                context_type=context_type,
                tags=tags,
                telemetry=telemetry,
            )
        )

    def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.grep(
                uri=uri,
                pattern=pattern,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                exclude_uri=exclude_uri,
            )
        )

    def glob(self, pattern: str, uri: str = "viking://") -> Dict[str, Any]:
        return run_async(self._async_client.glob(pattern, uri=uri))

    def relations(self, uri: str) -> List[Any]:
        return run_async(self._async_client.relations(uri))

    def link(self, from_uri: str, to_uris: Union[str, List[str]], reason: str = "") -> None:
        run_async(self._async_client.link(from_uri, to_uris, reason=reason))

    def unlink(self, from_uri: str, to_uri: str) -> None:
        run_async(self._async_client.unlink(from_uri, to_uri))

    def create_session(
        self,
        session_id: Optional[str] = None,
        telemetry: Any = False,
        memory_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.create_session(
                session_id=session_id,
                telemetry=telemetry,
                memory_policy=memory_policy,
            )
        )

    def list_sessions(self) -> List[Any]:
        return run_async(self._async_client.list_sessions())

    def get_session(self, session_id: str, *, auto_create: bool = False) -> Dict[str, Any]:
        return run_async(self._async_client.get_session(session_id, auto_create=auto_create))

    def get_session_context(self, session_id: str, token_budget: int = 128_000) -> Dict[str, Any]:
        return run_async(self._async_client.get_session_context(session_id, token_budget))

    def get_session_archive(self, session_id: str, archive_id: str) -> Dict[str, Any]:
        return run_async(self._async_client.get_session_archive(session_id, archive_id))

    def delete_session(self, session_id: str) -> None:
        run_async(self._async_client.delete_session(session_id))

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return run_async(self._async_client.get_task(task_id))

    def list_tasks(
        self,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return run_async(
            self._async_client.list_tasks(
                task_type=task_type,
                status=status,
                resource_id=resource_id,
                limit=limit,
            )
        )

    def commit_session(
        self,
        session_id: str,
        telemetry: Any = False,
        *,
        keep_recent_count: int = 0,
    ) -> Dict[str, Any]:
        if telemetry is False:
            return run_async(
                self._async_client.commit_session(
                    session_id,
                    keep_recent_count=keep_recent_count,
                )
            )
        return run_async(
            self._async_client.commit_session(
                session_id,
                telemetry=telemetry,
                keep_recent_count=keep_recent_count,
            )
        )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        parts: list[dict] | None = None,
        created_at: str | None = None,
        peer_id: str | None = None,
        telemetry: Any = False,
    ) -> Dict[str, Any]:
        kwargs = {
            "role": role,
            "content": content,
            "parts": parts,
            "created_at": created_at,
            "peer_id": peer_id,
        }
        if telemetry is not False:
            kwargs["telemetry"] = telemetry
        return run_async(
            self._async_client.add_message(
                session_id,
                **kwargs,
            )
        )

    def export_ovpack(
        self,
        uri: str,
        to: str,
        include_vectors: bool = False,
    ) -> str:
        return run_async(self._async_client.export_ovpack(uri, to, include_vectors=include_vectors))

    def backup_ovpack(self, to: str, include_vectors: bool = False) -> str:
        return run_async(self._async_client.backup_ovpack(to, include_vectors=include_vectors))

    def import_ovpack(
        self,
        file_path: str,
        parent: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        return run_async(
            self._async_client.import_ovpack(
                file_path,
                parent,
                on_conflict=on_conflict,
                vector_mode=vector_mode,
            )
        )

    def restore_ovpack(
        self,
        file_path: str,
        on_conflict: Optional[str] = None,
        vector_mode: Optional[str] = None,
    ) -> str:
        return run_async(
            self._async_client.restore_ovpack(
                file_path,
                on_conflict=on_conflict,
                vector_mode=vector_mode,
            )
        )

    def check_consistency(self, uri: str) -> Dict[str, Any]:
        return run_async(self._async_client.check_consistency(uri))

    def health(self) -> bool:
        return run_async(self._async_client.health())

    def reindex(
        self,
        uri: str,
        mode: str = "vectors_only",
        wait: bool = True,
    ) -> Dict[str, Any]:
        return run_async(self._async_client.reindex(uri=uri, mode=mode, wait=wait))

    def admin_create_account(
        self,
        account_id: str,
        admin_user_id: str,
        user_config: Optional[Dict[str, Any]] = None,
        seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.admin_create_account(
                account_id,
                admin_user_id,
                seed=seed,
                user_config=user_config,
            )
        )

    def admin_list_accounts(self) -> List[Any]:
        return run_async(self._async_client.admin_list_accounts())

    def admin_delete_account(self, account_id: str) -> Dict[str, Any]:
        return run_async(self._async_client.admin_delete_account(account_id))

    def admin_register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        user_config: Optional[Dict[str, Any]] = None,
        seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._async_client.admin_register_user(
                account_id,
                user_id,
                role,
                seed=seed,
                user_config=user_config,
            )
        )

    def admin_list_users(self, account_id: str) -> List[Any]:
        return run_async(self._async_client.admin_list_users(account_id))

    def admin_remove_user(self, account_id: str, user_id: str) -> Dict[str, Any]:
        return run_async(self._async_client.admin_remove_user(account_id, user_id))

    def admin_set_role(self, account_id: str, user_id: str, role: str) -> Dict[str, Any]:
        return run_async(self._async_client.admin_set_role(account_id, user_id, role))

    def admin_regenerate_key(
        self, account_id: str, user_id: str, seed: Optional[str] = None
    ) -> Dict[str, Any]:
        return run_async(self._async_client.admin_regenerate_key(account_id, user_id, seed=seed))

    def admin_migrate(self, cleanup: bool = False) -> Dict[str, Any]:
        return run_async(self._async_client.admin_migrate(cleanup=cleanup))

    def get_status(self) -> Dict[str, Any]:
        return self._async_client.get_status()

    def is_healthy(self) -> bool:
        return self._async_client.is_healthy()

    @property
    def observer(self) -> _HTTPObserver:
        return self._async_client.observer

    @property
    def snapshot(self) -> "SyncHTTPSnapshotNamespace":
        """Snapshot version control namespace (sync HTTP)."""
        if self._snapshot is None:
            self._snapshot = SyncHTTPSnapshotNamespace(self)
        return self._snapshot

    def __getattr__(self, name: str):
        attr = getattr(self._async_client, name)
        if inspect.iscoroutinefunction(attr):

            def wrapper(*args, **kwargs):
                return run_async(attr(*args, **kwargs))

            return wrapper
        return attr


class AsyncHTTPSnapshotNamespace:
    """Snapshot version control namespace forwarding to AsyncHTTPClient git_* methods."""

    def __init__(self, client: "AsyncHTTPClient"):
        self._client = client

    async def commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._client.git_commit(
            message=message,
            paths=paths,
            branch=branch,
            author_name=author_name,
            author_email=author_email,
        )

    async def restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self._client.git_restore(
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
        )

    async def show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        return await self._client.git_show(target_ref, path=path)

    async def log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return await self._client.git_log(branch=branch, limit=limit)


class SyncHTTPSnapshotNamespace:
    """Synchronous wrapper around the HTTP client's snapshot namespace."""

    def __init__(self, client: "SyncHTTPClient"):
        self._client = client

    def _ns(self) -> AsyncHTTPSnapshotNamespace:
        return self._client._async_client.snapshot

    def commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._ns().commit(
                message=message,
                paths=paths,
                branch=branch,
                author_name=author_name,
                author_email=author_email,
            )
        )

    def restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        return run_async(
            self._ns().restore(
                project_dir=project_dir,
                source_commit=source_commit,
                branch=branch,
                dry_run=dry_run,
                message=message,
                author_name=author_name,
                author_email=author_email,
            )
        )

    def show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
    ) -> Any:
        return run_async(self._ns().show(target_ref, path=path))

    def log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return run_async(self._ns().log(branch=branch, limit=limit))
