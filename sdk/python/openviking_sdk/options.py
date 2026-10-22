# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Dict, Generic, List, Literal, Mapping, Optional, TypeVar, Union

from typing_extensions import TypedDict

ExtraFields = Mapping[str, Any]
TargetURI = Union[str, List[str]]
Level = Union[int, str, List[int]]
TimeField = Literal["updated_at", "created_at"]
ProcessingMode = Literal["semantic_and_vectors", "vectors_only"]


class _ExtraOptions(TypedDict, total=False):
    extra: ExtraFields


class FindOptions(_ExtraOptions, total=False):
    image: Any
    node_limit: int
    score_threshold: float
    filter: Dict[str, Any]
    context_type: Any
    include_provenance: bool
    tags: List[str]
    since: str
    until: str
    time_field: TimeField
    level: Level
    read_content: bool
    telemetry: Any
    events_time_decay_protection: Optional[str]


class SearchOptions(FindOptions, total=False):
    search_type: Literal["semantic", "keywords"]


class SearchContextOptions(_ExtraOptions, total=False):
    search_type: Literal["semantic", "keywords"]
    image: Any
    node_limit: int
    score_threshold: float
    filter: Dict[str, Any]
    context_type: Any
    include_provenance: bool
    tags: List[str]
    since: str
    until: str
    time_field: TimeField
    events_time_decay_protection: Optional[str]
    query_expansion: Literal["off", "auto"]
    max_tokens: int
    quotas: Dict[str, int]
    purpose: Literal["chat", "coding"]
    detail: Union[str, Dict[str, str]]
    dedup_turns: int
    exclude_uris: List[str]
    peer_scope: Literal["actor", "all"]
    other_peer_penalty: Union[float, Dict[str, float]]
    rewrite: Union[bool, Literal["auto"]]
    rewrite_max_bullets: int
    telemetry: Any


class AddResourceOptions(_ExtraOptions, total=False):
    acl: Dict[str, Any]
    reason: str
    instruction: str
    create_parent: bool
    strict: bool
    ignore_dirs: str
    include: str
    exclude: str
    directly_upload_media: bool
    preserve_structure: bool
    watch_interval: float
    args: Dict[str, Any]
    telemetry: Any
    processing_mode: ProcessingMode
    add_type: str
    tags: List[str]
    tag_mode: Literal["replace", "append", "clear"]


class AddSkillOptions(_ExtraOptions, total=False):
    telemetry: Any
    target_uri: str


class UpdateSkillOptions(AddSkillOptions, total=False):
    source_metadata: Dict[str, Any]


class WriteOptions(_ExtraOptions, total=False):
    acl: Dict[str, Any]
    telemetry: Any
    processing_mode: ProcessingMode
    tags: List[str]
    tag_mode: Literal["replace", "append", "clear"]


class BatchWriteOptions(_ExtraOptions, total=False):
    telemetry: Any
    processing_mode: ProcessingMode
    # Return target-state conflicts without overwriting them or blocking compatible writes.
    skip_conflicts: bool


class CompileOptions(_ExtraOptions, total=False):
    instruction: str
    args: Dict[str, Any]


class SetTagsOptions(_ExtraOptions, total=False):
    telemetry: Any


class ReindexOptions(_ExtraOptions, total=False):
    force: bool
    tags: List[str]
    tag_mode: Literal["replace", "append", "clear"]


PageItemT = TypeVar("PageItemT")


class OffsetPage(TypedDict, Generic[PageItemT]):
    items: List[PageItemT]
    offset: int
    limit: int
    has_more: bool
    total: Optional[int]


class CursorPage(TypedDict, Generic[PageItemT]):
    items: List[PageItemT]
    limit: int
    has_more: bool
    next_cursor: Optional[str]


class AccountListItem(TypedDict, total=False):
    account_id: str
    created_at: str
    user_count: int
    status: str
    task_id: str


class UserListItem(TypedDict, total=False):
    user_id: str
    role: str
    api_key: str
    key_prefix: str
    api_key_available: bool


class AuditLogItem(TypedDict, total=False):
    request_id: str
    account_id: str
    user_id: Optional[str]
    method: str
    route: str
    api_type: str
    status_code: int
    duration_ms: float
    error_code: Optional[str]
    error_message: Optional[str]
    error_details: Optional[Dict[str, Any]]
    created_at: str


class SessionListItem(TypedDict):
    session_id: str
    uri: str
    is_dir: bool
    mod_time: str


TaskStatus = Literal["pending", "running", "cancelling", "completed", "failed", "cancelled"]


class TaskListItem(TypedDict, total=False):
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: float
    updated_at: float
    processing_seconds: Optional[float]
    resource_id: Optional[str]
    meta: Dict[str, Any]
    stage: Optional[str]
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    created_at_iso: str
    updated_at_iso: str


class SkillListItem(TypedDict, total=False):
    type: str
    name: str
    uri: str
    root_uri: str
    skill_md_uri: str
    description: str
    tags: List[str]
    allowed_tools: List[str]


class WatchListItem(TypedDict, total=False):
    task_id: str
    path: str
    to_uri: Optional[str]
    created_at: Optional[str]
    is_active: bool


class FilesystemListItem(TypedDict, total=False):
    name: str
    uri: str
    isDir: bool


class ExperienceTrajectoryItem(TypedDict, total=False):
    uri: str
    name: str
    description: str
    created_at: Any
    updated_at: Any


class CreateSessionOptions(_ExtraOptions, total=False):
    memory_policy: Dict[str, Any]
    auto_commit_policy: Optional[Dict[str, Any]]
    memory_extraction_config: Dict[str, Any]
    telemetry: Any


class UpdateSessionConfigOptions(_ExtraOptions, total=False):
    auto_commit_policy: Optional[Dict[str, Any]]
    memory_extraction_config: Dict[str, Any]
    telemetry: Any


class _RequiredMessage(TypedDict):
    role: str


class Message(_RequiredMessage, total=False):
    content: str
    parts: List[Any]
    created_at: str
    peer_id: str
    turn_id: str
    message_kind: Literal["user_query", "assistant_step", "tool_transport", "checkpoint"]
    source_message_ids: List[str]
    telemetry: Any


class AddMessageOptions(_ExtraOptions, total=False):
    created_at: str
    peer_id: str
    turn_id: str
    message_kind: Literal["user_query", "assistant_step", "tool_transport", "checkpoint"]
    source_message_ids: List[str]
    telemetry: Any


class BatchAddMessagesOptions(_ExtraOptions, total=False):
    telemetry: Any


class CommitSessionOptions(_ExtraOptions, total=False):
    enable_working_memory: Optional[bool]
    retention_mode: Literal["turn_budget"]
    keep_recent_turn_count: int
    retained_message_token_budget: int
    min_raw_tail_steps: int
    event_tags: List[str]
    telemetry: Any


class ExperienceTrajectoryOptions(TypedDict, total=False):
    limit: int
    offset: int
    start_date: str
    end_date: str


class ExperienceOutcomeOptions(TypedDict, total=False):
    start_date: str
    end_date: str


class ResolveAssetsOptions(_ExtraOptions, total=False):
    catalog_yaml: str
    manifest_label: str
    catalog_label: str


class AssetGitAuth(TypedDict, total=False):
    username: str
    token: str


class PreflightAssetOptions(_ExtraOptions, total=False):
    branch: str
    commit: str
    auth_config: AssetGitAuth


class SearchContextEntry(TypedDict, total=False):
    uri: str
    category: str
    score: float
    detail: str
    text: str
    origin: str


class SearchContextResult(TypedDict, total=False):
    entries: List[SearchContextEntry]
    rendered: str
    digest: str
    stats: Dict[str, Any]
