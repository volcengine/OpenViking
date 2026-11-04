"""Validated, non-final progress snapshots for a single Compile assignment."""

from __future__ import annotations

import json
import stat
import unicodedata
from collections import Counter
from pathlib import PurePosixPath

from pydantic import Field

from vikingbot.agent.tools.base import Tool
from vikingbot.compile.hashing import content_hash, digest
from vikingbot.compile.pipeline_io import TaskFiles, retry_allowed
from vikingbot.compile.renderer import validate_relative_file_path
from vikingbot.compile.results import StrictModel


class CheckpointRequest(StrictModel):
    """Progress within the original input scope; snapshots never declare final delivery."""

    state_ref: str = Field(
        description="Scratch-relative JSON file containing the cumulative business state, "
        "including facts, exact calculations and provenance needed to continue.",
    )
    processed_inputs: list[str] = Field(
        description="Cumulative original input IDs whose business processing is recorded in state. "
        "Reading an input alone does not complete its processing.",
    )
    pending_inputs: list[str] = Field(
        description="Original input IDs still requiring business processing. Together with "
        "processed_inputs this must partition all assigned inputs without duplicates.",
    )
    pending_tasks: list[str] = Field(
        max_length=32,
        description="Unfinished synthesis, writing or validation tasks, even after all inputs are processed.",
    )
    next_action: str = Field(
        min_length=1,
        max_length=1200,
        description="Concrete next action for the next window; full evidence and state remain in files.",
    )
    draft_refs: list[str] = Field(
        default_factory=list,
        description="Scratch-relative files whose current contents must survive continuation. "
        "Exclude state_ref, which is saved automatically. Use [] when there are no drafts. "
        "Keep previously checkpointed draft paths in this list.",
    )


class CheckpointCompletion(StrictModel):
    """Disposition of inputs still pending at the last checkpoint, before final validation."""

    completed_inputs: list[str] = Field(
        description="Pending input IDs whose business processing is complete in the final result.",
    )
    excluded_inputs: dict[str, str] = Field(
        description="Pending input IDs intentionally excluded under the Skill, with a specific reason each.",
    )
    pending_tasks: list[str] = Field(
        max_length=0,
        description="Must be empty: final submission has no unfinished synthesis or validation work.",
    )


class CheckpointTool(Tool):
    """Persist progress atomically without accepting artifacts or changing completion state.

    The runner resets submitted for each window. latest retains the last committed
    manifest; immutable snapshots allow retries to discard uncheckpointed mutations.
    """

    name = "checkpoint"
    description = (
        "Save non-final progress and end this window. Store cumulative state in a JSON file first. "
        "Use emit instead when the complete assignment is finished. A checkpoint is not delivery."
    )
    parameters = CheckpointRequest.model_json_schema()

    def __init__(self, files: TaskFiles, sandbox, root: str, data: dict, fingerprint: str, metrics):
        """Bind progress storage to an immutable assignment scope and its private scratch root."""
        self.files, self.sandbox, self.root = files, sandbox, root
        # The immutable input set remains the final validation scope across all windows.
        self.inputs = {item["id"] for item in data.get("inputs", [])}
        self.fingerprint, self.metrics = fingerprint, metrics
        self.latest: dict | None = None
        self.submitted = False
        self.can_retry = True
        self.last_error = ""
        self.failures = Counter()

    @staticmethod
    def _relative(path: str) -> str:
        """Return a portable scratch path outside runtime input and snapshot namespaces.

        Windows device names, invalid characters and trailing-dot/space aliases are
        rejected on every host so a checkpoint keeps the same file identity across OSes.
        """
        relative = validate_relative_file_path(path)
        segments = relative.split("/")
        for segment in segments:
            stem = segment.partition(".")[0].rstrip(" ").upper()
            if (
                segment.endswith((".", " "))
                or any(character in '<>:"|?*' or ord(character) < 32 for character in segment)
                or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")
            ):
                raise ValueError(f"Checkpoint file path is not portable: {path}")
        if segments[0].casefold() in {"checkpoints", ".inputs"}:
            raise ValueError(
                "Runtime inputs and checkpoint snapshots cannot be mutable state or drafts"
            )
        return relative

    async def _check_files(
        self, paths: list[str], *, anchor: str | None = None, allow_missing=False
    ):
        """Reject filesystem aliases before snapshot reads or restoration writes.

        A known existing file anchors the host-visible private root. Remote regular-file
        inventories exclude symlinks; their API does not expose hard-link counts.
        Missing local restoration targets are allowed only beneath checked ancestors.
        """
        anchor = anchor or paths[0]
        local = self.sandbox.local_file_path(f"{self.root}/{anchor}")
        if local is None:
            inventory = await self.sandbox.list_files(self.root, max_entries=None)
            visible = {entry.path.replace("\\", "/") for entry in inventory}
            if any(f"{self.root}/{path}" not in visible for path in paths):
                raise ValueError("Checkpoint files must be regular files inside the private root")
            return
        root = local.parents[len(PurePosixPath(anchor).parts) - 1]
        for path in paths:
            candidate = root.joinpath(*PurePosixPath(path).parts)
            if not candidate.resolve().is_relative_to(root.resolve()):
                raise ValueError("Checkpoint file resolves outside the private root")
            for current in (candidate, *candidate.parents):
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    if allow_missing:
                        continue
                    raise
                if stat.S_ISLNK(info.st_mode) or (
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                ):
                    raise ValueError(
                        "Checkpoint files and directories cannot be links or junctions"
                    )
                if current == candidate and (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1):
                    raise ValueError("Checkpoint files must be regular files without hard links")
                if current == root:
                    break

    async def execute(self, tool_context=None, **kwargs):
        """Commit validated file snapshots and return a compact acknowledgement.

        Invalid partitions, regressing progress and unsafe or missing files return
        bounded repair feedback. Storage failures leave latest unchanged.
        """
        try:
            request = CheckpointRequest.model_validate(kwargs)
            processed, pending = set(request.processed_inputs), set(request.pending_inputs)
            if (
                len(processed) != len(request.processed_inputs)
                or len(pending) != len(request.pending_inputs)
                or processed & pending
                or processed | pending != self.inputs
            ):
                raise ValueError("Processed and pending IDs must partition all assigned input IDs")
            if not pending and not request.pending_tasks:
                raise ValueError("Finished assignments must use emit, not checkpoint")
            if any(not task.strip() or len(task) > 300 for task in request.pending_tasks):
                raise ValueError("Pending tasks must contain 1 to 300 characters each")
            if not request.next_action.strip():
                raise ValueError("Next action must not be blank")
            state_ref = self._relative(request.state_ref)
            draft_refs = [self._relative(path) for path in request.draft_refs]
            references = [state_ref, *draft_refs]
            state_identity = unicodedata.normalize("NFC", state_ref).casefold()
            if any(
                unicodedata.normalize("NFC", path).casefold() == state_identity
                for path in draft_refs
            ):
                raise ValueError(
                    "Remove state_ref from draft_refs: state is saved automatically. "
                    "Resubmit checkpoint with only the other draft paths, or [] if none; "
                    "do not rewrite the state file."
                )
            identities = {unicodedata.normalize("NFC", path).casefold() for path in references}
            if len(identities) != len(references):
                raise ValueError("State and draft references must name distinct files")
            await self._check_files(references)
            raw_state = await self.sandbox.read_file_bytes(f"{self.root}/{state_ref}")
            state = json.loads(raw_state)
            if not isinstance(state, (dict, list)) or not state:
                raise ValueError("Checkpoint state must contain a nonempty JSON object or array")
            contents = {state_ref: raw_state}
            for path in draft_refs:
                raw = await self.sandbox.read_file_bytes(f"{self.root}/{path}")
                if not raw:
                    raise ValueError("Checkpoint draft files must not be empty")
                contents[path] = raw
            hashes = {path: content_hash(raw) for path, raw in contents.items()}
            state_hash = digest(state)
            previous = self.latest
            if previous:
                if previous["fingerprint"] != self.fingerprint:
                    raise ValueError("Checkpoint assignment fingerprint mismatch")
                if not set(previous["processed_inputs"]) <= processed:
                    raise ValueError("Processed input IDs cannot regress across checkpoints")
                if not set(previous["draft_refs"]) <= set(draft_refs):
                    raise ValueError("Checkpoint must retain previously saved draft paths")
                drafts_changed = any(
                    hashes[path] != previous["hashes"].get(path) for path in draft_refs
                )
                state_advanced = state_hash != previous["state_hash"]
                if not (
                    processed - set(previous["processed_inputs"])
                    or drafts_changed
                    or state_advanced
                ):
                    raise ValueError(
                        "Checkpoint must advance processed inputs, drafts or cumulative work"
                    )
            sequence = previous["sequence"] + 1 if previous else 1
            snapshots = {}
            for index, (path, raw) in enumerate(contents.items()):
                snapshot = f"checkpoints/{sequence}/{index}"
                await self.sandbox.write_file_bytes(f"{self.root}/{snapshot}", raw)
                snapshots[path] = snapshot
            manifest = {
                "fingerprint": self.fingerprint,
                "sequence": sequence,
                "state_ref": state_ref,
                "state_hash": state_hash,
                "draft_refs": draft_refs,
                "processed_inputs": sorted(processed),
                "pending_inputs": sorted(pending),
                "pending_tasks": request.pending_tasks,
                "next_action": request.next_action,
                "snapshots": snapshots,
                "hashes": hashes,
                "manifest_file": f"checkpoints/{sequence}/manifest.json",
            }
            await self.sandbox.write_file(
                f"{self.root}/{manifest['manifest_file']}",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            manifest_ref = await self.files.put(
                f"checkpoints/{digest([self.fingerprint, self.root])}/{sequence}", manifest
            )
            self.latest = {**manifest, "manifest_ref": manifest_ref}
            self.submitted = True
            if self.metrics is not None:
                self.metrics["checkpoints"] += 1
            return (
                f"Progress saved at {manifest['manifest_file']}; "
                f"{len(pending)} inputs and {len(request.pending_tasks)} tasks remain. "
                "This is not a final result."
            )
        except (ValueError, TypeError, OSError) as exc:
            self.last_error = str(exc)[:1600]
            self.can_retry = retry_allowed(self.failures, self.last_error)
            if self.metrics is not None:
                self.metrics["validation_failures"] += 1
                if self.can_retry:
                    self.metrics["repairs"] += 1
            return "Error: " + self.last_error

    def validate_completion(self, completion: dict | None) -> None:
        """Require explicit completion or justified exclusion of checkpoint-pending inputs.

        A declaration cannot replace the caller's final business and file validators.
        With no saved checkpoint, the original single-window validation is unchanged.
        Invalid types, references and unfinished work raise ValueError.
        """
        if self.latest is None:
            return
        if completion is None:
            raise ValueError("Final emit after a checkpoint requires a completion declaration")
        request = CheckpointCompletion.model_validate(completion)
        completed = set(request.completed_inputs)
        excluded = set(request.excluded_inputs)
        if (
            len(completed) != len(request.completed_inputs)
            or completed & excluded
            or completed | excluded != set(self.latest["pending_inputs"])
        ):
            raise ValueError(
                "Completion must account for every checkpoint-pending input exactly once"
            )
        if any(
            not reason.strip() or len(reason) > 1200 for reason in request.excluded_inputs.values()
        ):
            raise ValueError("Excluded input reasons must contain 1 to 1200 characters each")

    async def restore(self):
        """Restore the latest immutable state and drafts after verifying every snapshot.

        Assignment and hash mismatches raise before restoration writes; storage errors
        propagate. No checkpoint is a no-op. Final history and publication are untouched.
        """
        if self.latest is None:
            return
        if self.latest["fingerprint"] != self.fingerprint:
            raise ValueError("Checkpoint assignment fingerprint mismatch")
        snapshots = self.latest["snapshots"]
        await self._check_files(
            [*snapshots, *snapshots.values()],
            anchor=next(iter(snapshots.values())),
            allow_missing=True,
        )
        contents = {}
        for path, snapshot in self.latest["snapshots"].items():
            relative = self._relative(path)
            safe_snapshot = validate_relative_file_path(snapshot)
            raw = await self.sandbox.read_file_bytes(f"{self.root}/{safe_snapshot}")
            if content_hash(raw) != self.latest["hashes"][path]:
                raise ValueError("Checkpoint snapshot hash mismatch")
            contents[relative] = raw
        for path, raw in contents.items():
            await self.sandbox.write_file_bytes(f"{self.root}/{path}", raw)
