# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx

from openviking_cli.utils.config.consts import DEFAULT_CONFIG_DIR

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
# Public Codex device-auth client_id, not a secret; env or existing auth is preferred, and this is only a compatibility fallback.
DEFAULT_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 300
CODEX_AUTH_OWNER_OPENVIKING = "openviking"
CODEX_AUTH_OWNER_EXTERNAL = "external"
_auth_lock_holder = threading.local()


class CodexAuthError(RuntimeError):
    pass


def _validate_allowed_url(value: str, *, expected: str, label: str) -> str:
    """Return an exact pilot endpoint or fail before any network access."""
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    expected_parsed = urlsplit(expected)
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise CodexAuthError(f"Codex {label} endpoint is not an allowed HTTPS origin.") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname != expected_parsed.hostname
        or parsed_port not in (None, 443)
        or parsed.path.rstrip("/") != expected_parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        raise CodexAuthError(f"Codex {label} endpoint is not an allowed HTTPS origin.")
    return expected


def validate_codex_base_url(value: str | None = None) -> str:
    """Validate the only Responses origin permitted by the OAuth pilot."""
    configured = (value or os.getenv("OPENVIKING_CODEX_BASE_URL", "")).strip()
    return _validate_allowed_url(
        configured or DEFAULT_CODEX_BASE_URL,
        expected=DEFAULT_CODEX_BASE_URL,
        label="Responses",
    )


def _resolve_base_url() -> str:
    return validate_codex_base_url()


def _resolve_codex_oauth_issuer() -> str:
    configured = os.getenv("OPENVIKING_CODEX_OAUTH_ISSUER", "").strip()
    return _validate_allowed_url(
        configured or CODEX_OAUTH_ISSUER,
        expected=CODEX_OAUTH_ISSUER,
        label="OAuth issuer",
    )


def _resolve_codex_oauth_token_url() -> str:
    override = os.getenv("OPENVIKING_CODEX_OAUTH_TOKEN_URL", "").strip()
    expected = f"{CODEX_OAUTH_ISSUER}/oauth/token"
    if override:
        return _validate_allowed_url(override, expected=expected, label="OAuth token")
    return _validate_allowed_url(
        f"{_resolve_codex_oauth_issuer()}/oauth/token",
        expected=expected,
        label="OAuth token",
    )


def _decode_jwt_claims(token: Any) -> Dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        claims = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _codex_access_token_is_expiring(access_token: Any, skew_seconds: int) -> bool:
    claims = _decode_jwt_claims(access_token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return float(exp) <= (time.time() + max(0, int(skew_seconds)))


def _default_codex_auth_path() -> Path:
    codex_home = os.getenv("CODEX_HOME", "").strip()
    if not codex_home:
        codex_home = str(Path.home() / ".codex")
    return Path(codex_home).expanduser() / "auth.json"


def _default_openviking_auth_path() -> Path:
    return DEFAULT_CONFIG_DIR / "codex_auth.json"


def get_codex_auth_store_path() -> Path:
    override = os.getenv("OPENVIKING_CODEX_AUTH_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return _default_openviking_auth_path()


def _auth_lock_path() -> Path:
    return get_codex_auth_store_path().with_suffix(".lock")


def _ensure_private_auth_parent(path: Path) -> None:
    """Create/validate the auth parent without following a symlink."""
    parent = path.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise CodexAuthError("Codex auth store parent is unavailable.") from exc
    if stat.S_ISLNK(parent_stat.st_mode):
        raise CodexAuthError("Codex auth store parent must not be a symlink.")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and parent_stat.st_uid != getuid():
        raise CodexAuthError("Codex auth store parent has an unexpected owner.")
    try:
        os.chmod(parent, stat.S_IRWXU)
    except OSError as exc:
        raise CodexAuthError("Codex auth store parent permissions could not be secured.") from exc


def _reject_auth_symlink(path: Path) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CodexAuthError("Codex auth store is unavailable.") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise CodexAuthError("Codex auth store must not be a symlink.")
    getuid = getattr(os, "getuid", None)
    if getuid is not None and path_stat.st_uid != getuid():
        raise CodexAuthError("Codex auth store has an unexpected owner.")


def _acquire_windows_file_lock(handle) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _release_windows_file_lock(handle) -> None:
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _auth_store_lock():
    if getattr(_auth_lock_holder, "depth", 0) > 0:
        _auth_lock_holder.depth += 1
        try:
            yield
        finally:
            _auth_lock_holder.depth -= 1
        return
    lock_path = _auth_lock_path()
    _ensure_private_auth_parent(lock_path)
    _reject_auth_symlink(lock_path)
    if fcntl is None and msvcrt is None:
        _auth_lock_holder.depth = 1
        try:
            yield
        finally:
            _auth_lock_holder.depth = 0
        return
    open_mode = "a+b" if msvcrt is not None and fcntl is None else "a+"
    open_kwargs = {} if open_mode == "a+b" else {"encoding": "utf-8"}
    with open(lock_path, open_mode, **open_kwargs) as handle:
        try:
            os.chmod(lock_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise CodexAuthError("Codex auth lock permissions could not be secured.") from exc
        _auth_lock_holder.depth = 1
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:
                # Windows needs an explicit byte-range lock to coordinate access
                # across separate processes writing the shared auth store.
                _acquire_windows_file_lock(handle)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                else:
                    _release_windows_file_lock(handle)
            except OSError:
                pass
            _auth_lock_holder.depth = 0


def _candidate_auth_sources() -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    sources.append(("openviking", get_codex_auth_store_path()))
    import_override = os.getenv("OPENVIKING_CODEX_BOOTSTRAP_PATH", "").strip()
    if import_override:
        sources.append(("codex-cli", Path(import_override).expanduser()))
    else:
        sources.append(("codex-cli", _default_codex_auth_path()))
    return sources


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return {}
    getuid = getattr(os, "getuid", None)
    if getuid is not None and path_stat.st_uid != getuid():
        return {}
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    _ensure_private_auth_parent(path)
    _reject_auth_symlink(path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp_name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _format_expires_at(token: str) -> Optional[str]:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(float(exp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_codex_oauth_client_id(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("client_id", "oauth_client_id", "clientId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        for token_key in ("access_token", "id_token"):
            claims = _decode_jwt_claims(tokens.get(token_key))
            for claim_key in ("azp", "client_id"):
                value = claims.get(claim_key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _extract_codex_auth_owner(payload: Dict[str, Any]) -> str:
    owner = payload.get("auth_owner")
    if isinstance(owner, str) and owner.strip() in {
        CODEX_AUTH_OWNER_OPENVIKING,
        CODEX_AUTH_OWNER_EXTERNAL,
    }:
        return owner.strip()
    imported_from = payload.get("imported_from")
    if isinstance(imported_from, str) and imported_from.strip():
        return CODEX_AUTH_OWNER_EXTERNAL
    return CODEX_AUTH_OWNER_OPENVIKING


def _resolve_codex_oauth_client_id() -> str:
    override = os.getenv("OPENVIKING_CODEX_OAUTH_CLIENT_ID", "").strip()
    if override:
        return override
    for _, path in _candidate_auth_sources():
        payload = _read_json_file(path)
        if not payload:
            continue
        client_id = _extract_codex_oauth_client_id(payload)
        if client_id:
            return client_id
    return DEFAULT_CODEX_OAUTH_CLIENT_ID


def _load_tokens_from_source(source: str, path: Path) -> Optional[Dict[str, Any]]:
    payload = _read_json_file(path)
    if not payload:
        return None
    client_id = _extract_codex_oauth_client_id(payload)
    auth_owner = _extract_codex_auth_owner(payload)
    imported_from = payload.get("imported_from")
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = str(tokens.get("access_token", "") or "").strip()
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()
    if not access_token or not refresh_token:
        return None
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "last_refresh": payload.get("last_refresh"),
        "source": source,
        "path": path,
        "client_id": client_id,
        "auth_owner": auth_owner,
        "imported_from": imported_from,
    }


def _write_tokens_to_ov_store(
    path: Path,
    access_token: str,
    refresh_token: str,
    *,
    last_refresh: Optional[str] = None,
    imported_from: Optional[str] = None,
    client_id: Optional[str] = None,
    auth_owner: str = CODEX_AUTH_OWNER_OPENVIKING,
) -> None:
    with _auth_store_lock():
        payload = _read_json_file(path)
        payload["provider"] = "openai-codex"
        payload["auth_mode"] = "chatgpt"
        payload["auth_owner"] = (
            CODEX_AUTH_OWNER_EXTERNAL
            if auth_owner == CODEX_AUTH_OWNER_EXTERNAL
            else CODEX_AUTH_OWNER_OPENVIKING
        )
        payload["tokens"] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
        if last_refresh is not None:
            payload["last_refresh"] = last_refresh
        if imported_from:
            payload["imported_from"] = imported_from
        else:
            payload.pop("imported_from", None)
        resolved_client_id = client_id
        if not resolved_client_id:
            existing_client_id = payload.get("client_id")
            if isinstance(existing_client_id, str) and existing_client_id.strip():
                resolved_client_id = existing_client_id.strip()
        if resolved_client_id:
            payload["client_id"] = resolved_client_id
        _atomic_write_json_file(path, payload)


def delete_codex_auth_store() -> bool:
    path = get_codex_auth_store_path()
    with _auth_store_lock():
        _reject_auth_symlink(path)
        if not path.exists():
            return False
        path.unlink()
        return True


def save_codex_tokens(
    access_token: str,
    refresh_token: str,
    *,
    imported_from: Optional[str] = None,
    last_refresh: Optional[str] = None,
    client_id: Optional[str] = None,
    auth_owner: str = CODEX_AUTH_OWNER_OPENVIKING,
) -> Path:
    path = get_codex_auth_store_path()
    _write_tokens_to_ov_store(
        path,
        access_token,
        refresh_token,
        imported_from=imported_from,
        last_refresh=last_refresh or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        client_id=client_id,
        auth_owner=auth_owner,
    )
    return path


def get_codex_auth_status() -> Dict[str, Any]:
    store_path = get_codex_auth_store_path()
    store_payload = _load_tokens_from_source("openviking", store_path)
    bootstrap_path = None
    for source, path in _candidate_auth_sources():
        if source == "codex-cli":
            bootstrap_path = path
            break
    status: Dict[str, Any] = {
        "store_path": str(store_path),
        "store_exists": store_payload is not None,
        "bootstrap_path": str(bootstrap_path) if bootstrap_path else None,
        "bootstrap_available": bool(
            bootstrap_path and _load_tokens_from_source("codex-cli", bootstrap_path)
        ),
        "provider": "openai-codex",
    }
    if store_payload:
        status["last_refresh"] = store_payload.get("last_refresh")
        status["expires_at"] = _format_expires_at(store_payload["access_token"])
        payload = _read_json_file(store_path)
        status["imported_from"] = payload.get("imported_from")
        status["auth_owner"] = _extract_codex_auth_owner(payload)
        status["expiring"] = _codex_access_token_is_expiring(
            store_payload["access_token"],
            CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
        )
    return status


def bootstrap_codex_auth() -> Optional[Path]:
    bootstrap_path = None
    for source, path in _candidate_auth_sources():
        if source == "codex-cli":
            bootstrap_path = path
            break
    if bootstrap_path is None:
        return None
    payload = _load_tokens_from_source("codex-cli", bootstrap_path)
    if payload is None:
        return None
    return save_codex_tokens(
        payload["access_token"],
        payload["refresh_token"],
        imported_from=str(bootstrap_path),
        last_refresh=payload.get("last_refresh"),
        client_id=payload.get("client_id"),
        auth_owner=CODEX_AUTH_OWNER_EXTERNAL,
    )


def login_codex_with_device_code(
    *,
    timeout_seconds: float = 15.0,
    max_wait_seconds: int = 900,
) -> Path:
    issuer = _resolve_codex_oauth_issuer()
    client_id = _resolve_codex_oauth_client_id()
    token_url = _resolve_codex_oauth_token_url()
    with httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
    ) as client:
        response = client.post(
            f"{issuer}/api/accounts/deviceauth/usercode",
            json={"client_id": client_id},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise CodexAuthError(
            f"Codex device login request failed with status {response.status_code}."
        )
    payload = response.json()
    user_code = str(payload.get("user_code", "") or "").strip()
    device_auth_id = str(payload.get("device_auth_id", "") or "").strip()
    if not user_code or not device_auth_id:
        raise CodexAuthError("Codex device login response is missing required fields.")
    poll_interval = max(3, int(payload.get("interval", "5")))
    print("Open this URL in your browser:")
    print(f"  {issuer}/codex/device")
    print("Enter this code:")
    print(f"  {user_code}")
    print("Waiting for sign-in...")
    start = time.monotonic()
    auth_code_payload = None
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        ) as client:
            while time.monotonic() - start < max_wait_seconds:
                time.sleep(poll_interval)
                poll = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    auth_code_payload = poll.json()
                    break
                if poll.status_code in (403, 404):
                    continue
                raise CodexAuthError(
                    f"Codex device auth polling failed with status {poll.status_code}."
                )
    except KeyboardInterrupt as exc:
        raise CodexAuthError("Codex device login cancelled.") from exc
    if auth_code_payload is None:
        raise CodexAuthError("Codex device login timed out.")
    authorization_code = str(auth_code_payload.get("authorization_code", "") or "").strip()
    code_verifier = str(auth_code_payload.get("code_verifier", "") or "").strip()
    if not authorization_code or not code_verifier:
        raise CodexAuthError(
            "Codex device login response is missing authorization_code or code_verifier."
        )
    with httpx.Client(
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
    ) as client:
        token_response = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": f"{issuer}/deviceauth/callback",
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if token_response.status_code != 200:
        raise CodexAuthError(
            f"Codex token exchange failed with status {token_response.status_code}."
        )
    tokens = token_response.json()
    access_token = str(tokens.get("access_token", "") or "").strip()
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()
    if not access_token or not refresh_token:
        raise CodexAuthError(
            "Codex token exchange did not return both access_token and refresh_token."
        )
    return save_codex_tokens(
        access_token,
        refresh_token,
        client_id=client_id,
        auth_owner=CODEX_AUTH_OWNER_OPENVIKING,
    )


def refresh_codex_oauth(
    refresh_token: str,
    *,
    client_id: str,
    timeout_seconds: float = 20.0,
) -> Dict[str, str]:
    client_id = str(client_id or "").strip()
    if not client_id:
        raise CodexAuthError("Codex OAuth client_id is missing.")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("Codex OAuth refresh_token is missing.")
    try:
        response = httpx.post(
            _resolve_codex_oauth_token_url(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
                "client_id": client_id,
            },
            timeout=timeout_seconds,
            follow_redirects=False,
        )
    except Exception as exc:
        raise CodexAuthError("Codex OAuth refresh failed before a response was received.") from exc
    if response.status_code != 200:
        raise CodexAuthError(f"Codex OAuth refresh failed with status {response.status_code}.")
    try:
        payload = response.json()
    except Exception as exc:
        raise CodexAuthError("Codex OAuth refresh returned invalid JSON.") from exc
    access = str(payload.get("access_token", "") or "").strip()
    if not access:
        raise CodexAuthError("Codex OAuth refresh response is missing access_token.")
    next_refresh = str(payload.get("refresh_token", "") or "").strip() or refresh_token.strip()
    return {
        "access_token": access,
        "refresh_token": next_refresh,
        "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def has_codex_auth_available() -> bool:
    return any(
        _load_tokens_from_source(source, path) is not None
        for source, path in _candidate_auth_sources()
    )


def resolve_codex_runtime_credentials(
    *,
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
    refresh_skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
) -> Dict[str, Any]:
    ov_auth_path = get_codex_auth_store_path()
    payload = _load_tokens_from_source("openviking", ov_auth_path)
    if payload is None:
        for source, path in _candidate_auth_sources():
            if source == "openviking":
                continue
            payload = _load_tokens_from_source(source, path)
            if payload is None:
                continue
            _write_tokens_to_ov_store(
                ov_auth_path,
                payload["access_token"],
                payload["refresh_token"],
                last_refresh=payload.get("last_refresh"),
                imported_from=str(path),
                client_id=payload.get("client_id"),
                auth_owner=CODEX_AUTH_OWNER_EXTERNAL,
            )
            payload = _load_tokens_from_source("openviking", ov_auth_path)
            break
    if payload is not None:
        auth_owner = payload.get("auth_owner") or CODEX_AUTH_OWNER_OPENVIKING
        imported_from = payload.get("imported_from")
        access_token = payload["access_token"]
        refresh_token = payload["refresh_token"]
        if auth_owner == CODEX_AUTH_OWNER_EXTERNAL:
            external_path = None
            if isinstance(imported_from, str) and imported_from.strip():
                external_path = Path(imported_from).expanduser()
            elif os.getenv("OPENVIKING_CODEX_BOOTSTRAP_PATH", "").strip():
                external_path = Path(
                    os.getenv("OPENVIKING_CODEX_BOOTSTRAP_PATH", "").strip()
                ).expanduser()
            elif _default_codex_auth_path().exists():
                external_path = _default_codex_auth_path()
            external_missing = False
            should_resync = force_refresh or (
                refresh_if_expiring
                and _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
            )
            if should_resync:
                if external_path is not None:
                    external_payload = _load_tokens_from_source("codex-cli", external_path)
                    if external_payload is not None:
                        _write_tokens_to_ov_store(
                            ov_auth_path,
                            external_payload["access_token"],
                            external_payload["refresh_token"],
                            last_refresh=external_payload.get("last_refresh"),
                            imported_from=str(external_path),
                            client_id=external_payload.get("client_id"),
                            auth_owner=CODEX_AUTH_OWNER_EXTERNAL,
                        )
                        payload = external_payload
                        access_token = payload["access_token"]
                        refresh_token = payload["refresh_token"]
                    else:
                        external_missing = True
                else:
                    external_missing = True

            should_refresh = force_refresh or (
                refresh_if_expiring
                and _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
            )
            if should_refresh and external_missing:
                refreshed = refresh_codex_oauth(
                    refresh_token,
                    client_id=str(payload.get("client_id", "") or ""),
                )
                access_token = refreshed["access_token"]
                refresh_token = refreshed["refresh_token"]
                _write_tokens_to_ov_store(
                    ov_auth_path,
                    access_token,
                    refresh_token,
                    last_refresh=refreshed.get("last_refresh"),
                    imported_from=None,
                    client_id=payload.get("client_id"),
                    auth_owner=CODEX_AUTH_OWNER_OPENVIKING,
                )
                return {
                    "provider": "openai-codex",
                    "api_key": access_token,
                    "refresh_token": refresh_token,
                    "base_url": _resolve_base_url(),
                    "source": "openviking",
                    "path": str(ov_auth_path),
                    "auth_owner": CODEX_AUTH_OWNER_OPENVIKING,
                    "client_id": payload.get("client_id"),
                }

            if should_refresh:
                raise CodexAuthError(
                    "Externally managed Codex auth is expiring. Refresh it via Codex CLI or re-run openviking-server init."
                )

            return {
                "provider": "openai-codex",
                "api_key": access_token,
                "refresh_token": refresh_token,
                "base_url": _resolve_base_url(),
                "source": "codex-cli",
                "path": str(ov_auth_path),
                "auth_owner": CODEX_AUTH_OWNER_EXTERNAL,
                "client_id": payload.get("client_id"),
            }
        should_refresh = force_refresh or (
            refresh_if_expiring
            and _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
        )
        if should_refresh:
            refreshed = refresh_codex_oauth(
                refresh_token,
                client_id=str(payload.get("client_id", "") or ""),
            )
            access_token = refreshed["access_token"]
            refresh_token = refreshed["refresh_token"]
            _write_tokens_to_ov_store(
                ov_auth_path,
                access_token,
                refresh_token,
                last_refresh=refreshed.get("last_refresh"),
                imported_from=None,
                client_id=payload.get("client_id"),
                auth_owner=CODEX_AUTH_OWNER_OPENVIKING,
            )
        return {
            "provider": "openai-codex",
            "api_key": access_token,
            "refresh_token": refresh_token,
            "base_url": _resolve_base_url(),
            "source": "openviking",
            "path": str(ov_auth_path),
            "auth_owner": CODEX_AUTH_OWNER_OPENVIKING,
            "client_id": payload.get("client_id"),
        }

    raise CodexAuthError(
        "No Codex OAuth credentials found. Run openviking-server init or populate ~/.openviking/codex_auth.json."
    )


async def resolve_codex_runtime_credentials_async(
    *,
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
    refresh_skew_seconds: int = CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        resolve_codex_runtime_credentials,
        force_refresh=force_refresh,
        refresh_if_expiring=refresh_if_expiring,
        refresh_skew_seconds=refresh_skew_seconds,
    )
