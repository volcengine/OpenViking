"""Small official EvoAgentBench run with the native OpenSpace v2 agent.

Train tasks use task-level native skill evolution after official verification.
Test tasks receive isolated copies of a frozen skill and quality snapshot.
Credentials are loaded from an existing local dotenv file, never serialized.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def serial(value):
    if dataclasses.is_dataclass(value):
        return {k: serial(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def official_reward(verifier, domain_name):
    """Keep official invalid-submission scores separate from grader outages."""
    error = verifier.get("error")
    missing_code = domain_name == "code_implementation" and error == "no_code_extracted"
    if error and error != "no_artifacts_found" and not missing_code:
        raise RuntimeError(f"Official grader unavailable: {error}")
    if domain_name == "code_implementation" and verifier.get("total", 0) < 1 and not missing_code:
        raise RuntimeError(f"No official code tests executed: {verifier}")
    reward = float(verifier["reward"])
    if not 0 <= reward <= 1 or (missing_code and reward != 0):
        raise ValueError(f"Invalid reward: {reward}")
    return reward


def persisted_session_storage(trial_dir, session_id):
    """Locate the exact original transcript without creating another session."""
    matches = list((trial_dir / "session-storage").glob(f"projects/*/sessions/{session_id}"))
    if len(matches) != 1 or not (matches[0] / "transcript.jsonl").is_file():
        raise RuntimeError(f"Original transcript unavailable for session {session_id}")
    return SimpleNamespace(
        session_dir=matches[0],
        transcript_path=matches[0] / "transcript.jsonl",
    )


def model_kwargs(args):
    """Forward Ark effort through extra_body for opaque endpoint model IDs."""
    kwargs = {
        "api_key": os.environ["ARK_API_KEY"],
        "api_base": args.base_url,
        "max_tokens": args.max_tokens,
        "temperature": getattr(args, "temperature", 0.7),
    }
    effort = getattr(args, "reasoning_effort", "auto")
    if effort != "auto":
        if effort not in {"low", "medium", "high"}:
            raise ValueError(f"Invalid reasoning effort: {effort}")
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return kwargs


def record_reasoning_settings(summary, effort):
    previous = summary.get("reasoning_effort", "auto")
    if previous != effort:
        summary.setdefault("reasoning_effort_changes", []).append(
            {
                "changed_at": time.time(),
                "previous": previous,
                "current": effort,
                "completed_results": len(summary["results"]),
            }
        )
    for record in summary["results"]:
        record.setdefault("reasoning_effort", previous)
    summary["reasoning_effort"] = effort


def add_evolution_arguments(parser):
    parser.add_argument(
        "--replay-policy",
        choices=("optional", "required"),
        default="optional",
        help="Use native benchmark optional replay, or require an external task runner.",
    )
    parser.add_argument(
        "--replay-command",
        help="External OpenSpace replay command; required for strict replay.",
    )


def evolution_policy(args):
    """Resolve the native environment overrides before starting any rollout."""
    from openspace.application import _env_bool

    policy = getattr(args, "replay_policy", "optional")
    if policy not in {"optional", "required"}:
        raise ValueError(f"Invalid replay policy: {policy}")
    required = _env_bool(
        "OPENSPACE_EVOLUTION_BEHAVIOR_EVAL_REQUIRE_REPLAY_RUNNER",
        policy == "required",
    )
    command = getattr(args, "replay_command", None) or os.environ.get(
        "OPENSPACE_EVOLUTION_REPLAY_COMMAND"
    )
    if required and not command:
        raise ValueError(
            "Required replay needs --replay-command or OPENSPACE_EVOLUTION_REPLAY_COMMAND; "
            "the default static eval_worker cannot approve skills."
        )
    return {"replay_required": required, "replay_command": command}


def record_evolution_policy(summary, args):
    policy = evolution_policy(args)
    previous = summary.get("evolution_policy")
    if previous is None and summary.get("results"):
        previous = {"replay_required": True, "replay_command": None}
    if previous is not None and previous != policy:
        raise ValueError("Resume evolution policy differs; use a new output directory.")
    summary["evolution_policy"] = policy


def skills_hash(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def database_rows(path):
    """Compare logical rows, independent of SQLite checkpoint/WAL layout."""
    with sqlite3.connect(path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        return {
            table: sorted(
                [list(row) for row in conn.execute(f'SELECT * FROM "{table}"')],
                key=str,
            )
            for table in tables
        }


def copy_database(source, target):
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def load_domain(args):
    sys.path.insert(0, str(args.repo / "benchmark/src"))
    from config import get_domain, get_domain_config, load_config

    load_config(str(args.benchmark_config))
    get_domain_config("code_implementation")["lcb_repo"] = str(
        args.repo.parent / "lcb-source/livecodebench"
    )
    get_domain_config("code_implementation")["cache_dir"] = str(
        args.repo.parent / "lcb-smoke-cache"
    )
    return get_domain("code_implementation")


def task_args(split, names):
    return argparse.Namespace(split=split, task=",".join(names), trials=1, parallel=1, live=False)


def configure_registry():
    """Limit the experimental library to explicit roots, without user skills."""
    import openspace.runtime.skill_registry as module
    from openspace.skill_engine import SkillRegistry

    def isolated_registry(
        *,
        workspace_dir=None,
        configured_skill_dirs=None,
        metadata_only_discovery=False,
    ):
        registry = SkillRegistry(
            skill_dirs=[Path(p) for p in configured_skill_dirs or []],
            metadata_only_discovery=metadata_only_discovery,
        )
        registry.discover()
        return registry

    module.build_skill_registry = isolated_registry


async def make_app(
    args,
    task_dir,
    skill_dir,
    store_path,
    *,
    train,
    domain_name="code_implementation",
    workspace=None,
    mcp_servers=None,
):
    from openspace.application import OpenSpace, OpenSpaceConfig

    grounding = json.loads(
        (args.openspace_repo / "openspace/openspace/config/config_grounding.json").read_text()
    )
    workspace = workspace or task_dir / "workspace"
    grounding["shell"]["working_dir"] = str(workspace)
    grounding["skills"]["skill_dirs"] = [str(skill_dir)]
    grounding["tool_quality"]["enable_persistence"] = False
    backend = "mcp" if domain_name == "information_retrieval" or mcp_servers else "shell"
    grounding["enabled_backends"] = [
        b for b in grounding["enabled_backends"] if b["name"] == backend
    ]
    if backend == "mcp":
        grounding["mcp"].update(
            {
                "eager_sessions": True,
                "check_dependencies": False,
                "auto_install": False,
                "servers": mcp_servers
                or {
                    "bcp-search": {
                        "command": "python3",
                        "env": {
                            "PATH": str(Path(sys.executable).parent)
                            + os.pathsep
                            + os.environ["PATH"],
                        },
                        "args": [
                            str(Path(__file__).with_name("corpus_search_mcp.py")),
                            "--corpus",
                            str(args.cache / "corpus.sqlite"),
                        ],
                    }
                },
            }
        )
        grounding["mcpServers"] = grounding["mcp"].pop("servers")
        if mcp_servers:
            grounding["mcpServers"] = mcp_servers
    config_path = task_dir / "grounding.json"
    save(config_path, grounding)
    llm_kwargs = model_kwargs(args)
    save(
        task_dir / "model-settings.json",
        {
            "model": args.model,
            "max_tokens": args.max_tokens,
            "reasoning_effort": getattr(args, "reasoning_effort", "auto"),
            "extra_body": llm_kwargs.get("extra_body", {}),
        },
    )
    policy = evolution_policy(args)
    config = OpenSpaceConfig(
        llm_model=f"openai/{args.model}",
        llm_timeout=300,
        llm_kwargs=llm_kwargs,
        backend_scope=[backend, "meta"],
        workspace_dir=str(workspace),
        capture_skill_dir=str(skill_dir),
        session_storage_dir=str(task_dir / "session-storage"),
        grounding_config_path=str(config_path),
        grounding_max_iterations=args.max_iterations,
        enable_recording=True,
        recording_log_dir=str(task_dir / "recordings"),
        # The official verifier runs between execution and native review.
        post_execution_mode="disabled",
        lsp_sync_start=False,
        scheduler_sync_start=False,
        scheduler_execute_sync_start=False,
        evolution_storage_root=str(args.output / "learning" if train else task_dir),
        skill_store_db_path=str(store_path),
        evidence_db_path=str(
            args.output / "learning/evidence.db" if train else task_dir / "evidence.db"
        ),
        evolution_engine_enabled=train,
        execution_analysis_sync_start=train,
        evolution_triggers_enabled=train,
        # Match native benchmark adapters; keep admission, semantic and routing gates.
        evolution_behavior_eval_require_replay_runner=policy["replay_required"],
        evolution_replay_command=policy["replay_command"],
        log_level="INFO",
        log_to_console=True,
        log_to_file=False,
        memory_mode="off",
    )
    save(
        task_dir / "evolution-settings.json",
        {
            **policy,
            "learning_enabled": train,
            "semantic_validation_enabled": config.evolution_capture_semantic_validation_enabled,
            "routing_eval_enabled": config.evolution_routing_eval_enabled,
            "single_observation_capture": config.evolution_allow_single_observation_capture,
            "replay_evidence": "external runner"
            if policy["replay_command"]
            else "static preflight only",
        },
    )
    app = OpenSpace(config=config)
    await app.initialize()
    if train:
        if not all(
            [
                app.get_skill_registry(),
                app.get_skill_store(),
                app.get_trigger_engine(),
                app.get_evolution_engine(),
                app.get_execution_analyzer(),
            ]
        ):
            raise RuntimeError("Native learning services did not initialize")
        # Prevent a pre-verifier watermark from freezing incomplete evidence.
        app.runtime.state.evidence_runtime_adapter._trigger_engine = None
    else:
        # Preserve quality/trust reads used by native discovery and ranking.
        # Test observations remain in the transcript, without updating the store.
        store = app.get_skill_store()
        if store:

            async def ignore_observation(*args, **kwargs):
                return None

            def ignore_observation_now(*args, **kwargs):
                return None

            for name in (
                "record_skill_selection",
                "record_skill_event",
                "record_trust_observation",
            ):
                setattr(store, name, ignore_observation)
            for name in ("record_skill_selection_now", "record_skill_event_now"):
                setattr(store, name, ignore_observation_now)
            store.set_evidence_sink(None)
    return app


async def add_verifier_evidence(
    app,
    task,
    result,
    verifier,
    trial_dir,
    *,
    domain_name="code_implementation",
    prompt=None,
    pass_threshold=1.0,
    verifier_actor="official_evoagentbench_verifier",
    session_storage=None,
):
    from openspace.skill_engine.evidence import EvidenceEvent, ResourceRef

    sid, tid = result.session_id, result.task_id
    adapter = app.runtime.state.evidence_runtime_adapter
    evidence = app.runtime.state.evidence_store
    verifier_path = trial_dir / "verifier.json"
    save(verifier_path, verifier)
    actor = verifier_actor
    passed = verifier["reward"] >= pass_threshold
    event = adapter._build_event(
        "tool_pipeline_complete",
        {
            "session_id": sid,
            "task_id": tid,
            "agent_id": actor,
            "tool_use_id": f"official-verify-{tid}",
            "tool_name": f"{domain_name}_verify",
            "backend": "benchmark",
            "status": "success" if passed else "error",
            "result_preview": json.dumps(verifier, ensure_ascii=False),
            "tool_result_metadata": {
                "persisted_path": str(verifier_path),
                "original_length": len(verifier_path.read_text()),
                "persistence_source": "official_domain_verifier",
            },
        },
    )
    evidence.ingest_event(event)
    # An authoritative final snapshot follows the agent's own completion claim.
    now = datetime.now(timezone.utc).isoformat()
    storage = session_storage or app.current_session_storage
    if storage is None:
        raise RuntimeError("Official evidence requires the original session storage")
    snapshot = ResourceRef(
        ref_id=f"runtime_snapshot:{tid}:official_verification",
        ref_type="runtime_snapshot",
        session_id=sid,
        task_id=tid,
        producer=actor,
        created_at=now,
        role="primary",
        preview=f"Official verification reward={verifier['reward']}",
        metadata={
            "status": "success" if passed else "failed",
            "instruction_preview": (prompt or task["question_content"])[:500],
            "iterations": result.iterations,
            "tool_execution_count": len(result.tool_executions) + 1,
            "active_skills": list(result.active_skills),
            "final_response_preview": result.text[:500],
            "session_dir": str(storage.session_dir),
            "transcript_path": str(storage.transcript_path),
            "official_reward": verifier["reward"],
            "official_pass_threshold": pass_threshold,
            "agent_reported_status": result.status,
        },
    )
    evidence.ingest_event(
        EvidenceEvent.create(
            event_type="official_verification_complete",
            producer=actor,
            created_at=now,
            session_id=sid,
            task_id=tid,
            idempotency_key=f"official:{sid}:{tid}",
            primary_refs=[snapshot],
        )
    )
    from openspace.skill_engine.evidence import EvidenceScope

    jobs = app.get_trigger_engine().evaluate_checkpoint(
        "task_session_persisted", EvidenceScope(session_id=sid, task_id=tid)
    )
    if not jobs:
        raise RuntimeError("No native task-finished analysis job was created")
    return await app.runtime.drain_evolution_jobs(
        job_ids=[job.job_id for job in jobs], limit=len(jobs)
    )
