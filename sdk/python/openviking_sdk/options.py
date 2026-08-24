from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, TypedDict, Union

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


class SearchOptions(FindOptions, total=False):
    pass


class SearchContextOptions(_ExtraOptions, total=False):
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
    tag_mode: Literal["replace", "append"]


class AddSkillOptions(_ExtraOptions, total=False):
    telemetry: Any
    target_uri: str


class UpdateSkillOptions(AddSkillOptions, total=False):
    source_metadata: Dict[str, Any]


class WriteOptions(_ExtraOptions, total=False):
    telemetry: Any
    processing_mode: ProcessingMode


class BatchWriteOptions(_ExtraOptions, total=False):
    telemetry: Any


class SetTagsOptions(_ExtraOptions, total=False):
    telemetry: Any


class ReindexOptions(_ExtraOptions, total=False):
    tags: List[str]
    tag_mode: Literal["replace", "append"]


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
