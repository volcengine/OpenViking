# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator, model_validator

from openviking_cli.utils.logger import get_logger

from .agfs_config import AGFSConfig
from .http_store_config import HTTPStoreConfig
from .parse_output_config import ParseOutputConfig
from .transaction_config import TransactionConfig
from .vectordb_config import VectorDBBackendConfig

logger = get_logger(__name__)


class TaskStoreConfig(BaseModel):
    """Durable Task/Work backend selection."""

    provider: str = "file"
    params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, provider: str) -> str:
        provider = provider.strip().lower()
        if provider not in {"file", "mysql", "postgres", "sqlite", "http"}:
            raise ValueError(
                "storage.task_store.provider must be 'file', 'mysql', 'postgres', 'sqlite' or 'http', "
                f"got {provider!r}"
            )
        return provider


class WatchStoreConfig(BaseModel):
    """Watch store: a single-process control file or shared relational metadata."""

    provider: str = "file"
    params: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_provider(self):
        self.provider = self.provider.strip().lower()
        if self.provider not in {"file", "mysql", "postgres", "sqlite", "http"}:
            raise ValueError(
                "storage.watch_store.provider must be 'file', 'mysql', 'postgres', 'sqlite' or 'http', "
                f"got {self.provider!r}"
            )
        if self.provider == "sqlite":
            unknown = self.params.keys() - {
                "path",
                "busy_timeout_ms",
                "table",
                "credential_encryption_key",
            }
            if unknown:
                raise ValueError(
                    f"Unknown SQLite watch store parameters: {', '.join(sorted(unknown))}"
                )
            path = self.params.get("path")
            if path is not None and (
                not isinstance(path, str) or not path or not Path(path).is_absolute()
            ):
                raise ValueError("storage.watch_store.params.path must be an absolute local path")
            timeout = self.params.get("busy_timeout_ms", 5000)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
                raise ValueError(
                    "storage.watch_store.params.busy_timeout_ms must be a positive integer"
                )
        return self


class CompileStoreConfig(BaseModel):
    """Persistent Compile configuration and run history backend."""

    provider: str = "file"
    params: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_provider(self):
        self.provider = self.provider.strip().lower()
        if self.provider not in {"file", "mysql", "postgres", "sqlite", "http"}:
            raise ValueError(
                "storage.compile_store.provider must be 'file', 'mysql', 'postgres', 'sqlite' or 'http'"
            )
        return self


class StorageConfig(BaseModel):
    """Configuration for storage backend.

    The `workspace` field is the primary configuration for local data storage.
    When `workspace` is set, it overrides the deprecated `path` fields in
    `agfs` and `vectordb` configurations.
    """

    workspace: str = Field(default="./data", description="Local data storage path (primary)")
    skip_process_lock: bool = Field(
        default=False,
        description=(
            "Skip the workspace file lock for embedded vector backends ('local', 'cuvs'). "
            "Other backends do not acquire this lock. Use only when you explicitly accept "
            "the risk of multi-process contention on embedded vector storage."
        ),
    )

    agfs: AGFSConfig = Field(default_factory=AGFSConfig, description="AGFS configuration")

    transaction: TransactionConfig = Field(
        default_factory=TransactionConfig,
        description="Transaction mechanism configuration",
    )

    vectordb: VectorDBBackendConfig | None = Field(
        default_factory=VectorDBBackendConfig,
        description="VectorDB backend configuration",
    )

    parse_output: ParseOutputConfig = Field(
        default_factory=ParseOutputConfig,
        description="Where parsers write intermediate artifacts (agfs temp or local dir)",
    )

    task_store: TaskStoreConfig = Field(
        default_factory=TaskStoreConfig,
        description="Persistent Task/Work storage backend",
    )

    watch_store: WatchStoreConfig = Field(
        default_factory=WatchStoreConfig,
        description="Watch task and scheduling storage backend",
    )

    compile_store: CompileStoreConfig = Field(default_factory=CompileStoreConfig)
    task_service: HTTPStoreConfig | None = None

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional storage-specific parameters"
    )

    @model_validator(mode="before")
    @classmethod
    def ignore_deprecated_task_tracker(cls, data: Any) -> Any:
        if isinstance(data, dict) and "task_tracker" in data:
            data = dict(data)
            data.pop("task_tracker", None)
            logger.info(
                "'task_tracker' is deprecated and ignored. Task records are always persisted.",
                extra={"degraded": True},
            )
        return data

    @model_validator(mode="after")
    def resolve_paths(self):
        """Normalize storage paths and map legacy transaction settings into native pathlock config."""
        for store in (self.task_store, self.watch_store, self.compile_store):
            if store.provider == "http":
                if store.params or self.task_service is None:
                    raise ValueError(
                        "HTTP store connection settings belong in storage.task_service"
                    )
        if "lock_timeout" in self.transaction.model_fields_set:
            logger.info(
                "'transaction.lock_timeout' is deprecated and ignored. The runtime wait timeout is fixed at 0.0 seconds.",
                extra={"degraded": True},
            )

        if (
            "lock_expire" in self.transaction.model_fields_set
            and "lock_expire_secs" not in self.agfs.pathlock.model_fields_set
        ):
            self.agfs.pathlock.lock_expire_secs = self.transaction.lock_expire
            logger.info(
                "'transaction.lock_expire' is deprecated. Mapped to 'storage.agfs.pathlock.lock_expire_secs'."
            )
        elif "lock_expire" in self.transaction.model_fields_set:
            logger.info(
                "'transaction.lock_expire' is deprecated and ignored because 'storage.agfs.pathlock.lock_expire_secs' is set.",
                extra={"degraded": True},
            )

        if "redo_recovery_enabled" in self.transaction.model_fields_set:
            logger.info(
                "'transaction.redo_recovery_enabled' is deprecated and ignored. Session commit phase-2 recovery now resumes from the persistent 'session_commit' queue.",
                extra={"degraded": True},
            )

        # Update paths to use workspace (expand ~ first)
        workspace_path = Path(self.workspace).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.workspace = str(workspace_path)
        self.agfs.path = self.workspace
        if self.vectordb is not None:
            self.vectordb.path = self.workspace
        # logger.info(f"StorageConfig: Using workspace '{self.workspace}' for storage")
        return self

    def get_upload_temp_dir(self) -> Path:
        """Get the temporary directory for file uploads.

        Returns:
            Path to {workspace}/temp/upload directory
        """
        workspace_path = Path(self.workspace).expanduser().resolve()
        upload_temp_dir = workspace_path / "temp" / "upload"
        upload_temp_dir.mkdir(parents=True, exist_ok=True)
        return upload_temp_dir

    def build_task_tracker(self, agfs: Any, *, service_name: str | None = None):
        """Build the persistent TaskTracker from storage config."""
        from openviking.service.task_store import PersistentTaskStore, TaskWorkStore
        from openviking.service.task_tracker import TaskTracker

        provider = self.task_store.provider
        if "provider" not in self.task_store.model_fields_set:
            provider = "file" if PersistentTaskStore.has_task_store(agfs) else "sqlite"

        store: TaskWorkStore
        if provider == "http":
            from openviking.storage.task_service import HTTPTaskWorkStore

            store = HTTPTaskWorkStore(self._build_task_service())
        elif provider == "mysql":
            from openviking.service.task_store_mysql import MySQLTaskWorkStore

            store = MySQLTaskWorkStore(params=self.task_store.params, service_name=service_name)
        elif provider == "postgres":
            from openviking.service.task_store_postgres import PostgresTaskWorkStore

            store = PostgresTaskWorkStore(params=self.task_store.params, service_name=service_name)
        elif provider == "sqlite":
            from openviking.service.task_store_sqlite import SQLiteTaskWorkStore

            params = dict(self.task_store.params)
            params.setdefault("path", str(Path(self.workspace) / "task_store" / "tasks.db"))
            store = SQLiteTaskWorkStore(params=params, service_name=service_name)
        else:
            store = PersistentTaskStore(agfs)
        return TaskTracker(store=store)

    def build_watch_store(self, *, service_name: str, viking_fs: Any):
        """Build the configured Watch store."""
        if self.watch_store.provider == "http":
            from openviking.storage.task_service import HTTPWatchStore

            return HTTPWatchStore(self._build_task_service())
        if self.watch_store.provider == "postgres":
            from openviking.resource.watch_store_postgres import PostgresWatchStore

            return PostgresWatchStore(service_name=service_name, params=self.watch_store.params)
        if self.watch_store.provider == "mysql":
            from openviking.resource.watch_store_mysql import MySQLWatchStore

            return MySQLWatchStore(service_name=service_name, params=self.watch_store.params)
        if self.watch_store.provider == "sqlite":
            from openviking.resource.watch_store_sqlite import SQLiteWatchStore

            params = dict(self.watch_store.params)
            if params.get("path") is None:
                params["path"] = str(Path(self.workspace) / "_system" / "watch" / "watch.db")
            return SQLiteWatchStore(service_name=service_name, params=params)
        from openviking.resource.watch_store_file import FileWatchStore

        return FileWatchStore(viking_fs)

    def build_compile_store(self, service_name: str):
        """Build the selected Compile store; file is wired with AGFS."""
        provider = self.compile_store.provider
        if provider == "file":
            return None
        if provider == "http":
            from openviking.storage.task_service.compile import HTTPCompileStore

            return HTTPCompileStore(self._build_task_service())
        params = dict(self.compile_store.params)
        try:
            if provider == "mysql":
                from openviking.service.compile_store_mysql import MySQLCompileStore

                return MySQLCompileStore(params=params, service_name=service_name)
            if provider == "postgres":
                from openviking.service.compile_store_postgres import PostgresCompileStore

                return PostgresCompileStore(params=params, service_name=service_name)
            from openviking.service.compile_store_sqlite import SQLiteCompileStore

            params.setdefault("path", str(Path(self.workspace) / "compile_store" / "compile.db"))
            return SQLiteCompileStore(params=params, service_name=service_name)
        except ImportError as exc:
            raise RuntimeError(
                f"Compile {provider} storage requires openviking[{provider}]"
            ) from exc

    def _build_task_service(self):
        from openviking.storage.task_service import HTTPTaskService

        if self.task_service is None:
            raise ValueError("storage.task_service is required for HTTP stores")
        return HTTPTaskService(self.task_service)
