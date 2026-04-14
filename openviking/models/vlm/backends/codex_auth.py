from __future__ import annotations

import base64
import json
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Dict, Optional

import httpx
from openviking_cli.utils.config.consts import DEFAULT_CONFIG_DIR

try:
    import fcntl
except ImportError:
    fcntl = None

DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 300
_auth_lock_holder = threading.local()


class CodexAuthError(RuntimeError):
    pass


def _resolve_base_url() -> str:
    return (
        os.getenv("OPENVIKING_CODEX_BASE_URL", "").strip().rstrip("/")
        or DEFAULT_CODEX_BASE_URL
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
        return False
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
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        _auth_lock_holder.depth = 1
        try:
            yield
        finally:
            _auth_lock_holder.depth = 0
        return
    with open(lock_path, "a+", encoding="utf-8") as handle:
        _auth_lock_holder.depth = 1
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
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
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _format_expires_at(token: str) -> Optional[str]:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(float(exp), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _load_tokens_from_source(source: str, path: Path) -> Optional[Dict[str, Any]]:
    payload = _read_json_file(path)
    if not payload:
        return None
    if source == "openviking":
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
        }
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
    }


def _write_tokens_to_ov_store(
    path: Path,
    access_token: str,
    refresh_token: str,
    *,
    last_refresh: Optional[str] = None,
    imported_from: Optional[str] = None,
) -> None:
    with _auth_store_lock():
        payload = _read_json_file(path)
        payload["provider"] = "openai-codex"
        payload["auth_mode"] = "chatgpt"
        payload["tokens"] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
        if last_refresh is not None:
            payload["last_refresh"] = last_refresh
        if imported_from:
            payload["imported_from"] = imported_from
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def delete_codex_auth_store() -> bool:
    path = get_codex_auth_store_path()
    with _auth_store_lock():
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
) -> Path:
    path = get_codex_auth_store_path()
    _write_tokens_to_ov_store(
        path,
        access_token,
        refresh_token,
        imported_from=imported_from,
        last_refresh=last_refresh or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
        "bootstrap_available": bool(bootstrap_path and _load_tokens_from_source("codex-cli", bootstrap_path)),
        "env_override": bool(os.getenv("OPENVIKING_CODEX_ACCESS_TOKEN", "").strip()),
        "provider": "openai-codex",
    }
    if store_payload:
        status["last_refresh"] = store_payload.get("last_refresh")
        status["expires_at"] = _format_expires_at(store_payload["access_token"])
        payload = _read_json_file(store_path)
        status["imported_from"] = payload.get("imported_from")
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
    )


def login_codex_with_device_code(
    *,
    timeout_seconds: float = 15.0,
    max_wait_seconds: int = 900,
) -> Path:
    with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
        response = client.post(
            f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": CODEX_OAUTH_CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise CodexAuthError(f"Codex device login request failed with status {response.status_code}.")
    payload = response.json()
    user_code = str(payload.get("user_code", "") or "").strip()
    device_auth_id = str(payload.get("device_auth_id", "") or "").strip()
    if not user_code or not device_auth_id:
        raise CodexAuthError("Codex device login response is missing required fields.")
    poll_interval = max(3, int(payload.get("interval", "5")))
    print("Open this URL in your browser:")
    print(f"  {CODEX_OAUTH_ISSUER}/codex/device")
    print("Enter this code:")
    print(f"  {user_code}")
    print("Waiting for sign-in...")
    start = time.monotonic()
    auth_code_payload = None
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
            while time.monotonic() - start < max_wait_seconds:
                time.sleep(poll_interval)
                poll = client.post(
                    f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    auth_code_payload = poll.json()
                    break
                if poll.status_code in (403, 404):
                    continue
                raise CodexAuthError(f"Codex device auth polling failed with status {poll.status_code}.")
    except KeyboardInterrupt as exc:
        raise CodexAuthError("Codex device login cancelled.") from exc
    if auth_code_payload is None:
        raise CodexAuthError("Codex device login timed out.")
    authorization_code = str(auth_code_payload.get("authorization_code", "") or "").strip()
    code_verifier = str(auth_code_payload.get("code_verifier", "") or "").strip()
    if not authorization_code or not code_verifier:
        raise CodexAuthError("Codex device login response is missing authorization_code or code_verifier.")
    with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
        token_response = client.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": f"{CODEX_OAUTH_ISSUER}/deviceauth/callback",
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if token_response.status_code != 200:
        raise CodexAuthError(f"Codex token exchange failed with status {token_response.status_code}.")
    tokens = token_response.json()
    access_token = str(tokens.get("access_token", "") or "").strip()
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()
    if not access_token or not refresh_token:
        raise CodexAuthError("Codex token exchange did not return both access_token and refresh_token.")
    return save_codex_tokens(access_token, refresh_token)


def refresh_codex_oauth(
    access_token: str,
    refresh_token: str,
    *,
    timeout_seconds: float = 20.0,
) -> Dict[str, str]:
    del access_token
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise CodexAuthError("Codex OAuth refresh_token is missing.")
    try:
        response = httpx.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.strip(),
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise CodexAuthError(f"Codex OAuth refresh failed: {exc}") from exc
    if response.status_code != 200:
        message = f"Codex OAuth refresh failed with status {response.status_code}."
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("error_description") or payload.get("message") or payload.get("error")
            if isinstance(detail, str) and detail.strip():
                message = f"Codex OAuth refresh failed: {detail.strip()}"
        raise CodexAuthError(message)
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
    if os.getenv("OPENVIKING_CODEX_ACCESS_TOKEN", "").strip():
        return True
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
    env_access_token = os.getenv("OPENVIKING_CODEX_ACCESS_TOKEN", "").strip()
    env_refresh_token = os.getenv("OPENVIKING_CODEX_REFRESH_TOKEN", "").strip()
    if env_access_token:
        access_token = env_access_token
        refresh_token = env_refresh_token
        if (force_refresh or (
            refresh_if_expiring
            and refresh_token
            and _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
        )):
            refreshed = refresh_codex_oauth(access_token, refresh_token)
            access_token = refreshed["access_token"]
            refresh_token = refreshed["refresh_token"]
        return {
            "provider": "openai-codex",
            "api_key": access_token,
            "refresh_token": refresh_token,
            "base_url": _resolve_base_url(),
            "source": "env",
        }

    for source, path in _candidate_auth_sources():
        payload = _load_tokens_from_source(source, path)
        if payload is None:
            continue
        access_token = payload["access_token"]
        refresh_token = payload["refresh_token"]
        ov_auth_path = (
            path if source == "openviking"
            else Path(os.getenv("OPENVIKING_CODEX_AUTH_PATH", "").strip()).expanduser()
            if os.getenv("OPENVIKING_CODEX_AUTH_PATH", "").strip()
            else _default_openviking_auth_path()
        )
        if source != "openviking":
            _write_tokens_to_ov_store(
                ov_auth_path,
                access_token,
                refresh_token,
                last_refresh=payload.get("last_refresh"),
                imported_from=str(path),
            )
        should_refresh = force_refresh or (
            refresh_if_expiring
            and _codex_access_token_is_expiring(access_token, refresh_skew_seconds)
        )
        if should_refresh:
            refreshed = refresh_codex_oauth(access_token, refresh_token)
            access_token = refreshed["access_token"]
            refresh_token = refreshed["refresh_token"]
            _write_tokens_to_ov_store(
                ov_auth_path,
                access_token,
                refresh_token,
                last_refresh=refreshed.get("last_refresh"),
                imported_from=None if source == "openviking" else str(path),
            )
        return {
            "provider": "openai-codex",
            "api_key": access_token,
            "refresh_token": refresh_token,
            "base_url": _resolve_base_url(),
            "source": "openviking-auth-store" if source != "openviking" else source,
            "path": str(ov_auth_path),
        }

    raise CodexAuthError(
        "No Codex OAuth credentials found. Set OPENVIKING_CODEX_ACCESS_TOKEN, populate ~/.openviking/codex_auth.json, or bootstrap from an existing Codex CLI auth file."
    )
