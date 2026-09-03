# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Email Accessor (IMAP).

Pilot implementation of the accessor sync standard (RFC discussion #3354):
auth declaration via ``auth_spec()``/``check()``, incremental sync via an
opaque ``sync_checkpoint`` cursor, full-sync ``doc_ids`` for orphan cleanup,
and per-document error isolation.

Source form: ``imap://user@host/INBOX`` or ``imaps://user@host:993/INBOX``.
Emails are converted to markdown files (one directory per mailbox) so the
existing markdown parser handles them — no dedicated eml parser.
"""

import asyncio
import email
import hashlib
import imaplib
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union
from urllib.parse import unquote, urlparse

from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils.logger import get_logger

from .base import AccessError, AccessResult, ConnectionStatus, DataAccessor, LocalResource, SourceType

logger = get_logger(__name__)

DEFAULT_IMAP_PORT = 993
DEFAULT_IMAP_PORT_PLAIN = 143
SYNC_CHECKPOINT_KEY = "sync_checkpoint"

# IMAP SINCE requires DD-Mon-YYYY with English month names; strftime("%b") is
# locale-dependent, so build the token explicitly.
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class _EmailParams:
    """Resolved connection parameters for one access run."""

    host: str
    port: int
    use_ssl: bool
    username: str
    password: str
    mailboxes: List[str]


def _imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{_IMAP_MONTHS[dt.month - 1]}-{dt.year}"


def _to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_checkpoint(cursor: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not cursor:
        return None
    raw = cursor.get(SYNC_CHECKPOINT_KEY)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(f"[EmailAccessor] Invalid {SYNC_CHECKPOINT_KEY} in cursor, ignoring: {raw!r}")
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_before_checkpoint(item_time: Optional[datetime], checkpoint: Optional[datetime]) -> bool:
    """Strict ``<`` incremental filter.

    cursor=T means "every email dated < T is already synced"; an email dated
    exactly T is re-pulled next round so a crash on the boundary never loses
    it (downstream dedup absorbs the repeat). Either side None means we can't
    prove the item was handled, so it is kept.
    """
    return checkpoint is not None and item_time is not None and item_time < checkpoint


def _mailbox_hash(mailbox: str) -> str:
    return hashlib.sha256(mailbox.encode("utf-8")).hexdigest()[:8]


def _decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _message_date(msg: Message) -> Optional[datetime]:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html."""
    plain: Optional[str] = None
    html: Optional[str] = None
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.is_multipart() or part.get_filename():
            continue
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
        if content_type == "text/plain" and plain is None:
            plain = text
        elif content_type == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain.strip()
    if html is not None:
        return _HTML_TAG_RE.sub("", html).strip()
    return ""


def _attachment_names(msg: Message) -> List[str]:
    names = []
    if msg.is_multipart():
        for part in msg.walk():
            filename = part.get_filename()
            if filename:
                names.append(_decode_header_value(filename))
    return names


def _doc_filename(doc_id: str) -> str:
    sanitized = _FILENAME_SANITIZE_RE.sub("_", doc_id).strip("_")
    return f"{sanitized[:100] or 'email'}.md"


def _build_markdown(msg: Message, mailbox: str, doc_id: str) -> str:
    subject = _decode_header_value(msg.get("Subject")) or "(no subject)"
    lines = [
        f"# {subject}",
        "",
        f"- **From**: {_decode_header_value(msg.get('From'))}",
        f"- **To**: {_decode_header_value(msg.get('To'))}",
        f"- **Date**: {_decode_header_value(msg.get('Date'))}",
        f"- **Mailbox**: {mailbox}",
        f"- **Message-ID**: {doc_id}",
    ]
    attachments = _attachment_names(msg)
    if attachments:
        lines.append(f"- **Attachments**: {', '.join(attachments)}")
    lines += ["", "---", "", _extract_body(msg), ""]
    return "\n".join(lines)


class EmailAccessor(DataAccessor):
    """Fetches emails from an IMAP mailbox and converts them to markdown."""

    def __init__(self, imap_factory: Optional[Callable[[str, int, bool, float], Any]] = None):
        """
        Args:
            imap_factory: Optional factory ``(host, port, use_ssl, timeout) ->
                IMAP4-like connection``, injectable for tests. Defaults to
                stdlib ``imaplib``.
        """
        self._imap_factory = imap_factory or self._default_imap_factory

    @property
    def priority(self) -> int:
        return 100  # Specific service, same tier as FeishuAccessor

    def can_handle(self, source: Union[str, Path], **kwargs) -> bool:
        return str(source).lower().startswith(("imap://", "imaps://"))

    def auth_spec(self) -> Optional[Dict[str, Any]]:
        return {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "IMAP server hostname"},
                "port": {"type": "integer", "default": DEFAULT_IMAP_PORT, "exclusiveMinimum": 0},
                "use_ssl": {"type": "boolean", "default": True},
                "username": {"type": "string", "description": "IMAP login, usually the email address"},
                "password": {"type": "string", "description": "Password or app-specific password"},
            },
            "required": ["host", "username", "password"],
        }

    def check(self, auth: Dict[str, Any]) -> ConnectionStatus:
        try:
            conn = self._connect(self._params_from_auth(auth))
            try:
                conn.logout()
            except Exception:
                pass
            return ConnectionStatus(success=True)
        except Exception as e:
            return ConnectionStatus(success=False, message=str(e))

    async def access(
        self,
        source: Union[str, Path],
        cursor: Optional[Dict[str, Any]] = None,
        progress: Optional[Callable[..., None]] = None,
        auth: Optional[Dict[str, Any]] = None,
        mailboxes: Optional[List[str]] = None,
        since_days: Optional[int] = None,
        timeout: float = 30.0,
        **kwargs,
    ) -> AccessResult:
        """
        Sync emails to a local markdown directory tree.

        Args:
            source: ``imap[s]://user@host[:port]/mailbox`` URL. The mailbox
                path segment is optional (defaults to INBOX).
            cursor: Cursor returned by the previous run, replayed as-is by the
                framework. None triggers a full sync.
            progress: Optional ``progress(done=..., total=...)`` callback.
            auth: Credentials matching ``auth_spec()``. URL parts win for
                host/port/username; the password always comes from ``auth``.
            mailboxes: Explicit mailbox list; overrides the URL path segment.
            since_days: Cap history depth on first sync. Implies a partial
                view, so ``doc_ids`` is not returned when set.
            timeout: IMAP socket timeout in seconds.
        """
        params = self._resolve_params(str(source), auth or {}, mailboxes)
        return await asyncio.to_thread(
            self._sync, params, str(source), cursor, progress, since_days, timeout
        )

    # ── parameter resolution ─────────────────────────────────────────

    def _resolve_params(
        self,
        source: str,
        auth: Dict[str, Any],
        mailboxes: Optional[List[str]],
    ) -> _EmailParams:
        parsed = urlparse(source)
        use_ssl = auth.get("use_ssl")
        if use_ssl is None:
            use_ssl = parsed.scheme.lower() != "imap"
        host = parsed.hostname or auth.get("host") or ""
        if not host:
            raise OpenVikingError(
                "Email source needs a host: imap[s]://user@host/mailbox",
                code="INVALID_ARGUMENT",
            )
        default_port = DEFAULT_IMAP_PORT if use_ssl else DEFAULT_IMAP_PORT_PLAIN
        username = unquote(parsed.username) if parsed.username else auth.get("username") or ""
        password = auth.get("password") or ""
        if not username or not password:
            raise OpenVikingError(
                "Email access requires username and password credentials",
                code="UNAUTHENTICATED",
            )
        url_mailbox = unquote(parsed.path).strip("/")
        resolved_mailboxes = list(mailboxes) if mailboxes else ([url_mailbox] if url_mailbox else ["INBOX"])
        return _EmailParams(
            host=host,
            port=parsed.port or int(auth.get("port") or default_port),
            use_ssl=bool(use_ssl),
            username=username,
            password=password,
            mailboxes=resolved_mailboxes,
        )

    def _params_from_auth(self, auth: Dict[str, Any]) -> _EmailParams:
        use_ssl = bool(auth.get("use_ssl", True))
        return _EmailParams(
            host=auth.get("host") or "",
            port=int(auth.get("port") or (DEFAULT_IMAP_PORT if use_ssl else DEFAULT_IMAP_PORT_PLAIN)),
            use_ssl=use_ssl,
            username=auth.get("username") or "",
            password=auth.get("password") or "",
            mailboxes=["INBOX"],
        )

    # ── IMAP plumbing ────────────────────────────────────────────────

    @staticmethod
    def _default_imap_factory(host: str, port: int, use_ssl: bool, timeout: float) -> Any:
        if use_ssl:
            return imaplib.IMAP4_SSL(host, port, timeout=timeout)
        return imaplib.IMAP4(host, port, timeout=timeout)

    def _connect(self, params: _EmailParams, timeout: float = 30.0) -> Any:
        conn = self._imap_factory(params.host, params.port, params.use_ssl, timeout)
        typ, _ = conn.login(params.username, params.password)
        if typ != "OK":
            raise OpenVikingError(
                f"IMAP login failed for {params.username}@{params.host}",
                code="UNAUTHENTICATED",
            )
        return conn

    # ── sync core (runs in a worker thread) ──────────────────────────

    def _sync(
        self,
        params: _EmailParams,
        source: str,
        cursor: Optional[Dict[str, Any]],
        progress: Optional[Callable[..., None]],
        since_days: Optional[int],
        timeout: float,
    ) -> AccessResult:
        checkpoint = _parse_checkpoint(cursor)
        watermark = datetime.now(timezone.utc)

        # since_days caps history depth; combined with the checkpoint the
        # later bound wins so a stale cursor never re-pulls capped history.
        effective_since = checkpoint
        if since_days is not None:
            floor = watermark - timedelta(days=since_days)
            if effective_since is None or floor > effective_since:
                effective_since = floor

        temp_dir = Path(tempfile.mkdtemp(prefix="ov_email_"))
        seen_ids: Set[str] = set()
        errors: List[AccessError] = []
        failed: List[str] = []
        done = 0

        conn = self._connect(params, timeout=timeout)
        try:
            for mailbox in params.mailboxes:
                try:
                    done = self._sync_mailbox(
                        conn=conn,
                        mailbox=mailbox,
                        target_dir=temp_dir / _FILENAME_SANITIZE_RE.sub("_", mailbox),
                        since=effective_since,
                        checkpoint=checkpoint,
                        seen_ids=seen_ids,
                        errors=errors,
                        progress=progress,
                        done=done,
                    )
                except Exception as e:
                    logger.exception(f"[EmailAccessor] Mailbox failed (continuing with the rest): {mailbox}")
                    failed.append(mailbox)
                    errors.append(AccessError(doc_id=None, kind="transient", message=f"{mailbox}: {e}"))
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        if failed:
            # Never advance the cursor on a partial run: re-pulling beats losing mail.
            raise OpenVikingError(
                f"Email sync failed for {len(failed)} mailbox(es): {', '.join(failed)}",
                code="UNAVAILABLE",
                details={"failed_mailboxes": failed, "errors": [e.message for e in errors]},
            )

        # doc_ids drives orphan deletion, so it is only safe when this run saw
        # the complete mailbox view: no cursor and no since_days cap.
        full_view = checkpoint is None and since_days is None
        logger.info(
            f"[EmailAccessor] Synced {done} email(s) from {len(params.mailboxes)} mailbox(es), "
            f"watermark={_to_utc_iso(watermark)}, full_view={full_view}"
        )
        return AccessResult(
            resource=LocalResource(
                path=temp_dir,
                source_type=SourceType.EMAIL,
                original_source=source,
                meta={
                    "host": params.host,
                    "username": params.username,
                    "mailboxes": params.mailboxes,
                    "email_count": done,
                },
            ),
            cursor={SYNC_CHECKPOINT_KEY: _to_utc_iso(watermark)},
            doc_ids=seen_ids if full_view else None,
            errors=errors,
        )

    def _sync_mailbox(
        self,
        *,
        conn: Any,
        mailbox: str,
        target_dir: Path,
        since: Optional[datetime],
        checkpoint: Optional[datetime],
        seen_ids: Set[str],
        errors: List[AccessError],
        progress: Optional[Callable[..., None]],
        done: int,
    ) -> int:
        typ, _ = conn.select(f'"{mailbox}"', readonly=True)
        if typ != "OK":
            raise OpenVikingError(f"Cannot select mailbox: {mailbox}", code="NOT_FOUND")

        criteria = f"SINCE {_imap_date(since)}" if since else "ALL"
        typ, data = conn.uid("SEARCH", None, criteria)
        if typ != "OK":
            raise OpenVikingError(f"IMAP SEARCH failed in mailbox: {mailbox}", code="UNAVAILABLE")
        uids = data[0].split() if data and data[0] else []

        target_dir.mkdir(parents=True, exist_ok=True)
        for uid in uids:
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            try:
                typ, fetched = conn.uid("FETCH", uid_str, "(RFC822)")
                if typ != "OK" or not fetched or fetched[0] is None:
                    raise OpenVikingError(f"IMAP FETCH failed for uid {uid_str}", code="UNAVAILABLE")
                raw = fetched[0][1] if isinstance(fetched[0], tuple) else fetched[0]
                msg = email.message_from_bytes(raw)

                # SINCE is day-granular; the strict `<` client-side filter is
                # what actually enforces the checkpoint semantics.
                if _is_before_checkpoint(_message_date(msg), checkpoint):
                    continue

                doc_id = _decode_header_value(msg.get("Message-ID")) or f"{_mailbox_hash(mailbox)}_{uid_str}"
                (target_dir / _doc_filename(doc_id)).write_text(
                    _build_markdown(msg, mailbox, doc_id), encoding="utf-8"
                )
                seen_ids.add(doc_id)
                done += 1
                if progress:
                    progress(done=done, total=None)
            except Exception as e:
                logger.warning(f"[EmailAccessor] Skipping email uid={uid_str} in {mailbox}: {e}")
                errors.append(
                    AccessError(
                        doc_id=f"{_mailbox_hash(mailbox)}_{uid_str}",
                        kind="permanent",
                        message=str(e),
                    )
                )
        return done
