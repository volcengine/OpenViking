"""Sequential family-local OpenSpace runs using official Harbor environments/verifier."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shlex
import sys
import time
import traceback
from pathlib import Path

from benchmark.evoagentbench.openspace_run import (
    add_verifier_evidence,
    make_app,
    save,
    serial,
    skills_hash,
)

PREAMBLE = """Solve this SkillFlow workflow inside its isolated Docker container.
Use MCP bash/read/write/ls tools: their working directory is {workdir} in the
container, not the host. Inspect the provided input files and produce the exact
artifact(s) requested. Validate content, calculations, formulas and output paths.
Do not inspect verifier-owned /tests, /solution or /logs/verifier, or reference
solutions, or modify checker files. Official verification runs after you finish.
If an injected skill references an absolute script/resource path, that same path
has been copied into the container and is accessible through bash.
Only finish once the requested artifact is on disk.

Task:
"""

FILE_INVENTORY = """import pathlib,hashlib,json,os,sys
roots=[pathlib.Path(p) for p in json.loads(sys.argv[1])]
rows={}
for root in roots:
    for folder,dirs,files in os.walk(root):
        folder=pathlib.Path(folder)
        dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('__pycache__','node_modules')]
        for name in files:
            if name.startswith('.'):
                continue
            p=folder/name
            if p.is_file() and not p.is_symlink():
                digest=hashlib.sha256()
                with p.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
                rows[str(p)]=digest.hexdigest()
print(json.dumps(rows))
"""


async def inventory(environment):
    workdir = getattr(environment, "_skillflow_workdir", "/root")
    if not isinstance(workdir, str):
        workdir = "/root"
    roots = sorted({"/root", workdir, "/tmp/skillflow-tool-output"})
    result = await environment.exec(
        "python3 -c " + shlex.quote(FILE_INVENTORY) + " " + shlex.quote(json.dumps(roots)),
        timeout_sec=120,
    )
    if result.return_code:
        raise RuntimeError("Could not inventory task artifacts")
    return json.loads(result.stdout)


class AgentBudgetExceeded(TimeoutError):
    """Only the outer official agent deadline, not a verifier or build timeout."""


async def execute_with_budget(app, request, seconds):
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            return await app.execute(request)
    except TimeoutError as exc:
        if deadline.expired():
            raise AgentBudgetExceeded(f"Official agent budget exhausted: {seconds}s") from exc
        raise


def recover_legacy_timeout(record, trial):
    """Keep a timed-out recorded rollout whose old runner removed its artifacts."""
    if (
        record.get("infrastructure_error") != "TimeoutError: "
        or (trial / "execution.json").exists()
    ):
        return False
    metadata = list((trial / "session-storage").glob("projects/*/sessions/*/metadata.json"))
    transcripts = list((trial / "session-storage").glob("projects/*/sessions/*/transcript.jsonl"))
    if not metadata or not any(p.stat().st_size for p in transcripts):
        return False
    # Preserve the old record. Its official grade cannot be recovered after container removal.
    save(trial / "recovery-audit/summary-before-recovery.json", record)
    record.update(
        previous_infrastructure_error=record["infrastructure_error"],
        infrastructure_error=None,
        agent_status="timeout",
        task_timeout=True,
        grading_status="unscored_artifacts_lost",
        recovery_note="Old runner removed the container before verification; rollout retained, not repeated.",
    )
    save(trial / "summary.json", record)
    return True


async def collect_artifacts(environment, paths, input_files):
    output_files = await inventory(environment)
    changed = {p: sha for p, sha in output_files.items() if input_files.get(p) != sha}
    manifest = {}
    source_paths = {}
    for name, sha in changed.items():
        source = Path(name) if Path(name).is_absolute() else Path("/root") / name
        relative = (
            source.relative_to("/root")
            if source.is_relative_to("/root")
            else Path("__container__") / str(source).lstrip("/")
        )
        destination = paths.artifacts_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        await environment.download_file(str(source), destination)
        manifest[str(relative)] = sha
        source_paths[str(relative)] = str(source)
    save(paths.artifacts_dir / "manifest.json", manifest)
    save(paths.artifacts_dir / "container-paths.json", source_paths)


async def task_run(args, source, family, stage, skills, store):
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.verifier import Verifier
    from openspace.runtime import ExecutionRequest
    from openspace.telemetry.call_source import get_call_source

    task = Task(source)
    trial = args.output / stage / task.name
    if trial.exists() and args.resume:
        if (trial / "execution.json").exists() or any(
            (trial / "session-storage").glob("projects/*/sessions/*/transcript.jsonl")
        ):
            raise RuntimeError("Refusing to repeat a task with a recorded execution")
        attempt = 1
        archived = trial.with_name(f"{task.name}.startup-failed-{attempt}")
        while archived.exists():
            attempt += 1
            archived = trial.with_name(f"{task.name}.startup-failed-{attempt}")
        trial.rename(archived)
    trial.mkdir(parents=True, exist_ok=False)
    paths = TrialPaths(trial)
    paths.mkdir()
    workspace = trial / "workspace"
    workspace.mkdir()
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:16]
    session_id = f"sf-{stage}-{digest}-{os.getpid()}"
    environment = DockerEnvironment(
        environment_dir=task.paths.environment_dir,
        environment_name=f"sf-{digest}",
        session_id=session_id,
        trial_paths=paths,
        task_env_config=task.config.environment,
        logger=logging.getLogger("skillflow"),
    )
    save(
        trial / "config.json",
        {
            "task_dir": str(source),
            "task_checksum": task.checksum,
            "task_config": task.config.model_dump(mode="json"),
            "method": "native OpenSpace",
            "model": args.model,
            "phase": stage,
            "family": family,
            "environment_session": session_id,
            "force_build": True,
            "verifier": "harbor.verifier.verifier:Verifier",
        },
    )
    record = {
        "family": family,
        "stage": stage,
        "task_id": task.name,
        "passed": False,
        "reward": None,
        "infrastructure_error": None,
        "reasoning_effort": getattr(args, "reasoning_effort", "auto"),
    }
    started = time.monotonic()
    before = skills_hash(skills)
    app, usages, outcomes = None, [], []
    result = None
    input_files = None
    artifacts_collected = False
    args.progress.task(f"{family} / {stage}", task.name, "构建任务容器")
    print(f"[{family}][{stage}] {task.name} START", flush=True)
    try:
        await asyncio.wait_for(
            environment.start(force_build=True),
            timeout=task.config.environment.build_timeout_sec + 60,
        )
        container = await environment._run_docker_compose_command(["ps", "-q", "main"])
        container_id = (container.stdout or "").strip()
        if not container_id or len(container_id.splitlines()) != 1:
            raise RuntimeError("Harbor did not identify exactly one task container")
        location = await environment.exec("pwd", timeout_sec=30)
        workdir = location.stdout.strip() if isinstance(location.stdout, str) else "/root"
        if not Path(workdir).is_absolute() or "\n" in workdir:
            raise RuntimeError(f"Invalid task working directory: {workdir!r}")
        environment._skillflow_workdir = workdir
        save(
            trial / "environment.json",
            {"container_id": container_id, "session_id": session_id, "workdir": workdir},
        )
        input_files = await inventory(environment)
        await environment.exec("mkdir -p " + shlex.quote(str(skills)))
        await environment.upload_dir(skills, str(skills))
        # The runtime remains on the host; only its task tools execute in Docker.
        servers = {
            "skillflow-container": {
                "command": "python3",
                "env": {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]},
                "args": [
                    str(Path(__file__).with_name("container_tools_mcp.py")),
                    "--container",
                    container_id,
                    "--workdir",
                    workdir,
                ],
            }
        }
        app = await make_app(
            args,
            trial,
            skills,
            store,
            train=stage == "evolved",
            domain_name="skillflow",
            workspace=workspace,
            mcp_servers=servers,
        )
        app.get_llm_client().set_usage_callback(
            lambda model, usage: usages.append(
                {"model": model, "source": get_call_source(), **serial(usage)}
            )
        )
        prompt = PREAMBLE.format(workdir=workdir) + task.instruction
        (trial / "prompt.md").write_text(prompt)
        args.progress.activity("执行")
        execution_start = time.monotonic()
        try:
            result = await execute_with_budget(
                app,
                ExecutionRequest(
                    prompt=prompt,
                    task_id=f"skillflow:{family}:{stage}:{task.name}",
                    context={"permission_mode": "bypassPermissions", "memory_mode": "off"},
                    workspace_dir=workspace,
                    capture_skill_dir=str(skills),
                    max_iterations=args.max_iterations,
                ),
                task.config.agent.timeout_sec,
            )
        except AgentBudgetExceeded as exc:
            record.update(agent_status="timeout", agent_error=str(exc), task_timeout=True)
            save(
                trial / "agent-timeout.json",
                {
                    "status": "timeout",
                    "budget_sec": task.config.agent.timeout_sec,
                    "error": str(exc),
                    "transcripts_retained": True,
                },
            )
            print(
                f"[{family}][{stage}] {task.name} TIMEOUT; verifying partial artifacts", flush=True
            )
        record["execution_elapsed_sec"] = round(time.monotonic() - execution_start, 2)
        if result is not None:
            save(trial / "execution.json", serial(result))
            (trial / "response.md").write_text(result.text)
            record.update(
                agent_status=result.status,
                agent_error=result.error,
                iterations=result.iterations,
                skills_used=serial(result.skills_used),
                active_skills=serial(result.active_skills),
            )
        args.progress.activity("官方验证")
        verification_start = time.monotonic()
        verified = await asyncio.wait_for(
            Verifier(task, paths, environment).verify(),
            timeout=task.config.verifier.timeout_sec,
        )
        from benchmark.skillflow.verifier_health import check_verifier_stdout

        health = check_verifier_stdout(paths.test_stdout_path)
        save(trial / "verifier-health.json", health)
        if not health["healthy"]:
            raise RuntimeError(
                f"Official SkillFlow verifier failed internally: {health['problems']}"
            )
        rewards = verified.rewards
        if not rewards or len(rewards) != 1:
            raise RuntimeError(f"Expected exactly one official reward: {rewards}")
        reward = float(next(iter(rewards.values())))
        if not 0 <= reward <= 1:
            raise RuntimeError(f"Invalid official reward: {reward}")
        verifier = {
            "reward": reward,
            "rewards": rewards,
            "test_stdout_path": str(paths.test_stdout_path),
            "source": "official_skillflow_harbor_verifier",
        }
        record.update(
            reward=reward,
            passed=reward >= 1,
            grading_status="officially_graded",
            verification_elapsed_sec=round(time.monotonic() - verification_start, 2),
        )
        save(trial / "verifier.json", verifier)
        print(f"[{family}][{stage}] {task.name} reward={reward}", flush=True)
        # Remove only the verifier's ephemeral copy, before any replay execution.
        await environment.exec("rm -rf /tests")
        await collect_artifacts(environment, paths, input_files)
        artifacts_collected = True
        if stage == "evolved" and result is None:
            record["evolution_skipped_reason"] = (
                "Agent deadline cancelled execution before a finalized result; "
                "official grade and partial artifacts retained, no synthetic evolution evidence."
            )
        if stage == "evolved" and result is not None:
            args.progress.activity("技能演化审查")
            evolution_start = time.monotonic()
            outcomes = await add_verifier_evidence(
                app,
                {"name": task.name},
                result,
                verifier,
                trial,
                domain_name="skillflow",
                prompt=prompt,
                verifier_actor="official_skillflow_verifier",
            )
            save(trial / "evolution.json", serial(outcomes))
            record["evolution_elapsed_sec"] = round(time.monotonic() - evolution_start, 2)
        record.update(
            evolution_status=[o.status for o in outcomes],
            committed_actions=sum(len(o.actions) for o in outcomes),
            decisions=sum(len(o.decisions) for o in outcomes),
            skill_stats=app.get_skill_store().get_stats(),
        )
    except Exception as exc:
        record["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
        (trial / "error.txt").write_text(traceback.format_exc())
        print(f"[{family}][{stage}] {task.name} ERROR {exc}", flush=True)
    finally:
        if input_files is not None and not artifacts_collected:
            try:
                await collect_artifacts(environment, paths, input_files)
            except Exception as exc:
                record["artifact_collection_error"] = f"{type(exc).__name__}: {exc}"
        try:
            if app:
                await app.cleanup()
        finally:
            # Remove this trial's containers, retaining images for the matched run.
            await environment.stop(delete=False)
        record.update(
            elapsed_sec=round(time.monotonic() - started, 2),
            usage=usages,
            skills_before=before,
            skills_after=skills_hash(skills),
        )
        save(trial / "usage.json", usages)
        save(trial / "summary.json", record)
    if stage == "baseline" and record["skills_after"] != before:
        raise RuntimeError("Baseline modified skill files")
    return record
