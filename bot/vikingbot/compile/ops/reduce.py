"""Merge grouped Markdown and resolve output path collisions with isolated file workers."""

from openviking.core.namespace import relative_uri_path
from vikingbot.compile.hashing import digest
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS, bounded_jobs
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.results import Document


async def run(runtime, node, groups):
    """Produce Markdown per group; failed groups never contribute partial scratch files."""
    transform = getattr(runtime.contract, node.task)

    async def merge(group):
        """Combine compatible facts without erasing version, applicability or source differences."""
        refs = sorted({ref for doc in group.documents for ref in doc.source_refs})
        try:
            outputs = await runtime.generate(
                f"{node.name}-{group.group_id}",
                transform.instructions
                + "\nMerge duplicate facts and retain unique details, citations, "
                "conditions, conflicting claims and applicability. One group may produce multiple files. "
                "Read selected historical files before updating; preserve unrelated content.",
                {
                    "documents": [vars(doc) for doc in group.documents],
                    "historical_files": [
                        relative_uri_path(runtime.target, uri) for uri in group.target_uris
                    ],
                    "inputs": [{"payload": {"source_ranges": refs}}],
                },
                refs,
            )
        except BaseException:
            for ref in refs:
                runtime.status[ref] = "failed"
            raise
        if not outputs:
            for ref in refs:
                runtime.status[ref] = "unreferenced"
        return outputs

    outputs = await bounded_jobs(
        groups,
        merge,
        concurrency=runtime.limits.merge_concurrency,
        metrics=runtime.metrics,
        failures=runtime.failures,
    )
    return [document for job in outputs for document in job]


async def resolve_files(runtime, documents):
    """Resolve same-path Markdown candidates before publication, preserving unique outputs.

    File workers merge compatible content or rename independent pages. Output paths are
    reserved across workers; retries are bounded. Failed candidates remain unpublished and
    their sources are marked failed; cancellation propagates through the worker queue.
    """
    candidates = {}
    for document in documents:
        candidates.setdefault(document.path, []).append(document)
    if len(candidates) == len(documents):
        return documents
    reserved = {path: path for path in candidates}
    unique = [items[0] for items in candidates.values() if len(items) == 1]

    async def resolve(item):
        """Resolve one path and reserve its chosen output names without an intervening await."""
        path, items = item
        refs = sorted({ref for doc in items for ref in doc.source_refs})
        try:
            # Documents here were selected only from naturally completed workers.
            # Byte equality proves there is nothing for a collision worker to merge;
            # differing facts, citations or line endings still take the normal path.
            try:
                valid_path = validate_relative_file_path(path).lower().endswith(".md")
            except ValueError:
                valid_path = False
            if valid_path:
                first = await runtime.files.sandbox.read_file_bytes(items[0].workspace_path)
                try:
                    nonempty = bool(first.decode("utf-8").strip())
                except UnicodeDecodeError:
                    nonempty = False
                if nonempty:
                    identical = True
                    for document in items[1:]:
                        if (
                            await runtime.files.sandbox.read_file_bytes(document.workspace_path)
                            != first
                        ):
                            identical = False
                            break
                    if identical:
                        runtime.metrics["identical_collision_reuses"] += 1
                        return [Document(path, items[0].workspace_path, refs)]
            for _ in range(MAX_JOB_ATTEMPTS):
                outputs = await runtime.generate(
                    f"reduce_merge-{digest(path)[:24]}",
                    "Resolve the assigned Markdown path collision following the Skill. "
                    "Read every candidate; combine compatible contributions, deduplicate equivalent "
                    "content, or rename independent pages. Preserve unique facts, source citations, "
                    "conditions, applicability and conflicting claims. Write only the resolved "
                    "candidate files; do not generate extra indexes or unrelated pages. "
                    "reserved_paths belong to other outputs and must not be written.",
                    {
                        "path": path,
                        "documents": [vars(doc) for doc in items],
                        "reserved_paths": sorted(
                            p for p, owner in reserved.items() if owner != path
                        ),
                        "inputs": [{"payload": {"source_ranges": refs}}],
                    },
                    refs,
                )
                if not outputs:
                    raise ValueError(f"No Markdown outputs resolve {path}")
                blocked = {doc.path for doc in outputs if reserved.get(doc.path, path) != path}
                if not blocked:
                    reserved.update((doc.path, path) for doc in outputs)
                    return outputs
            raise ValueError(f"Output paths are reserved by other jobs: {sorted(blocked)}")
        except BaseException:
            for ref in refs:
                runtime.status[ref] = "failed"
            raise

    resolved = await bounded_jobs(
        ((path, items) for path, items in candidates.items() if len(items) > 1),
        resolve,
        concurrency=runtime.limits.merge_concurrency,
        metrics=runtime.metrics,
        failures=runtime.failures,
    )
    return unique + [doc for job in resolved for doc in job]
