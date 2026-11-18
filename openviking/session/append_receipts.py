"""Durable append intents; all operations run under the authoritative session lock.

Completed receipts contain only a fingerprint, message IDs and the original result.
One pending intent stores the append bytes and intended metadata. A commit must
recover it before moving live messages into an archive.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, is_dataclass, replace
from urllib.parse import unquote
from uuid import uuid4

from openviking.pyagfs.exceptions import AGFSAlreadyExistsError
from openviking.session.archive_store import is_storage_not_found
from openviking_cli.exceptions import AlreadyExistsError, ConflictError, InvalidArgumentError

DIRECTORY = ".append_receipts"
VERSION = 1
MAX_RECEIPTS = 10_000
MAX_INTENT_BYTES = 16 * 1024 * 1024


class InvalidReceiptError(ConflictError):
    """Unreadable state may be repaired only from a valid pending intent."""


def receipt_root(uri, ctx=None):
    """Recognize only the managed Session journal, including request aliases."""
    from openviking.core.namespace import resolve_request_uri, uri_parts

    decoded = unquote(unquote(uri))
    if "/sessions/" not in decoded and not decoded.startswith("viking://session/"):
        return None
    canonical = resolve_request_uri(decoded, ctx) if ctx is not None else decoded
    parts = [unquote(unquote(part)) for part in uri_parts(canonical)]
    if len(parts) >= 5 and parts[0] == "user" and parts[2] == "sessions" and parts[4] == DIRECTORY:
        return "viking://" + "/".join(parts[:5])
    return None


def receipt_access_allowed(uri, ctx):
    root = receipt_root(uri, ctx)
    return root is None or getattr(ctx, "session_receipt_scope", None) == root


def fingerprint(value) -> str:
    def encode(obj):
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        raise TypeError(f"Unsupported append value: {type(obj).__name__}")

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=encode,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise InvalidArgumentError("idempotency_key must be nonblank and at most 128 characters")
    return key


@dataclass(frozen=True)
class AppendResult:
    result: dict
    replayed: bool


class AppendReceiptStore:
    def __init__(self, fs, ctx, session_uri, *, lease_ref=None):
        self.fs = fs
        self.lease_ref = lease_ref
        self.root = session_uri.rstrip("/") + "/" + DIRECTORY
        self.ctx = replace(ctx, session_receipt_scope=self.root)
        self.pending_uri = self.root + "/pending.json"
        self.state_uri = self.root + "/state.json"

    async def read(self, uri):
        try:
            content = await self.fs.read_file(uri, ctx=self.ctx)
        except Exception as exc:
            if is_storage_not_found(exc):
                return None
            raise
        try:
            record = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise InvalidReceiptError("Invalid append receipt; recovery is required") from exc
        if (
            not isinstance(record, dict)
            or type(record.get("version")) is not int
            or record["version"] != VERSION
        ):
            raise InvalidReceiptError("Invalid append receipt; recovery is required")
        if uri == self.pending_uri:
            for key in ("key_digest", "request_hash", "prefix_hash"):
                if not isinstance(record.get(key), str) or not re.fullmatch(
                    r"[0-9a-f]{64}", record[key]
                ):
                    raise ConflictError("Invalid append intent fingerprint")
            if type(record.get("prefix_bytes")) is not int or record["prefix_bytes"] < 0:
                raise ConflictError("Invalid append intent position")
            if (
                not isinstance(record.get("prefix"), str)
                or not isinstance(record.get("append"), str)
                or not isinstance(record.get("meta_after"), dict)
                or not isinstance(record.get("result"), dict)
            ):
                raise ConflictError("Invalid append intent")
        elif uri != self.state_uri:
            if (
                not isinstance(record.get("request_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["request_hash"])
                or not isinstance(record.get("result"), dict)
            ):
                raise InvalidReceiptError("Invalid completed append receipt")
        return record

    async def write(self, uri, record):
        encoded = json.dumps(record, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_INTENT_BYTES:
            from openviking_cli.exceptions import ResourceExhaustedError

            raise ResourceExhaustedError("Append intent exceeds the 16 MiB limit")
        write = getattr(self.fs, "write_file_atomic", self.fs.write_file)
        await write(uri, encoded, ctx=self.ctx, lease_ref=self.lease_ref)

    def key_digest(self, key):
        # The directory already isolates the canonical Session and account;
        # include the authenticated user as an additional receipt boundary.
        return fingerprint([str(self.ctx.account_id), self.ctx.user.user_id, validate_key(key)])

    def receipt_uri(self, digest):
        return self.root + "/" + digest + ".json"

    async def completed(self, digest, request_hash):
        record = await self.read(self.receipt_uri(digest))
        if record is None:
            return None
        if record.get("request_hash") != request_hash:
            raise ConflictError("idempotency_key was already used with different messages")
        return AppendResult(record["result"], True)

    async def begin(self, intent):
        try:
            await self.fs.mkdir(self.root, ctx=self.ctx, lease_ref=self.lease_ref)
        except (AlreadyExistsError, AGFSAlreadyExistsError, FileExistsError):
            pass
        await self.write(self.pending_uri, {"version": VERSION, **intent})

    async def ensure_state(self):
        state = await self.read(self.state_uri)
        if state is None:
            try:
                await self.fs.mkdir(self.root, ctx=self.ctx, lease_ref=self.lease_ref)
            except (AlreadyExistsError, AGFSAlreadyExistsError, FileExistsError):
                pass
            state = {"version": VERSION, "incarnation": uuid4().hex}
            await self.write(self.state_uri, state)
        if not isinstance(state.get("incarnation"), str) or not re.fullmatch(
            r"[0-9a-f]{32}", state["incarnation"]
        ):
            raise ConflictError("Invalid append receipt state")
        return state["incarnation"]

    async def verify_state(self, incarnation):
        state = await self.read(self.state_uri)
        if not state or state.get("incarnation") != incarnation:
            raise ConflictError("Append receipts are missing; a receipt-aware restore is required")

    async def finish(self, intent):
        await self.write(
            self.receipt_uri(intent["key_digest"]),
            {
                "version": VERSION,
                "request_hash": intent["request_hash"],
                "message_ids": intent["message_ids"],
                "result": intent["result"],
            },
        )
        await self.clear_pending()

    async def clear_pending(self):
        try:
            await self.fs.rm(
                self.pending_uri, ctx=self.ctx, lease_ref=self.lease_ref, no_vector=True
            )
        except Exception as exc:
            if not is_storage_not_found(exc):
                raise

    async def recover(self, publish):
        pending = await self.read(self.pending_uri)
        if pending is None:
            return
        try:
            done = await self.completed(pending["key_digest"], pending["request_hash"])
        except InvalidReceiptError:
            # A complete pending intent can repair a torn completed receipt.
            # A well-formed receipt for a different body still conflicts.
            done = None
        if done is not None:
            await self.clear_pending()
            return
        await publish(pending)
        await self.finish(pending)
