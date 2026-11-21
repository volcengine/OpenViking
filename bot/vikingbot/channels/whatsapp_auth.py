"""Authentication helpers for the local WhatsApp bridge."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

AUTH_TIMEOUT_SECONDS = 5.0
_TOKEN_BYTES = 32
_TOKEN_READ_ATTEMPTS = 50
_TOKEN_READ_DELAY_SECONDS = 0.01


def _token_path(data_dir: Path) -> Path:
    return data_dir / "whatsapp" / "bridge-token"


def _read_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if len(token) < _TOKEN_BYTES or token.strip() != token:
        raise RuntimeError(f"Invalid WhatsApp bridge token file: {path}")
    return token


def resolve_bridge_token(configured_token: str, data_dir: Path) -> str:
    """Return the configured token or one shared through the bot data directory."""
    if configured_token:
        return configured_token

    token_path = _token_path(data_dir)
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    last_read_error: RuntimeError | None = None
    for _ in range(_TOKEN_READ_ATTEMPTS):
        try:
            token = _read_token(token_path)
        except RuntimeError as exc:
            last_read_error = exc
            time.sleep(_TOKEN_READ_DELAY_SECONDS)
            continue
        if token is not None:
            try:
                token_path.chmod(0o600)
            except OSError:
                pass
            return token

        candidate = secrets.token_urlsafe(_TOKEN_BYTES)
        try:
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            time.sleep(_TOKEN_READ_DELAY_SECONDS)
            continue
        try:
            data = candidate.encode("utf-8")
            while data:
                written = os.write(fd, data)
                if written == 0:
                    raise OSError("Could not write WhatsApp bridge token")
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return candidate

    if last_read_error is not None:
        raise last_read_error
    raise RuntimeError(f"Timed out creating WhatsApp bridge token file: {token_path}")


def bridge_auth_proof(token: str, challenge: str) -> str:
    """Bind proof of bridge identity to a fresh client challenge."""
    return hmac.new(token.encode(), challenge.encode(), hashlib.sha256).hexdigest()


async def authenticate_bridge(ws: Any, token: str) -> None:
    """Verify the bridge before disclosing the bearer, then authenticate the client."""
    challenge = secrets.token_hex(32)
    await ws.send(json.dumps({"type": "auth_challenge", "challenge": challenge}))

    try:
        raw_proof = await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT_SECONDS)
        proof_message = json.loads(raw_proof)
    except (asyncio.TimeoutError, json.JSONDecodeError, TypeError) as exc:
        raise ConnectionError("WhatsApp bridge identity proof failed") from exc

    if not isinstance(proof_message, dict):
        raise ConnectionError("WhatsApp bridge identity proof failed")
    expected_proof = bridge_auth_proof(token, challenge)
    proof = proof_message.get("proof")
    if proof_message.get("type") != "auth_proof" or not isinstance(proof, str):
        raise ConnectionError("WhatsApp bridge identity proof failed")
    if not hmac.compare_digest(proof, expected_proof):
        raise ConnectionError("WhatsApp bridge identity proof failed")

    await ws.send(json.dumps({"type": "auth", "token": token}))
    try:
        raw_result = await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT_SECONDS)
        result = json.loads(raw_result)
    except (asyncio.TimeoutError, json.JSONDecodeError, TypeError) as exc:
        raise ConnectionError("WhatsApp bridge authentication failed") from exc
    if not isinstance(result, dict) or result.get("type") != "auth_ok":
        raise ConnectionError("WhatsApp bridge authentication failed")
