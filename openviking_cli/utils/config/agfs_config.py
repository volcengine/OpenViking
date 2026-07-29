# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class DirectoryMarkerMode(str, Enum):
    """How S3 directory markers should be persisted."""

    NONE = "none"
    EMPTY = "empty"
    NONEMPTY = "nonempty"


class S3Config(BaseModel):
    """Configuration for S3 backend."""

    bucket: Optional[str] = Field(default=None, description="S3 bucket name")

    region: Optional[str] = Field(
        default=None,
        description="AWS region where the bucket is located (e.g., us-east-1, cn-beijing)",
    )

    access_key: Optional[str] = Field(
        default=None,
        description="S3 access key ID. If not provided, RAGFS may attempt to use environment variables or IAM roles.",
    )

    secret_key: Optional[str] = Field(
        default=None,
        description="S3 secret access key corresponding to the access key ID.",
    )

    endpoint: Optional[str] = Field(
        default=None,
        description="Custom S3 endpoint URL. Required for S3-compatible services like MinIO or LocalStack. "
        "Leave empty for standard AWS S3.",
    )

    prefix: Optional[str] = Field(
        default="",
        description="Optional key prefix for namespace isolation. All objects will be stored under this prefix.",
    )

    use_ssl: bool = Field(
        default=True,
        description="Enable/Disable SSL (HTTPS) for S3 connections. Set to False for local testing without HTTPS.",
    )

    use_path_style: bool = Field(
        default=True,
        description="true represent UsePathStyle for MinIO and some S3-compatible services; false represent VirtualHostStyle for TOS  and some S3-compatible services.",
    )

    directory_marker_mode: DirectoryMarkerMode = Field(
        default=DirectoryMarkerMode.EMPTY,
        description="How to persist S3 directory markers: 'none' skips marker creation, 'empty' writes a zero-byte marker, and 'nonempty' writes a non-empty marker payload. Defaults to 'empty'.",
    )

    disable_batch_delete: bool = Field(
        default=False,
        description="Disable batch delete (DeleteObjects) and use sequential single-object deletes instead. "
        "Required for S3-compatible services like Alibaba Cloud OSS that require a Content-MD5 header "
        "for DeleteObjects but AWS SDK v2 does not send it by default. Defaults to False.",
    )

    normalize_encoding_chars: str = Field(
        default="?#%+@",
        description="Characters to escape in S3 object keys as !HH hexadecimal bytes. "
        "Set to an empty string to disable key normalization. Defaults to ?#%+@.",
    )

    auto_detect_content_type: bool = Field(
        default=False,
        description="Automatically infer S3 object Content-Type from the object key filename extension "
        "during uploads. Disabled by default for backward compatibility.",
    )

    model_config = {"extra": "forbid"}

    def validate_config(self):
        """Validate S3 configuration completeness"""
        missing = []
        if not self.bucket:
            missing.append("bucket")
        if not self.endpoint:
            missing.append("endpoint")
        if not self.region:
            missing.append("region")
        if not self.access_key:
            missing.append("access_key")
        if not self.secret_key:
            missing.append("secret_key")

        if missing:
            raise ValueError(f"S3 backend requires the following fields: {', '.join(missing)}")

        return self


class QueueFSConfig(BaseModel):
    """Configuration for QueueFS backend."""

    mode: str = Field(
        default="shared",
        description="QueueFS namespace mode: 'shared' | 'worker'",
    )

    backend: str = Field(
        default="sqlite",
        description="QueueFS backend: 'memory' | 'sqlite' | 'sqlite3'",
    )

    db_path: Optional[str] = Field(
        default=None,
        description="SQLite database path for QueueFS when backend is 'sqlite' or 'sqlite3'.",
    )

    recover_stale_sec: int = Field(
        default=0,
        description="Recover processing messages older than this many seconds on startup (0 = recover all).",
    )

    busy_timeout_ms: int = Field(
        default=5000,
        description="SQLite busy timeout for QueueFS in milliseconds.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        valid_modes = {"shared", "worker"}
        if self.mode not in valid_modes:
            raise ValueError("queuefs mode must be one of: 'shared', 'worker'")

        valid_backends = {"memory", "sqlite", "sqlite3"}
        if self.backend not in valid_backends:
            raise ValueError("queuefs backend must be one of: 'memory', 'sqlite', 'sqlite3'")
        if self.recover_stale_sec < 0:
            raise ValueError("queuefs recover_stale_sec must be >= 0")
        if self.busy_timeout_ms < 0:
            raise ValueError("queuefs busy_timeout_ms must be >= 0")
        return self


class AGFSCacheProvider(str, Enum):
    """Cache providers supported by RAGFS."""

    MEMORY = "memory"
    YUANRONG = "yuanrong"
    MOONCAKE = "mooncake"
    REDIS = "redis"


class AGFSCacheTraversalMode(str, Enum):
    """Traversal strategy for cache-aware recursive RAGFS APIs."""

    BACKEND = "backend"
    CACHED_TRAVERSAL = "cached_traversal"


class YuanrongCacheConfig(BaseModel):
    """Configuration for Yuanrong cache provider."""

    host: str = Field(default="127.0.0.1", description="Yuanrong worker host")
    port: int = Field(default=31501, description="Yuanrong worker port")
    connect_timeout_ms: int = Field(default=5000, description="Yuanrong connect timeout")
    request_timeout_ms: int = Field(default=5000, description="Yuanrong request timeout")
    sdk_concurrency: int = Field(default=4, description="Yuanrong SDK concurrency")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if not self.host.strip():
            raise ValueError("yuanrong host must not be empty")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("yuanrong port must be between 1 and 65535")
        if self.connect_timeout_ms <= 0:
            raise ValueError("yuanrong connect_timeout_ms must be > 0")
        if self.request_timeout_ms <= 0:
            raise ValueError("yuanrong request_timeout_ms must be > 0")
        if self.sdk_concurrency <= 0:
            raise ValueError("yuanrong sdk_concurrency must be > 0")
        return self


class MooncakeCacheConfig(BaseModel):
    """Configuration for Mooncake cache provider."""

    local_hostname: str = Field(default="127.0.0.1", description="Mooncake local hostname")
    metadata_server: str = Field(
        default="http://127.0.0.1:8080/metadata",
        description="Mooncake metadata server",
    )
    master_server_addr: str = Field(
        default="127.0.0.1:50051",
        description="Mooncake master server address",
    )
    protocol: str = Field(default="tcp", description="Mooncake transfer protocol")
    device_name: str = Field(default="", description="Mooncake transport device name")
    global_segment_size: int = Field(default=512 << 20, description="Mooncake global segment size")
    local_buffer_size: int = Field(default=128 << 20, description="Mooncake local buffer size")
    replica_num: int = Field(default=2, description="Mooncake replica count")
    sdk_concurrency: int = Field(default=4, description="Mooncake SDK concurrency")
    operation_timeout_ms: int = Field(default=5000, description="Mooncake operation timeout")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        for name in ("local_hostname", "metadata_server", "master_server_addr", "protocol"):
            if not getattr(self, name).strip():
                raise ValueError(f"mooncake {name} must not be empty")
        if self.protocol not in {"tcp", "rdma", "ascend", "cxl", "nvlink", "barex"}:
            raise ValueError("mooncake protocol is unsupported")
        if self.global_segment_size <= 0:
            raise ValueError("mooncake global_segment_size must be > 0")
        if self.local_buffer_size <= 0:
            raise ValueError("mooncake local_buffer_size must be > 0")
        if self.replica_num <= 0:
            raise ValueError("mooncake replica_num must be > 0")
        if self.sdk_concurrency <= 0:
            raise ValueError("mooncake sdk_concurrency must be > 0")
        if self.operation_timeout_ms <= 0:
            raise ValueError("mooncake operation_timeout_ms must be > 0")
        return self


class RedisCacheConfig(BaseModel):
    """Configuration for Redis cache provider."""

    mode: str = Field(default="standalone", description="Redis deployment mode")
    endpoints: list[str] = Field(
        default_factory=lambda: ["redis://127.0.0.1:6379"],
        description="Redis endpoint URLs",
    )
    username: str = Field(default="", description="Redis ACL username")
    password_env: str = Field(default="", description="Environment variable containing password")
    pool_size: int = Field(default=32, description="Redis command concurrency")
    connect_timeout_ms: int = Field(default=1000, description="Redis connect timeout")
    command_timeout_ms: int = Field(default=20, description="Redis command timeout")
    key_prefix: str = Field(default="ragfs-cache", description="Redis cache key prefix")
    default_ttl_seconds: int = Field(default=3600, description="Redis default cache TTL")
    read_from_replica: bool = Field(default=False, description="Read from Redis replicas")

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if self.mode != "standalone":
            raise ValueError("redis mode must be standalone")
        if not self.endpoints:
            raise ValueError("redis endpoints must not be empty")
        if any(not endpoint.strip() for endpoint in self.endpoints):
            raise ValueError("redis endpoints must not contain empty values")
        if self.pool_size <= 0:
            raise ValueError("redis pool_size must be > 0")
        if self.connect_timeout_ms <= 0:
            raise ValueError("redis connect_timeout_ms must be > 0")
        if self.command_timeout_ms <= 0:
            raise ValueError("redis command_timeout_ms must be > 0")
        if not self.key_prefix.strip():
            raise ValueError("redis key_prefix must not be empty")
        if self.default_ttl_seconds < 0:
            raise ValueError("redis default_ttl_seconds must be >= 0")
        if self.read_from_replica:
            raise ValueError("redis read_from_replica is not supported in standalone mode")
        return self


class AGFSCacheConfig(BaseModel):
    """Configuration for optional RAGFS cache layer."""

    enabled: bool = Field(default=False, description="Enable RAGFS cache")
    provider: AGFSCacheProvider = Field(
        default=AGFSCacheProvider.MEMORY,
        description="RAGFS cache provider",
    )
    namespace: str = Field(default="openviking", description="RAGFS cache namespace")
    max_file_size_bytes: int = Field(
        default=1024 * 1024,
        description="Maximum full-file object size admitted to cache",
    )
    traversal_mode: AGFSCacheTraversalMode = Field(
        default=AGFSCacheTraversalMode.BACKEND,
        description="Traversal strategy for tree, glob, and grep",
    )
    bypass_prefixes: list[str] = Field(
        default_factory=list,
        description="Path prefixes that bypass cache",
    )
    yuanrong: YuanrongCacheConfig = Field(default_factory=YuanrongCacheConfig)
    mooncake: MooncakeCacheConfig = Field(default_factory=MooncakeCacheConfig)
    redis: RedisCacheConfig = Field(default_factory=RedisCacheConfig)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        if not self.namespace.strip():
            raise ValueError("cache namespace must not be empty")
        if self.max_file_size_bytes <= 0:
            raise ValueError("cache max_file_size_bytes must be > 0")
        return self


class AGFSConfig(BaseModel):
    """Configuration for RAGFS (Rust-based AGFS)."""

    name: str = Field(
        default="primary",
        description="Logical backend name, globally unique across primary and all backups",
    )

    path: Optional[str] = Field(
        default=None,
        description="[Deprecated in favor of `storage.workspace`] RAGFS data storage path. This will be ignored if `storage.workspace` is set.",
    )

    port: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS service port. Ignored by RAGFS.",
    )

    log_level: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS log level. Ignored by RAGFS.",
    )

    url: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS service URL. Ignored by RAGFS.",
    )

    mode: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS client mode. Ignored by RAGFS.",
    )

    impl: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS binding implementation selector. Ignored by RAGFS.",
    )

    backend: str = Field(
        default="local", description="RAGFS storage backend: 'local' | 's3' | 'memory'"
    )

    timeout: int = Field(default=10, description="RAGFS request timeout (seconds)")

    queue_db_path: Optional[str] = Field(
        default=None,
        description="Override path of the queuefs sqlite database file. "
        "Defaults to '{storage.workspace}/_system/queue/queue.db' when not set. "
        "Useful when the workspace volume does not support sqlite (e.g. some network filesystems).",
    )

    queuefs: QueueFSConfig = Field(
        default_factory=QueueFSConfig,
        description="QueueFS configuration.",
    )

    cache: AGFSCacheConfig = Field(
        default_factory=AGFSCacheConfig,
        description="RAGFS cache configuration.",
    )

    retry_times: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS retry count. Ignored by RAGFS.",
    )

    use_ssl: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS SSL switch. Ignored by RAGFS.",
    )

    lib_path: Any = Field(
        default=None,
        exclude=True,
        description="[Deprecated] Legacy AGFS binding library path. Ignored by RAGFS.",
    )

    # S3 backend configuration
    # These settings are used when backend is set to 's3'.
    # RAGFS will act as a gateway to the specified S3 bucket.
    s3: S3Config = Field(default_factory=lambda: S3Config(), description="S3 backend configuration")

    # Multi-write configuration
    backups: Optional[dict[str, Any]] = Field(
        default=None, description="Multi-write backups configuration. None = single backend mode."
    )
    redirects: Optional[List[dict[str, Any]]] = Field(
        default=None, description="Primary redirect policies."
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        deprecated_fields = (
            "port",
            "log_level",
            "url",
            "mode",
            "impl",
            "retry_times",
            "use_ssl",
            "lib_path",
        )
        for field_name in deprecated_fields:
            if field_name in self.model_fields_set:
                logger.warning(
                    "AGFSConfig: 'storage.agfs.%s' is deprecated and ignored after the RAGFS migration.",
                    field_name,
                )

        if self.backend not in ["local", "s3", "memory"]:
            raise ValueError(
                f"Invalid RAGFS backend: '{self.backend}'. Must be one of: 'local', 's3', 'memory'"
            )

        if self.backend == "local":
            pass

        elif self.backend == "s3":
            # Validate S3 configuration
            self.s3.validate_config()

        if self.queue_db_path is not None and self.queuefs.db_path is None:
            logger.warning(
                "AGFSConfig: 'storage.agfs.queue_db_path' is deprecated; "
                "prefer 'storage.agfs.queuefs.db_path'."
            )

        if self.queuefs.backend == "memory":
            if self.queuefs.db_path is not None or self.queue_db_path is not None:
                logger.warning(
                    "AGFSConfig: QueueFS backend is 'memory'; "
                    "db_path/queue_db_path will be ignored."
                )

        if self.redirects is not None and self.backups is None:
            raise ValueError(
                "redirects requires backups; single-backend mode does not support redirects"
            )

        return self
