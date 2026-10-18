# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLAlchemy-backed MySQL implementation of the account and API-key store."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import ssl as ssl_module
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from openviking.core.identifiers import MAX_IDENTIFIER_LENGTH
from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.models import (
    AccountSummary,
    DeletionRecord,
    GroupSummary,
    UsersPage,
    UserSummary,
)
from openviking.server.account_stores.mysql_schema import (
    Account,
    Credential,
    Group,
    GroupMember,
    User,
)
from openviking.server.identity import Role
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.session.user_id import (
    validate_account_id,
    validate_identifier_part,
    validate_user_id,
)
from openviking_cli.utils.config.config_utils import normalize_service_name

_MYSQL_IDENTIFIER_MAX_LENGTH = MAX_IDENTIFIER_LENGTH
_RETAINED_REVOKED_CREDENTIALS = 10


def _validate_length(value: str, name: str) -> None:
    if len(value) > _MYSQL_IDENTIFIER_MAX_LENGTH:
        raise InvalidArgumentError(
            f"{name} must not exceed {_MYSQL_IDENTIFIER_MAX_LENGTH} characters."
        )


def _validate(value: str, validator, name: str) -> None:
    if error := validator(value):
        raise InvalidArgumentError(error)
    _validate_length(value, name)


def _validate_role(role: str) -> Role:
    if role == Role.ROOT:
        raise PermissionDeniedError("Account users cannot be assigned ROOT")
    if role not in (Role.ADMIN, Role.USER):
        raise InvalidArgumentError("Account user role must be user or admin.")
    return Role(role)


def _like_pattern(pattern: str) -> str:
    escaped = []
    for character in pattern:
        if character == "*":
            escaped.append("%")
        elif character == "?":
            escaped.append("_")
        elif character in {"%", "_", "="}:
            escaped.append("=" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def _substring_pattern(value: str) -> str:
    escaped = "".join(
        "=" + character if character in {"%", "_", "="} else character for character in value
    )
    return f"%{escaped}%"


def _credential_digest(api_key: str) -> str:
    """Return the lookup digest for a high-entropy API key."""
    return hashlib.sha256(api_key.encode()).hexdigest()


_DIGEST_CREDENTIAL_PREFIX = "sha256:v1:"
_ENCRYPTED_CREDENTIAL_PREFIX = "aesgcm:v1:"


def _deletion(record) -> DeletionRecord | None:
    if record.deletion_task_id is None:
        return None
    return DeletionRecord(
        task_id=record.deletion_task_id,
        owner_account_id=record.deletion_owner_account_id,
        owner_user_id=record.deletion_owner_user_id,
    )


class MySQLAccountStore(AccountStore):
    """A manually provisioned relational account store scoped by service name."""

    def __init__(
        self,
        *,
        service_name: str,
        params: dict[str, object] | None = None,
    ) -> None:
        values = dict(params or {})
        try:
            self._service_name = normalize_service_name(service_name)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from None
        _validate_length(self._service_name, "service_name")

        credential_storage = values.pop("credential_storage", "hash")
        encryption_key = values.pop("credential_encryption_key", None)
        self._credential_storage = self._validate_credential_storage(credential_storage)
        self._credential_cipher = self._new_credential_cipher(encryption_key)
        if self._credential_storage == "encrypted" and self._credential_cipher is None:
            raise InvalidArgumentError(
                "encrypted MySQL credential storage requires credential_encryption_key"
            )
        self._url, engine_options = self._connection_options(values)
        self._engine_options = engine_options
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @staticmethod
    def _validate_credential_storage(mode: object) -> str:
        if mode not in {"hash", "encrypted"}:
            raise InvalidArgumentError("credential_storage must be 'hash' or 'encrypted'")
        return str(mode)

    @staticmethod
    def _new_credential_cipher(encryption_key: object) -> AESGCM | None:
        if encryption_key is None:
            return None
        if not isinstance(encryption_key, str):
            raise InvalidArgumentError("credential_encryption_key must be base64-encoded")
        try:
            key = base64.b64decode(encryption_key.encode(), altchars=b"-_", validate=True)
        except (binascii.Error, ValueError, UnicodeError) as exc:
            raise InvalidArgumentError("credential_encryption_key must be base64-encoded") from exc
        if len(key) != 32:
            raise InvalidArgumentError("credential_encryption_key must decode to exactly 32 bytes")
        return AESGCM(key)

    @staticmethod
    def _credential_associated_data(account_id: str, user_id: str) -> bytes:
        return f"{account_id}\0{user_id}".encode()

    def _encode_credential(self, account_id: str, user_id: str, api_key: str) -> str:
        if self._credential_storage == "hash":
            return _DIGEST_CREDENTIAL_PREFIX + _credential_digest(api_key)
        assert self._credential_cipher is not None
        nonce = secrets.token_bytes(12)
        ciphertext = self._credential_cipher.encrypt(
            nonce,
            api_key.encode(),
            self._credential_associated_data(account_id, user_id),
        )
        return _ENCRYPTED_CREDENTIAL_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def _decrypt_credential(self, account_id: str, user_id: str, material: str) -> str | None:
        if self._credential_cipher is None or not material.startswith(_ENCRYPTED_CREDENTIAL_PREFIX):
            return None
        try:
            payload = base64.urlsafe_b64decode(
                material[len(_ENCRYPTED_CREDENTIAL_PREFIX) :].encode()
            )
            return self._credential_cipher.decrypt(
                payload[:12],
                payload[12:],
                self._credential_associated_data(account_id, user_id),
            ).decode()
        except (InvalidTag, ValueError, UnicodeError):
            return None

    def _matches_credential(
        self, account_id: str, user_id: str, material: str, api_key: str
    ) -> bool:
        if material.startswith(_DIGEST_CREDENTIAL_PREFIX):
            expected = _DIGEST_CREDENTIAL_PREFIX + _credential_digest(api_key)
            return hmac.compare_digest(material, expected)
        plaintext = self._decrypt_credential(account_id, user_id, material)
        return plaintext is not None and hmac.compare_digest(plaintext, api_key)

    def _new_credential(
        self,
        account_id: str,
        user_id: str,
        user_pk: int,
        api_key: str,
        *,
        sequence: int,
    ):
        return Credential(
            credential_id=uuid4().hex,
            service_name=self._service_name,
            user_pk=user_pk,
            active_user_pk=user_pk,
            material=self._encode_credential(account_id, user_id, api_key),
            lookup_digest=_credential_digest(api_key),
            prefix=api_key[:8],
            sequence=sequence,
        )

    @staticmethod
    def _connection_options(
        values: dict[str, object],
    ) -> tuple[URL, dict[str, object]]:
        allowed = {
            "user",
            "password",
            "database",
            "host",
            "port",
            "minsize",
            "maxsize",
            "connect_timeout",
            "ssl",
            "unix_socket",
        }
        unknown = values.keys() - allowed
        if unknown:
            raise InvalidArgumentError(
                f"Unknown MySQL account store parameters: {', '.join(sorted(unknown))}"
            )
        missing = [name for name in ("user", "password", "database") if name not in values]
        if missing:
            raise InvalidArgumentError(f"MySQL account store requires: {', '.join(missing)}")

        connect_args: dict[str, object] = {
            "connect_timeout": values.get("connect_timeout", 10),
        }
        if socket := values.get("unix_socket"):
            connect_args["unix_socket"] = socket
        ssl = values.get("ssl")
        if isinstance(ssl, dict):
            unknown_ssl = ssl.keys() - {"ca", "cert", "key", "check_hostname"}
            if unknown_ssl:
                raise InvalidArgumentError(f"Unknown MySQL TLS options: {sorted(unknown_ssl)}")
            context = ssl_module.create_default_context(cafile=ssl.get("ca"))
            context.check_hostname = bool(ssl.get("check_hostname", True))
            if ssl.get("cert"):
                context.load_cert_chain(ssl["cert"], ssl.get("key"))
            connect_args["ssl"] = context
        elif ssl is True:
            connect_args["ssl"] = ssl_module.create_default_context()
        elif ssl not in (None, False):
            raise InvalidArgumentError("ssl must be a boolean or TLS options mapping")

        url = URL.create(
            "mysql+aiomysql",
            username=str(values["user"]),
            password=str(values["password"]),
            host=str(values.get("host", "127.0.0.1")),
            port=int(values.get("port", 3306)),
            database=str(values["database"]),
            query={"charset": "utf8mb4"},
        )
        return url, {
            "pool_size": int(values.get("maxsize", 10)),
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "connect_args": connect_args,
            "isolation_level": "READ COMMITTED",
        }

    async def load(self) -> None:
        if self._engine is not None:
            return
        try:
            engine = create_async_engine(self._url, **self._engine_options)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                for model in (Account, User, Group, GroupMember, Credential):
                    await session.execute(select(*model.__table__.columns).limit(1))
        except ImportError:
            raise RuntimeError("MySQL account storage requires openviking[mysql]") from None
        except DBAPIError as exc:
            await engine.dispose()
            original_args = getattr(exc.orig, "args", ())
            error_code = original_args[0] if original_args else None
            message = str(exc).lower()
            if error_code in {1054, 1146} or any(
                marker in message
                for marker in ("unknown column", "doesn't exist", "does not exist")
            ):
                raise RuntimeError(
                    "MySQL account schema is missing or incompatible; create "
                    "all configured relational tables before starting"
                ) from exc
            raise
        except BaseException:
            if "engine" in locals():
                await engine.dispose()
            raise
        self._engine = engine
        self._sessions = sessions

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._sessions = None

    @property
    def _session_factory(self) -> async_sessionmaker[AsyncSession]:
        assert self._sessions is not None
        return self._sessions

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            async with session.begin():
                yield session

    async def _lock_account(self, session: AsyncSession, account_id: str):
        account = await session.scalar(
            select(Account)
            .where(
                Account.service_name == self._service_name,
                Account.account_id == account_id,
            )
            .with_for_update()
        )
        if account is None:
            raise NotFoundError(account_id, "account")
        return account

    async def _lock_user(self, session: AsyncSession, account, user_id: str):
        user = await session.scalar(
            select(User)
            .where(
                User.service_name == self._service_name,
                User.account_pk == account.account_pk,
                User.user_id == user_id,
            )
            .with_for_update()
        )
        if user is None:
            raise NotFoundError(user_id, "user")
        return user

    @staticmethod
    def _require_active(record) -> None:
        deletion = _deletion(record)
        if deletion:
            raise FailedPreconditionError(
                "Identity deletion is in progress", details={"task_id": deletion["task_id"]}
            )

    async def _protect_last_admin(self, session: AsyncSession, account, user) -> None:
        if user.role != Role.ADMIN or user.deletion_task_id:
            return
        count = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.service_name == self._service_name,
                User.account_pk == account.account_pk,
                User.role == Role.ADMIN,
                User.deletion_task_id.is_(None),
            )
        )
        if count <= 1:
            raise FailedPreconditionError("Cannot remove the last active account admin")

    @staticmethod
    def _account_summary(account, user_count: int) -> AccountSummary:
        return {
            "account_id": account.account_id,
            "created_at": account.created_at.isoformat(),
            "user_count": user_count,
            "status": "deleting" if account.deletion_task_id else "active",
            **({"task_id": account.deletion_task_id} if account.deletion_task_id else {}),
        }

    async def get_account(self, account_id: str) -> AccountSummary | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Account, func.count(User.user_pk))
                    .outerjoin(
                        User,
                        and_(
                            User.service_name == Account.service_name,
                            User.account_pk == Account.account_pk,
                        ),
                    )
                    .where(
                        Account.service_name == self._service_name,
                        Account.account_id == account_id,
                    )
                    .group_by(Account.account_pk)
                )
            ).one_or_none()
        return self._account_summary(*row) if row else None

    async def get_user(self, account_id: str, user_id: str) -> UserSummary | None:
        async with self._session_factory() as session:
            user = await session.scalar(
                select(User)
                .join(
                    Account,
                    and_(
                        Account.service_name == User.service_name,
                        Account.account_pk == User.account_pk,
                    ),
                )
                .where(
                    User.service_name == self._service_name,
                    Account.account_id == account_id,
                    User.user_id == user_id,
                )
            )
        return UserSummary(user_id=user.user_id, role=user.role) if user else None

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
    ) -> None:
        _validate(account_id, validate_account_id, "account_id")
        _validate(admin_user_id, validate_user_id, "admin_user_id")
        try:
            async with self._transaction() as session:
                account = Account(service_name=self._service_name, account_id=account_id)
                session.add(account)
                await session.flush()
                session.add(
                    User(
                        service_name=self._service_name,
                        account_pk=account.account_pk,
                        user_id=admin_user_id,
                        role=Role.ADMIN,
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            raise AlreadyExistsError(account_id, "account") from exc

    async def create_account_with_api_key(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str,
    ) -> None:
        _validate(account_id, validate_account_id, "account_id")
        _validate(admin_user_id, validate_user_id, "admin_user_id")
        try:
            async with self._transaction() as session:
                account = Account(service_name=self._service_name, account_id=account_id)
                session.add(account)
                await session.flush()
                user = User(
                    service_name=self._service_name,
                    account_pk=account.account_pk,
                    user_id=admin_user_id,
                    role=Role.ADMIN,
                )
                session.add(user)
                await session.flush()
                session.add(
                    self._new_credential(
                        account_id,
                        admin_user_id,
                        user.user_pk,
                        api_key,
                        sequence=1,
                    )
                )
        except IntegrityError as exc:
            raise AlreadyExistsError(account_id, "account") from exc

    async def create_user(self, account_id: str, user_id: str, role: str) -> None:
        _validate(user_id, validate_user_id, "user_id")
        role = _validate_role(role)
        try:
            async with self._transaction() as session:
                account = await self._lock_account(session, account_id)
                self._require_active(account)
                session.add(
                    User(
                        service_name=self._service_name,
                        account_pk=account.account_pk,
                        user_id=user_id,
                        role=role,
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            raise AlreadyExistsError(user_id, "user") from exc

    async def create_user_with_api_key(
        self,
        account_id: str,
        user_id: str,
        role: str,
        api_key: str,
    ) -> None:
        _validate(user_id, validate_user_id, "user_id")
        role = _validate_role(role)
        try:
            async with self._transaction() as session:
                account = await self._lock_account(session, account_id)
                self._require_active(account)
                user = User(
                    service_name=self._service_name,
                    account_pk=account.account_pk,
                    user_id=user_id,
                    role=role,
                )
                session.add(user)
                await session.flush()
                session.add(
                    self._new_credential(
                        account_id,
                        user_id,
                        user.user_pk,
                        api_key,
                        sequence=1,
                    )
                )
        except IntegrityError as exc:
            raise AlreadyExistsError(user_id, "user") from exc

    async def delete_account(self, account_id: str) -> None:
        async with self._transaction() as session:
            await session.delete(await self._lock_account(session, account_id))

    async def list_accounts(
        self,
        name_filter: str | None = None,
        limit: int | None = None,
        page: int = 1,
        query_filter: str | None = None,
    ) -> list[AccountSummary]:
        predicates = [Account.service_name == self._service_name]
        if name_filter:
            predicates.append(Account.account_id.like(_like_pattern(name_filter), escape="="))
        if query := (query_filter or "").strip():
            predicates.append(
                func.lower(Account.account_id).like(
                    _substring_pattern(query.casefold()),
                    escape="=",
                )
            )
        statement = (
            select(Account, func.count(User.user_pk))
            .outerjoin(
                User,
                and_(
                    User.service_name == Account.service_name,
                    User.account_pk == Account.account_pk,
                ),
            )
            .where(*predicates)
            .group_by(Account.account_pk)
            .order_by(Account.created_at, Account.account_id)
        )
        if limit is not None:
            statement = statement.limit(limit).offset((max(1, page) - 1) * limit)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [self._account_summary(account, count) for account, count in rows]

    async def list_users_page(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        page: int = 1,
        query_filter: str | None = None,
        fresh: bool = False,
    ) -> UsersPage:
        del fresh
        async with self._session_factory() as session:
            # One HTTP response must derive its rows and summary from one MVCC snapshot.
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            account = await self._account_for_read(session, account_id)
            base = [
                User.service_name == self._service_name,
                User.account_pk == account.account_pk,
                User.deletion_task_id.is_(None),
            ]
            account_total, manager_count = (
                await session.execute(
                    select(
                        func.count(),
                        func.coalesce(func.sum(case((User.role == Role.ADMIN, 1), else_=0)), 0),
                    ).where(*base)
                )
            ).one()
            key_count = await session.scalar(
                select(func.count())
                .select_from(Credential)
                .join(
                    User,
                    and_(
                        User.service_name == Credential.service_name,
                        User.user_pk == Credential.user_pk,
                    ),
                )
                .where(*base, Credential.revoked.is_(False))
            )
            filters = list(base)
            if name_filter:
                filters.append(User.user_id.like(_like_pattern(name_filter), escape="="))
            if role_filter:
                filters.append(User.role == role_filter)
            if query := (query_filter or "").strip():
                filters.append(
                    func.lower(User.user_id).like(
                        _substring_pattern(query.casefold()),
                        escape="=",
                    )
                )
            total = await session.scalar(select(func.count()).select_from(User).where(*filters))
            statement = select(User).where(*filters).order_by(User.created_at, User.user_pk)
            if limit is not None:
                statement = statement.limit(limit).offset((max(1, page) - 1) * limit)
            users = list((await session.scalars(statement)).all())
            credentials = {}
            if users:
                credentials = {
                    credential.user_pk: credential
                    for credential in (
                        await session.scalars(
                            select(Credential).where(
                                Credential.service_name == self._service_name,
                                Credential.user_pk.in_([user.user_pk for user in users]),
                                Credential.revoked.is_(False),
                            )
                        )
                    )
                }
        result: list[UserSummary] = []
        for user in users:
            row: UserSummary = {"user_id": user.user_id, "role": user.role}
            if credential := credentials.get(user.user_pk):
                api_key = self._decrypt_credential(account_id, user.user_id, credential.material)
                if api_key is None:
                    row["key_prefix"] = credential.prefix
                else:
                    row["api_key"] = api_key
            result.append(row)
        return {
            "users": result,
            "total": total,
            "account_total": account_total,
            "manager_count": manager_count,
            "key_count": key_count,
        }

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        role = _validate_role(role)
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            self._require_active(account)
            user = await self._lock_user(session, account, user_id)
            self._require_active(user)
            if role != Role.ADMIN:
                await self._protect_last_admin(session, account, user)
            user.role = role

    async def ensure_trusted_identities(self, identities: dict[str, set[str]]) -> dict[str, int]:
        for account_id, user_ids in identities.items():
            _validate(account_id, validate_account_id, "account_id")
            for user_id in user_ids:
                _validate(user_id, validate_user_id, "user_id")
        created_accounts = 0
        created_users = 0
        for account_id, user_ids in identities.items():
            if not user_ids:
                continue
            async with self._transaction() as session:
                account = await session.scalar(
                    select(Account)
                    .where(
                        Account.service_name == self._service_name,
                        Account.account_id == account_id,
                    )
                    .with_for_update()
                )
                if account is None:
                    try:
                        async with session.begin_nested():
                            account = Account(
                                service_name=self._service_name,
                                account_id=account_id,
                            )
                            session.add(account)
                            await session.flush()
                        created_accounts += 1
                    except IntegrityError:
                        account = await self._lock_account(session, account_id)
                if account.deletion_task_id:
                    continue
                existing = set(
                    (
                        await session.scalars(
                            select(User.user_id).where(
                                User.service_name == self._service_name,
                                User.account_pk == account.account_pk,
                                User.user_id.in_(user_ids),
                            )
                        )
                    ).all()
                )
                for user_id in sorted(user_ids - existing):
                    session.add(
                        User(
                            service_name=self._service_name,
                            account_pk=account.account_pk,
                            user_id=user_id,
                            role=Role.USER,
                        )
                    )
                    created_users += 1
        return {"created_accounts": created_accounts, "created_users": created_users}

    async def begin_deletion(
        self,
        account_id: str,
        user_id: str | None,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> tuple[DeletionRecord, bool]:
        _validate_length(owner_account_id, "owner_account_id")
        _validate_length(owner_user_id, "owner_user_id")
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            record = account
            if user_id is not None:
                self._require_active(account)
                record = await self._lock_user(session, account, user_id)
                await self._protect_last_admin(session, account, record)
            if current := _deletion(record):
                return current, False
            record.deletion_task_id = task_id
            record.deletion_owner_account_id = owner_account_id
            record.deletion_owner_user_id = owner_user_id
            return DeletionRecord(
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
            ), True

    async def replace_deletion_task(
        self,
        account_id: str,
        user_id: str | None,
        *,
        expected_task_id: str,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> DeletionRecord:
        _validate_length(owner_account_id, "owner_account_id")
        _validate_length(owner_user_id, "owner_user_id")
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            record = account
            if user_id is not None:
                self._require_active(account)
                record = await self._lock_user(session, account, user_id)
            current = _deletion(record)
            if current is None or current["task_id"] != expected_task_id:
                return current or DeletionRecord()
            record.deletion_task_id = task_id
            record.deletion_owner_account_id = owner_account_id
            record.deletion_owner_user_id = owner_user_id
            return DeletionRecord(
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
            )

    async def finish_deletion(self, account_id: str, user_id: str | None, task_id: str) -> bool:
        async with self._transaction() as session:
            try:
                account = await self._lock_account(session, account_id)
            except NotFoundError:
                return False
            if user_id is None:
                if account.deletion_task_id != task_id:
                    return False
                await session.delete(account)
                return True
            if account.deletion_task_id:
                return False
            try:
                user = await self._lock_user(session, account, user_id)
            except NotFoundError:
                return False
            if user.deletion_task_id != task_id:
                return False
            await session.delete(user)
            return True

    async def get_deletion(
        self, account_id: str, user_id: str | None = None
    ) -> DeletionRecord | None:
        if user_id is None:
            async with self._session_factory() as session:
                account = await session.scalar(
                    select(Account).where(
                        Account.service_name == self._service_name,
                        Account.account_id == account_id,
                    )
                )
            return _deletion(account) if account else None

        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Account, User)
                    .outerjoin(
                        User,
                        and_(
                            User.service_name == Account.service_name,
                            User.account_pk == Account.account_pk,
                            User.user_id == user_id,
                        ),
                    )
                    .where(
                        Account.service_name == self._service_name,
                        Account.account_id == account_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        account, user = row
        return _deletion(account) or (_deletion(user) if user else None)

    async def iter_deletions(self) -> list[tuple[str, str | None, DeletionRecord]]:
        async with self._session_factory() as session:
            accounts = (
                await session.scalars(
                    select(Account).where(
                        Account.service_name == self._service_name,
                        Account.deletion_task_id.is_not(None),
                    )
                )
            ).all()
            users = (
                await session.execute(
                    select(Account.account_id, User)
                    .join(
                        User,
                        and_(
                            User.service_name == Account.service_name,
                            User.account_pk == Account.account_pk,
                        ),
                    )
                    .where(
                        Account.service_name == self._service_name,
                        Account.deletion_task_id.is_(None),
                        User.deletion_task_id.is_not(None),
                    )
                )
            ).all()
        return [(account.account_id, None, _deletion(account)) for account in accounts] + [
            (account_id, user.user_id, _deletion(user)) for account_id, user in users
        ]

    async def create_group(self, account_id: str, group_id: str) -> GroupSummary:
        _validate(
            group_id,
            lambda value: validate_identifier_part(value, "group_id"),
            "group_id",
        )
        try:
            async with self._transaction() as session:
                account = await self._lock_account(session, account_id)
                self._require_active(account)
                session.add(
                    Group(
                        service_name=self._service_name,
                        account_pk=account.account_pk,
                        group_id=group_id,
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            raise AlreadyExistsError(group_id, "group") from exc
        return GroupSummary(group_id=group_id, member_count=0)

    async def _account_for_read(self, session: AsyncSession, account_id: str):
        account = await session.scalar(
            select(Account).where(
                Account.service_name == self._service_name,
                Account.account_id == account_id,
            )
        )
        if account is None:
            raise NotFoundError(account_id, "account")
        return account

    async def get_groups(self, account_id: str) -> list[GroupSummary]:
        async with self._session_factory() as session:
            account = await self._account_for_read(session, account_id)
            rows = (
                await session.execute(
                    select(Group.group_id, func.count(GroupMember.user_pk))
                    .outerjoin(
                        GroupMember,
                        and_(
                            GroupMember.service_name == Group.service_name,
                            GroupMember.account_pk == Group.account_pk,
                            GroupMember.group_pk == Group.group_pk,
                        ),
                    )
                    .where(
                        Group.service_name == self._service_name,
                        Group.account_pk == account.account_pk,
                    )
                    .group_by(Group.group_pk)
                    .order_by(Group.group_id)
                )
            ).all()
        return [GroupSummary(group_id=group_id, member_count=count) for group_id, count in rows]

    async def get_group_members(self, account_id: str, group_id: str) -> list[str]:
        async with self._session_factory() as session:
            account = await self._account_for_read(session, account_id)
            group = await session.scalar(
                select(Group).where(
                    Group.service_name == self._service_name,
                    Group.account_pk == account.account_pk,
                    Group.group_id == group_id,
                )
            )
            if group is None:
                raise NotFoundError(group_id, "group")
            return list(
                (
                    await session.scalars(
                        select(User.user_id)
                        .join(
                            GroupMember,
                            and_(
                                GroupMember.service_name == User.service_name,
                                GroupMember.account_pk == User.account_pk,
                                GroupMember.user_pk == User.user_pk,
                            ),
                        )
                        .where(
                            GroupMember.service_name == self._service_name,
                            GroupMember.account_pk == account.account_pk,
                            GroupMember.group_pk == group.group_pk,
                        )
                        .order_by(User.user_id)
                    )
                ).all()
            )

    async def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        async with self._session_factory() as session:
            group_ids = (
                await session.scalars(
                    select(Group.group_id)
                    .select_from(Account)
                    .join(
                        User,
                        and_(
                            User.service_name == Account.service_name,
                            User.account_pk == Account.account_pk,
                        ),
                    )
                    .outerjoin(
                        GroupMember,
                        and_(
                            GroupMember.service_name == User.service_name,
                            GroupMember.account_pk == User.account_pk,
                            GroupMember.user_pk == User.user_pk,
                        ),
                    )
                    .outerjoin(
                        Group,
                        and_(
                            Group.service_name == GroupMember.service_name,
                            Group.account_pk == GroupMember.account_pk,
                            Group.group_pk == GroupMember.group_pk,
                        ),
                    )
                    .where(
                        Account.service_name == self._service_name,
                        Account.account_id == account_id,
                        User.user_id == user_id,
                    )
                    .order_by(Group.group_id)
                )
            ).all()
        return tuple(group_id for group_id in group_ids if group_id is not None)

    async def add_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            self._require_active(account)
            group = await session.scalar(
                select(Group)
                .where(
                    Group.service_name == self._service_name,
                    Group.account_pk == account.account_pk,
                    Group.group_id == group_id,
                )
                .with_for_update()
            )
            if group is None:
                raise NotFoundError(group_id, "group")
            user = await self._lock_user(session, account, user_id)
            self._require_active(user)
            if (
                await session.scalar(
                    select(GroupMember.group_pk).where(
                        GroupMember.service_name == self._service_name,
                        GroupMember.account_pk == account.account_pk,
                        GroupMember.group_pk == group.group_pk,
                        GroupMember.user_pk == user.user_pk,
                    )
                )
                is None
            ):
                session.add(
                    GroupMember(
                        service_name=self._service_name,
                        account_pk=account.account_pk,
                        group_pk=group.group_pk,
                        user_pk=user.user_pk,
                    )
                )
        return True

    async def remove_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            self._require_active(account)
            group = await session.scalar(
                select(Group)
                .where(
                    Group.service_name == self._service_name,
                    Group.account_pk == account.account_pk,
                    Group.group_id == group_id,
                )
                .with_for_update()
            )
            if group is None:
                raise NotFoundError(group_id, "group")
            membership = await session.scalar(
                select(GroupMember)
                .join(
                    User,
                    and_(
                        User.service_name == GroupMember.service_name,
                        User.account_pk == GroupMember.account_pk,
                        User.user_pk == GroupMember.user_pk,
                    ),
                )
                .where(
                    GroupMember.service_name == self._service_name,
                    GroupMember.account_pk == account.account_pk,
                    GroupMember.group_pk == group.group_pk,
                    User.account_pk == account.account_pk,
                    User.user_id == user_id,
                )
            )
            if membership is None:
                return False
            await session.delete(membership)
            return True

    async def delete_group(self, account_id: str, group_id: str) -> None:
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            self._require_active(account)
            group = await session.scalar(
                select(Group)
                .where(
                    Group.service_name == self._service_name,
                    Group.account_pk == account.account_pk,
                    Group.group_id == group_id,
                )
                .with_for_update()
            )
            if group is None:
                raise NotFoundError(group_id, "group")
            if await session.scalar(
                select(func.count())
                .select_from(GroupMember)
                .where(
                    GroupMember.service_name == self._service_name,
                    GroupMember.account_pk == account.account_pk,
                    GroupMember.group_pk == group.group_pk,
                )
            ):
                raise FailedPreconditionError("Group must be empty before deletion")
            await session.delete(group)

    async def replace_active_user_api_key(
        self, account_id: str, user_id: str, api_key: str
    ) -> None:
        async with self._transaction() as session:
            account = await self._lock_account(session, account_id)
            self._require_active(account)
            user = await self._lock_user(session, account, user_id)
            self._require_active(user)
            sequence = await session.scalar(
                select(func.coalesce(func.max(Credential.sequence), 0)).where(
                    Credential.service_name == self._service_name,
                    Credential.user_pk == user.user_pk,
                )
            )
            await session.execute(
                update(Credential)
                .where(
                    Credential.service_name == self._service_name,
                    Credential.user_pk == user.user_pk,
                    Credential.revoked.is_(False),
                )
                .values(
                    revoked=True,
                    active_user_pk=None,
                    version=Credential.version + 1,
                    revoked_at=func.utc_timestamp(6),
                )
            )
            session.add(
                self._new_credential(
                    account_id,
                    user_id,
                    user.user_pk,
                    api_key,
                    sequence=sequence + 1,
                )
            )
            await session.flush()
            old_credentials = list(
                (
                    await session.scalars(
                        select(Credential.credential_id)
                        .where(
                            Credential.service_name == self._service_name,
                            Credential.user_pk == user.user_pk,
                            Credential.revoked.is_(True),
                        )
                        .order_by(Credential.sequence.desc())
                        .offset(_RETAINED_REVOKED_CREDENTIALS)
                    )
                ).all()
            )
            if old_credentials:
                await session.execute(
                    delete(Credential).where(
                        Credential.service_name == self._service_name,
                        Credential.credential_id.in_(old_credentials),
                    )
                )

    async def verify_api_key(
        self,
        api_key: str,
        *,
        account_id_hint: str | None = None,
        user_id_hint: str | None = None,
    ) -> tuple[str, str] | None:
        predicates = [
            Credential.service_name == self._service_name,
            Credential.lookup_digest == _credential_digest(api_key),
            Credential.revoked.is_(False),
            Account.deletion_task_id.is_(None),
            User.deletion_task_id.is_(None),
        ]
        if account_id_hint is not None:
            predicates.append(Account.account_id == account_id_hint)
        if user_id_hint is not None:
            predicates.append(User.user_id == user_id_hint)
        async with self._session_factory() as session:
            candidates = (
                await session.execute(
                    select(Credential.material, Account.account_id, User.user_id)
                    .join(
                        User,
                        and_(
                            User.service_name == Credential.service_name,
                            User.user_pk == Credential.user_pk,
                        ),
                    )
                    .join(
                        Account,
                        and_(
                            Account.service_name == User.service_name,
                            Account.account_pk == User.account_pk,
                        ),
                    )
                    .where(*predicates)
                )
            ).all()
        for material, candidate_account_id, candidate_user_id in candidates:
            if self._matches_credential(candidate_account_id, candidate_user_id, material, api_key):
                return candidate_account_id, candidate_user_id
        return None

    async def revoke_active_api_keys(self, account_id: str, user_id: str | None = None) -> None:
        async with self._transaction() as session:
            account = await session.scalar(
                select(Account)
                .where(
                    Account.service_name == self._service_name,
                    Account.account_id == account_id,
                )
                .with_for_update()
            )
            if account is None:
                return
            user_ids = select(User.user_pk).where(
                User.service_name == self._service_name,
                User.account_pk == account.account_pk,
                *([User.user_id == user_id] if user_id is not None else []),
            )
            await session.execute(
                update(Credential)
                .where(
                    Credential.service_name == self._service_name,
                    Credential.user_pk.in_(user_ids),
                    Credential.revoked.is_(False),
                )
                .values(
                    revoked=True,
                    active_user_pk=None,
                    version=Credential.version + 1,
                    revoked_at=func.utc_timestamp(6),
                )
            )

    async def get_user_key_fingerprint(self, account_id: str, user_id: str) -> str | None:
        async with self._session_factory() as session:
            material = await session.scalar(
                select(Credential.material)
                .join(
                    User,
                    and_(
                        User.service_name == Credential.service_name,
                        User.user_pk == Credential.user_pk,
                    ),
                )
                .join(
                    Account,
                    and_(
                        Account.service_name == User.service_name,
                        Account.account_pk == User.account_pk,
                    ),
                )
                .where(
                    Credential.service_name == self._service_name,
                    Credential.revoked.is_(False),
                    Account.account_id == account_id,
                    Account.deletion_task_id.is_(None),
                    User.user_id == user_id,
                    User.deletion_task_id.is_(None),
                )
            )
        return hashlib.sha256(material.encode()).hexdigest() if material else None
