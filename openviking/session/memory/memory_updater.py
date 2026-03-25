# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Memory updater - applies MemoryOperations directly.

This is the system executor that applies LLM's final output (MemoryOperations)
to the storage system.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openviking.server.identity import RequestContext
from openviking.session.memory.utils import (
    deserialize_full,
    serialize_with_metadata,
    resolve_all_operations,
    flat_model_to_dict,
)
from openviking.session.memory.dataclass import MemoryField
from openviking.session.memory.merge_op import MergeOpFactory, PatchOp
from openviking.session.memory.merge_op.base import FieldType, SearchReplaceBlock, StrPatch
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class MemoryUpdateResult:
    """Result of memory update operation."""

    def __init__(self):
        self.written_uris: List[str] = []
        self.edited_uris: List[str] = []
        self.deleted_uris: List[str] = []
        self.errors: List[Tuple[str, Exception]] = []

    def add_written(self, uri: str) -> None:
        self.written_uris.append(uri)

    def add_edited(self, uri: str) -> None:
        self.edited_uris.append(uri)

    def add_deleted(self, uri: str) -> None:
        self.deleted_uris.append(uri)

    def add_error(self, uri: str, error: Exception) -> None:
        self.errors.append((uri, error))

    def has_changes(self) -> bool:
        return (
            len(self.written_uris) > 0
            or len(self.edited_uris) > 0
            or len(self.deleted_uris) > 0
        )

    def summary(self) -> str:
        return (
            f"Written: {len(self.written_uris)}, "
            f"Edited: {len(self.edited_uris)}, "
            f"Deleted: {len(self.deleted_uris)}, "
            f"Errors: {len(self.errors)}"
        )




class MemoryUpdater:
    """
    Applies MemoryOperations to storage.

    This is the system executor that directly applies the LLM's final output.
    No function calls are used for write/edit/delete - these are executed directly.
    """

    def __init__(self, registry: Optional[MemoryTypeRegistry] = None, vikingdb=None):
        self._viking_fs = None
        self._registry = registry
        self._vikingdb = vikingdb

    def set_registry(self, registry: MemoryTypeRegistry) -> None:
        """Set the memory type registry for URI resolution."""
        self._registry = registry

    def _get_viking_fs(self):
        """Get or create VikingFS instance."""
        if self._viking_fs is None:
            self._viking_fs = get_viking_fs()
        return self._viking_fs

    async def apply_operations(
        self,
        operations: Any,
        ctx: RequestContext,
        registry: Optional[MemoryTypeRegistry] = None,
    ) -> MemoryUpdateResult:
        """
        Apply MemoryOperations directly using the flat model format.

        This is the system executor - no LLM involved at this stage.

        Args:
            operations: StructuredMemoryOperations from LLM (final output) with flat models
            ctx: Request context
            registry: Optional MemoryTypeRegistry for URI resolution

        Returns:
            MemoryUpdateResult with changes made
        """
        result = MemoryUpdateResult()
        viking_fs = self._get_viking_fs()

        if not viking_fs:
            logger.warning("VikingFS not available, skipping memory operations")
            return result

        # Use provided registry or internal registry
        resolved_registry = registry or self._registry
        if not resolved_registry:
            raise ValueError("MemoryTypeRegistry is required for URI resolution")

        # Get actual user/agent space from ctx
        user_space = ctx.user.user_space_name() if ctx and ctx.user else "default"
        agent_space = ctx.user.agent_space_name() if ctx and ctx.user else "default"

        # Resolve all URIs first
        resolved_ops = resolve_all_operations(
            operations,
            resolved_registry,
            user_space=user_space,
            agent_space=agent_space,
        )

        if resolved_ops.has_errors():
            for error in resolved_ops.errors:
                result.add_error("unknown", ValueError(error))
            return result

        # Apply write operations
        for op, uri in resolved_ops.write_operations:
            try:
                await self._apply_write(op, uri, ctx)
                result.add_written(uri)
            except Exception as e:
                logger.error(f"Failed to write memory: {e}")
                result.add_error(uri, e)

        # Apply edit operations
        for op, uri in resolved_ops.edit_operations:
            try:
                await self._apply_edit(op, uri, ctx)
                result.add_edited(uri)
            except Exception as e:
                logger.error(f"Failed to edit memory {uri}: {e}")
                result.add_error(uri, e)

        # Apply edit_overview operations
        for op, uri in resolved_ops.edit_overview_operations:
            try:
                await self._apply_edit_overview(op, uri, ctx)
                result.add_edited(uri)
            except Exception as e:
                logger.error(f"Failed to edit overview {uri}: {e}")
                result.add_error(uri, e)

        # Apply delete operations
        for _uri_str, uri in resolved_ops.delete_operations:
            try:
                await self._apply_delete(uri, ctx)
                result.add_deleted(uri)
            except Exception as e:
                logger.error(f"Failed to delete memory {uri}: {e}")
                result.add_error(uri, e)

        # Vectorize written and edited memories
        await self._vectorize_memories(result, ctx)

        logger.info(f"Memory operations applied: {result.summary()}")
        return result

    async def _apply_write(self, flat_model: Any, uri: str, ctx: RequestContext) -> None:
        """Apply write operation from a flat model."""
        viking_fs = self._get_viking_fs()

        # Convert model to dict
        model_dict = flat_model_to_dict(flat_model)

        # Set timestamps if not provided
        now = datetime.utcnow()
        created_at = model_dict.get("created_at", now)
        updated_at = model_dict.get("updated_at", now)

        # Extract content - priority: model_dict["content"]
        content = model_dict.pop("content", None) or ""

        # Get memory type schema to know which fields are business fields vs metadata
        memory_type_str = model_dict.get("memory_type")
        field_schema_map: Dict[str, MemoryField] = {}
        business_fields: Dict[str, Any] = {}

        if self._registry and memory_type_str:
            schema = self._registry.get(memory_type_str)
            if schema:
                field_schema_map = {f.name: f for f in schema.fields}
                # Extract business fields (those defined in the schema)
                for field_name in field_schema_map:
                    if field_name in model_dict:
                        business_fields[field_name] = model_dict[field_name]

        # Collect metadata - only include business fields (from schema, except content)
        metadata = business_fields.copy()

        # Serialize content with metadata
        full_content = serialize_with_metadata(content, metadata)

        # Write content to VikingFS
        # VikingFS automatically handles L0/L1/L2 and vector index updates
        await viking_fs.write_file(uri, full_content, ctx=ctx)
        logger.debug(f"Written memory: {uri}")

    async def _apply_edit(self, flat_model: Any, uri: str, ctx: RequestContext) -> None:
        """Apply edit operation from a flat model."""
        viking_fs = self._get_viking_fs()

        # Read current memory
        try:
            current_full_content = await viking_fs.read_file(uri, ctx=ctx) or ""
        except NotFoundError:
            logger.warning(f"Memory not found for edit: {uri}")
            return

        # Deserialize content and metadata
        current_plain_content, current_metadata = deserialize_full(current_full_content)

        # Convert flat model to dict
        model_dict = flat_model_to_dict(flat_model)

        # Get memory type schema
        memory_type_str = model_dict.get("memory_type") or current_metadata.get("memory_type")
        field_schema_map: Dict[str, MemoryField] = {}

        if self._registry and memory_type_str:
            schema = self._registry.get(memory_type_str)
            if schema:
                field_schema_map = {f.name: f for f in schema.fields}

        # Apply all fields (including content) through MergeOp
        new_plain_content = current_plain_content
        metadata = current_metadata or {}

        # Handle schema-defined fields first
        for field_name, field_schema in field_schema_map.items():
            if field_name in model_dict:
                patch_value = model_dict[field_name]

                # Get current value
                if field_name == "content":
                    current_value = current_plain_content
                else:
                    current_value = metadata.get(field_name)

                # Create MergeOp and apply
                merge_op = MergeOpFactory.from_field(field_schema)
                new_value = merge_op.apply(current_value, patch_value)

                # Update the field
                if field_name == "content":
                    new_plain_content = new_value
                else:
                    metadata[field_name] = new_value

        # Special case: handle content field even without schema (for backward compatibility/testing)
        if "content" in model_dict and "content" not in field_schema_map:
            from openviking.session.memory.merge_op import PatchOp
            from openviking.session.memory.merge_op.base import FieldType
            patch_value = model_dict["content"]
            merge_op = PatchOp(FieldType.STRING)
            new_plain_content = merge_op.apply(current_plain_content, patch_value)

        # Re-serialize with updated content and metadata
        new_full_content = serialize_with_metadata(new_plain_content, metadata)

        # Print diff of the edit
        self._print_diff(uri, current_plain_content, new_plain_content)

        await viking_fs.write_file(uri, new_full_content, ctx=ctx)
        logger.debug(f"Edited memory: {uri}")

    async def _apply_delete(self, uri: str, ctx: RequestContext) -> None:
        """Apply delete operation (uri is already a string)."""
        viking_fs = self._get_viking_fs()

        # Delete from VikingFS
        # VikingFS automatically handles vector index cleanup
        try:
            await viking_fs.rm(uri, recursive=False, ctx=ctx)
            logger.debug(f"Deleted memory: {uri}")
        except NotFoundError:
            logger.warning(f"Memory not found for delete: {uri}")
            # Idempotent - deleting non-existent file succeeds

    async def _apply_edit_overview(self, overview_model: Any, uri: str, ctx: RequestContext) -> None:
        """
        Apply edit operation for .overview.md file.

        Args:
            overview_model: Overview edit model with memory_type and overview fields
            uri: URI of the .overview.md file
            ctx: Request context
        """
        viking_fs = self._get_viking_fs()

        # Get overview value from model
        if hasattr(overview_model, 'overview'):
            overview_value = overview_model.overview
        elif isinstance(overview_model, dict):
            overview_value = overview_model.get('overview')
        else:
            raise ValueError("overview_model must have overview field")

        # Read current overview if exists
        current_overview = ""
        try:
            current_overview = await viking_fs.read_file(uri, ctx=ctx) or ""
        except NotFoundError:
            # File doesn't exist yet, start with empty content
            logger.debug(f"Overview file does not exist yet: {uri}")

        # Apply patch or replace based on overview_value type
        new_overview = current_overview
        if overview_value is None:
            # No overview provided, nothing to do
            logger.debug(f"No overview value provided, skipping edit")
            return
        elif isinstance(overview_value, str):
            # Direct string - replace
            new_overview = overview_value
        elif isinstance(overview_value, dict):
            # Dict format - convert to StrPatch if needed
            if 'blocks' in overview_value:
                # Already in StrPatch format
                blocks = [SearchReplaceBlock(**block) for block in overview_value['blocks']]
                str_patch = StrPatch(blocks=blocks)
            else:
                # Unexpected format
                raise ValueError(f"Invalid overview patch format: {overview_value}")

            # Apply patch
            patch_op = PatchOp(FieldType.STRING)
            new_overview = patch_op.apply(current_overview, str_patch)
        else:
            # StrPatch object
            patch_op = PatchOp(FieldType.STRING)
            new_overview = patch_op.apply(current_overview, overview_value)

        # Print diff of the edit
        self._print_diff(uri, current_overview, new_overview)

        # Write new overview
        await viking_fs.write_file(uri, new_overview, ctx=ctx)
        logger.debug(f"Edited overview: {uri}")

        # Extract and write .abstract.md
        await self._write_abstract_from_overview(uri, new_overview, ctx)

    def _extract_abstract_from_overview(self, overview_content: str) -> str:
        """Extract abstract from overview.md - same logic as SemanticProcessor."""
        lines = overview_content.split("\n")

        # Skip header lines (starting with #)
        content_lines = []
        in_header = True

        for line in lines:
            if in_header and line.startswith("#"):
                continue
            elif in_header and line.strip():
                in_header = False

            if not in_header:
                # Stop at first ##
                if line.startswith("##"):
                    break
                if line.strip():
                    content_lines.append(line.strip())

        return "\n".join(content_lines).strip()

    async def _write_abstract_from_overview(
        self, overview_uri: str, overview_content: str, ctx: RequestContext
    ) -> None:
        """Extract abstract from overview and write to .abstract.md."""
        viking_fs = self._get_viking_fs()

        # Extract abstract from overview
        abstract = self._extract_abstract_from_overview(overview_content)

        # Convert overview_uri (e.g., skills/.overview.md) to abstract path
        abstract_uri = overview_uri.replace("/.overview.md", "/.abstract.md")

        try:
            await viking_fs.write_file(abstract_uri, abstract, ctx=ctx)
            logger.debug(f"Wrote abstract: {abstract_uri}")
        except Exception as e:
            logger.warning(f"Failed to write abstract {abstract_uri}: {e}")

    def _print_diff(self, uri: str, old_content: str, new_content: str) -> None:
        """Print a diff of the memory edit using diff_match_patch."""
        try:
            from diff_match_patch import diff_match_patch
            dmp = diff_match_patch()

            # Compute character-level diff
            diffs = dmp.diff_main(old_content, new_content)
            dmp.diff_cleanupSemantic(diffs)

            # Build formatted output
            lines = []
            lines.append(f"\n{'=' * 60}")
            lines.append(f"MEMORY EDIT: {uri}")
            lines.append(f"{'=' * 60}")

            # ANSI styles
            STYLE_DELETE = "\033[9m\033[31m"  # 删除线 + 红色
            STYLE_INSERT = "\033[32m"          # 绿色
            STYLE_RESET = "\033[0m"

            for op, text in diffs:
                if op == 0:  # Equal - 正常显示
                    lines.append(text)
                elif op == -1:  # Delete - 红色删除线
                    lines.append(f"{STYLE_DELETE}{text}{STYLE_RESET}")
                elif op == 1:  # Insert - 绿色高亮
                    lines.append(f"{STYLE_INSERT}{text}{STYLE_RESET}")

            lines.append(f"{'=' * 60}\n")

            # Print directly
            print("\n".join(lines))
        except ImportError:
            # Fallback: just show file name
            logger.debug(f"diff_match_patch not available, skipping diff for {uri}")
        except Exception as e:
            logger.debug(f"Failed to print diff for {uri}: {e}")

    async def _vectorize_memories(
        self,
        result: MemoryUpdateResult,
        ctx: RequestContext,
    ) -> None:
        """Vectorize written and edited memory files.

        Args:
            result: MemoryUpdateResult with written_uris and edited_uris
            ctx: Request context
        """
        if not self._vikingdb:
            logger.debug("VikingDB not available, skipping vectorization")
            return

        viking_fs = self._get_viking_fs()

        # Collect all URIs to vectorize (skip .overview.md and .abstract.md - they are handled separately)
        uris_to_vectorize = []
        for uri in result.written_uris + result.edited_uris:
            if not uri.endswith("/.overview.md") and not uri.endswith("/.abstract.md"):
                uris_to_vectorize.append(uri)

        if not uris_to_vectorize:
            logger.debug("No memory files to vectorize")
            return

        for uri in uris_to_vectorize:
            try:
                # Read the memory file to get content
                content = await viking_fs.read_file(uri, ctx=ctx) or ""

                # Extract abstract (first 200 chars or first paragraph)
                abstract = content[:200].split("\n\n")[0] if content else ""

                # Get parent URI
                from openviking_cli.utils.uri import VikingURI

                parent_uri = VikingURI(uri).parent.uri

                # Create Context for vectorization
                from openviking.core.context import Context, ContextLevel, Vectorize
                from openviking.storage.queuefs.embedding_msg_converter import EmbeddingMsgConverter

                memory_context = Context(
                    uri=uri,
                    parent_uri=parent_uri,
                    is_leaf=True,
                    abstract=abstract,
                    context_type="memory",
                    level=ContextLevel.DETAIL,
                    user=ctx.user,
                    account_id=ctx.account_id,
                )
                memory_context.set_vectorize(Vectorize(text=content))

                # Convert to embedding msg and enqueue
                embedding_msg = EmbeddingMsgConverter.from_context(memory_context)
                if embedding_msg:
                    await self._vikingdb.enqueue_embedding_msg(embedding_msg)
                    logger.debug(f"Enqueued memory for vectorization: {uri}")

            except Exception as e:
                logger.warning(f"Failed to vectorize memory {uri}: {e}")
