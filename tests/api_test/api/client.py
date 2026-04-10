import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from config import Config


class OpenVikingAPIClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        account: Optional[str] = None,
        user: Optional[str] = None,
    ):
        self.base_url = base_url or Config.CONSOLE_URL
        self.server_url = server_url or Config.SERVER_URL
        self.api_key = api_key or Config.OPENVIKING_API_KEY
        self.account = account or Config.OPENVIKING_ACCOUNT
        self.user = user or Config.OPENVIKING_USER
        self.session = requests.Session()
        self._setup_default_headers()
        self.max_retries = 3
        self.retry_delay = 0.5
        self.last_request_info = None
        self.last_response = None

    def _filter_sensitive_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """过滤敏感头信息"""
        filtered = {}
        sensitive_headers_lower = {"authorization"}
        for key, value in headers.items():
            if key.lower() in sensitive_headers_lower:
                filtered[key] = "[REDACTED]"
            else:
                filtered[key] = value
        return filtered

    def _filter_sensitive_data(self, data: Any) -> Any:
        """过滤敏感数据"""
        if isinstance(data, dict):
            filtered = {}
            for key, value in data.items():
                if key.lower() in ["api_key", "apikey", "password", "secret", "token"]:
                    filtered[key] = "[REDACTED]"
                else:
                    filtered[key] = self._filter_sensitive_data(value)
            return filtered
        elif isinstance(data, list):
            return [self._filter_sensitive_data(item) for item in data]
        else:
            return data

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        filtered_headers = self._filter_sensitive_headers(dict(self.session.headers))
        filtered_kwargs = self._filter_sensitive_data(kwargs)

        self.last_request_info = {
            "method": method,
            "url": url,
            "headers": filtered_headers,
            **filtered_kwargs,
        }
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, **kwargs)
                self.last_response = response
                return response
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ):
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(self.retry_delay * (attempt + 1))

    def to_curl(self) -> Optional[str]:
        if not self.last_request_info:
            return None

        info = self.last_request_info
        parts = ["curl -X", info["method"].upper()]

        if "headers" in info:
            for key, value in info["headers"].items():
                parts.append(f'-H "{key}: {value}"')

        if "json" in info and info["json"]:
            parts.append(f"-d '{json.dumps(info['json'], ensure_ascii=False)}'")
            parts.append('-H "Content-Type: application/json"')

        parts.append(f'"{info["url"]}"')
        return " ".join(parts)

    def _setup_default_headers(self):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "X-OpenViking-Account": self.account,
            "X-OpenViking-User": self.user,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self.session.headers.update(headers)

    def _build_url(self, base: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
        url = f"{base}{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _upload_temp_file(self, file_path: str) -> str:
        endpoint = "/api/v1/resources/temp_upload"
        url = self._build_url(self.server_url, endpoint)
        original_content_type = self.session.headers.pop("Content-Type", None)
        try:
            with open(file_path, "rb") as file_obj:
                response = self._request_with_retry(
                    "POST",
                    url,
                    files={"file": (Path(file_path).name, file_obj)},
                )
        finally:
            if original_content_type is not None:
                self.session.headers["Content-Type"] = original_content_type

        response.raise_for_status()
        data = response.json()
        return data["result"]["temp_file_id"]

    def _zip_directory_for_upload(self, directory_path: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            temp_zip_path = tmp_file.name
        try:
            with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                base_path = Path(directory_path)
                for file_path in base_path.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, arcname=file_path.relative_to(base_path))
            return temp_zip_path
        except Exception:
            Path(temp_zip_path).unlink(missing_ok=True)
            raise

    def find(
        self,
        query: str,
        target_uri: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        endpoint = "/api/v1/search/find"
        url = self._build_url(self.server_url, endpoint)
        payload = {"query": query, "limit": limit}
        if target_uri:
            payload["target_uri"] = target_uri
        if score_threshold is not None:
            payload["score_threshold"] = score_threshold
        if filter:
            payload["filter"] = filter
        return self._request_with_retry("POST", url, json=payload)

    def search(
        self,
        query: str,
        target_uri: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        endpoint = "/api/v1/search/search"
        url = self._build_url(self.server_url, endpoint)
        payload = {"query": query, "limit": limit}
        if target_uri:
            payload["target_uri"] = target_uri
        if session_id:
            payload["session_id"] = session_id
        if score_threshold is not None:
            payload["score_threshold"] = score_threshold
        if filter:
            payload["filter"] = filter
        return self._request_with_retry("POST", url, json=payload)

    def grep(
        self,
        uri: str,
        pattern: str,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        exclude_uri: Optional[str] = None,
    ) -> requests.Response:
        endpoint = "/api/v1/search/grep"
        url = self._build_url(self.server_url, endpoint)
        payload = {"uri": uri, "pattern": pattern, "case_insensitive": case_insensitive}
        if node_limit is not None:
            payload["node_limit"] = node_limit
        if exclude_uri is not None:
            payload["exclude_uri"] = exclude_uri
        return self._request_with_retry("POST", url, json=payload)

    def glob(self, pattern: str, uri: Optional[str] = None) -> requests.Response:
        endpoint = "/api/v1/search/glob"
        url = self._build_url(self.server_url, endpoint)
        payload = {"pattern": pattern}
        if uri:
            payload["uri"] = uri
        return self._request_with_retry("POST", url, json=payload)

    def read_content(self, uri: str, offset: int = 0, limit: int = -1) -> requests.Response:
        endpoint = "/console/api/v1/ov/content/read"
        params = {"uri": uri, "offset": offset, "limit": limit}
        url = self._build_url(self.base_url, endpoint, params)
        return self.session.get(url)

    def list_contents(self, path: str, offset: int = 0, limit: int = 100) -> requests.Response:
        endpoint = "/console/api/v1/ov/content/list"
        params = {"path": path, "offset": offset, "limit": limit}
        url = self._build_url(self.base_url, endpoint, params)
        return self.session.get(url)

    def write_content(self, uri: str, content: str) -> requests.Response:
        endpoint = "/console/api/v1/ov/content/write"
        params = {"uri": uri}
        url = self._build_url(self.base_url, endpoint, params)
        return self.session.post(url, json={"content": content})

    def delete_content(self, uri: str) -> requests.Response:
        endpoint = "/console/api/v1/ov/content/delete"
        params = {"uri": uri}
        url = self._build_url(self.base_url, endpoint, params)
        return self.session.delete(url)

    def health_check(self) -> requests.Response:
        return self.session.get(f"{self.base_url}/")

    def server_health_check(self) -> requests.Response:
        return self.session.get(f"{self.server_url}/health")

    def add_resource(
        self,
        path: str,
        to: Optional[str] = None,
        reason: Optional[str] = None,
        wait: bool = False,
    ) -> requests.Response:
        endpoint = "/api/v1/resources"
        url = self._build_url(self.server_url, endpoint)
        payload = {}
        cleanup_path = None
        if os.path.isfile(path):
            payload["temp_file_id"] = self._upload_temp_file(path)
        elif os.path.isdir(path):
            cleanup_path = self._zip_directory_for_upload(path)
            payload["temp_file_id"] = self._upload_temp_file(cleanup_path)
        else:
            payload["path"] = path
        if to:
            payload["to"] = to
        if reason:
            payload["reason"] = reason
        if wait:
            payload["wait"] = wait
        try:
            return self._request_with_retry("POST", url, json=payload)
        finally:
            if cleanup_path is not None:
                Path(cleanup_path).unlink(missing_ok=True)

    def wait_processed(self) -> requests.Response:
        endpoint = "/api/v1/system/wait"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("POST", url, json={})

    def create_session(self, session_id: Optional[str] = None) -> requests.Response:
        endpoint = "/api/v1/sessions"
        url = self._build_url(self.server_url, endpoint)
        payload = {}
        if session_id:
            payload["session_id"] = session_id
        return self._request_with_retry("POST", url, json=payload)

    def list_sessions(self) -> requests.Response:
        endpoint = "/api/v1/sessions"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def get_session(self, session_id: str) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def get_session_context(self, session_id: str, token_budget: int = 128000) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}/context"
        params = {"token_budget": token_budget}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("GET", url)

    def delete_session(self, session_id: str) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("DELETE", url)

    def add_message(self, session_id: str, role: str, content: str) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}/messages"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("POST", url, json={"role": role, "content": content})

    def fs_ls(self, uri: str, simple: bool = False, recursive: bool = False) -> requests.Response:
        endpoint = "/api/v1/fs/ls"
        params = {"uri": uri, "simple": simple, "recursive": recursive}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("GET", url)

    def fs_tree(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/fs/tree"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("GET", url)

    def fs_stat(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/fs/stat"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("GET", url)

    def fs_mkdir(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/fs/mkdir"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("POST", url, json={"uri": uri})

    def fs_read(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/content/read"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("GET", url)

    def fs_write(
        self,
        uri: str,
        content: str,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> requests.Response:
        endpoint = "/api/v1/content/write"
        url = self._build_url(self.server_url, endpoint)
        payload = {
            "uri": uri,
            "content": content,
            "mode": mode,
            "wait": wait,
        }
        if timeout is not None:
            payload["timeout"] = timeout
        return self._request_with_retry("POST", url, json=payload)

    def fs_rm(self, uri: str, recursive: bool = False) -> requests.Response:
        endpoint = "/api/v1/fs"
        params = {"uri": uri, "recursive": recursive}
        url = self._build_url(self.server_url, endpoint, params)
        return self._request_with_retry("DELETE", url)

    def get_abstract(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/content/abstract"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self.session.get(url)

    def get_overview(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/content/overview"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self.session.get(url)

    def export_ovpack(self, uri: str, to: str) -> requests.Response:
        """Export ovpack and save to local file path.

        Args:
            uri: Viking URI to export
            to: Local file path where to save the .ovpack file

        Returns:
            Response object with the downloaded file saved to 'to' path
        """
        endpoint = "/api/v1/pack/export"
        url = self._build_url(self.server_url, endpoint)

        # Request export (server streams the file)
        response = self._request_with_retry("POST", url, json={"uri": uri})

        # Save streamed content to local file
        if response.status_code == 200:
            to_path = Path(to)
            to_path.parent.mkdir(parents=True, exist_ok=True)
            with open(to_path, "wb") as f:
                f.write(response.content)

        return response

    def import_ovpack(
        self, file_path: str, parent: str, force: bool = False, vectorize: bool = True
    ) -> requests.Response:
        endpoint = "/api/v1/pack/import"
        url = self._build_url(self.server_url, endpoint)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Local ovpack file not found: {file_path}")
        payload = {"parent": parent, "force": force, "vectorize": vectorize}
        payload["temp_file_id"] = self._upload_temp_file(file_path)
        return self._request_with_retry(
            "POST",
            url,
            json=payload,
        )

    def fs_mv(self, from_uri: str, to_uri: str) -> requests.Response:
        endpoint = "/api/v1/fs/mv"
        url = self._build_url(self.server_url, endpoint)
        return self.session.post(url, json={"from_uri": from_uri, "to_uri": to_uri})

    def link(self, from_uri: str, to_uris: Any, reason: str = "") -> requests.Response:
        endpoint = "/api/v1/relations/link"
        url = self._build_url(self.server_url, endpoint)
        return self.session.post(
            url, json={"from_uri": from_uri, "to_uris": to_uris, "reason": reason}
        )

    def relations(self, uri: str) -> requests.Response:
        endpoint = "/api/v1/relations"
        params = {"uri": uri}
        url = self._build_url(self.server_url, endpoint, params)
        return self.session.get(url)

    def unlink(self, from_uri: str, to_uri: str) -> requests.Response:
        endpoint = "/api/v1/relations/link"
        url = self._build_url(self.server_url, endpoint)
        return self.session.delete(url, json={"from_uri": from_uri, "to_uri": to_uri})

    def session_used(
        self,
        session_id: str,
        contexts: Optional[list] = None,
        skill: Optional[Dict[str, Any]] = None,
    ) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}/used"
        url = self._build_url(self.server_url, endpoint)
        payload = {}
        if contexts:
            payload["contexts"] = contexts
        if skill:
            payload["skill"] = skill
        return self.session.post(url, json=payload)

    def session_commit(self, session_id: str) -> requests.Response:
        endpoint = f"/api/v1/sessions/{session_id}/commit"
        url = self._build_url(self.server_url, endpoint)
        return self.session.post(url, json={})

    def system_status(self) -> requests.Response:
        endpoint = "/api/v1/system/status"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def system_wait(self, timeout: Optional[float] = None) -> requests.Response:
        endpoint = "/api/v1/system/wait"
        url = self._build_url(self.server_url, endpoint)
        payload = {}
        if timeout is not None:
            payload["timeout"] = timeout
        return self._request_with_retry("POST", url, json=payload)

    def observer_queue(self) -> requests.Response:
        endpoint = "/api/v1/observer/queue"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def observer_vikingdb(self) -> requests.Response:
        endpoint = "/api/v1/observer/vikingdb"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def observer_vlm(self) -> requests.Response:
        endpoint = "/api/v1/observer/vlm"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def observer_system(self) -> requests.Response:
        endpoint = "/api/v1/observer/system"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def is_healthy(self) -> requests.Response:
        endpoint = "/api/v1/debug/health"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def get_task(self, task_id: str) -> requests.Response:
        endpoint = f"/api/v1/tasks/{task_id}"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def wait_for_task(self, task_id: str, timeout: float = 60.0, poll_interval: float = 1.0) -> dict:
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.get_task(task_id)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'ok':
                    result = data.get('result', {})
                    task_status = result.get('status')
                    if task_status in ['completed', 'failed']:
                        return result
            time.sleep(poll_interval)
        return {'status': 'timeout', 'task_id': task_id}

    def admin_create_account(self, account_id: str, admin_user_id: str) -> requests.Response:
        endpoint = "/api/v1/admin/accounts"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry(
            "POST", url, json={"account_id": account_id, "admin_user_id": admin_user_id}
        )

    def admin_list_accounts(self) -> requests.Response:
        endpoint = "/api/v1/admin/accounts"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def admin_delete_account(self, account_id: str) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("DELETE", url)

    def admin_register_user(
        self, account_id: str, user_id: str, role: str = "user"
    ) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}/users"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("POST", url, json={"user_id": user_id, "role": role})

    def admin_list_users(self, account_id: str) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}/users"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("GET", url)

    def admin_remove_user(self, account_id: str, user_id: str) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}/users/{user_id}"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("DELETE", url)

    def admin_set_role(self, account_id: str, user_id: str, role: str) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}/users/{user_id}/role"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("PUT", url, json={"role": role})

    def admin_regenerate_key(self, account_id: str, user_id: str) -> requests.Response:
        endpoint = f"/api/v1/admin/accounts/{account_id}/users/{user_id}/key"
        url = self._build_url(self.server_url, endpoint)
        return self._request_with_retry("POST", url, json={})

    def add_skill(
        self, data: Any, wait: bool = False, timeout: Optional[float] = None
    ) -> requests.Response:
        endpoint = "/api/v1/skills"
        url = self._build_url(self.server_url, endpoint)
        payload = {"data": data}
        if wait:
            payload["wait"] = wait
        if timeout is not None:
            payload["timeout"] = timeout
        return self._request_with_retry("POST", url, json=payload)

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
