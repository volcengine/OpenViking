"""Run 5 related train/test pairs with native OpenSpace and official domain graders."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from benchmark.evoagentbench.openspace_run import (
    add_verifier_evidence,
    make_app,
    official_reward,
    persisted_session_storage,
    save,
    serial,
    skills_hash,
)


async def run_task(args, domain_name, domain, task, stage, skills, store):
    from openspace.runtime import ExecutionRequest
    from openspace.telemetry.call_source import get_call_source

    trial = args.output / stage / domain_name / task["name"]
    recovered = trial.exists() and getattr(args, "resume", False)
    old_record = {}
    original_storage = None
    if recovered:
        old_record = json.loads((trial / "summary.json").read_text())
        if old_record.get("reward") is not None or not (trial / "execution.json").is_file():
            raise RuntimeError("Recovery requires an ungraded saved execution")
        result = SimpleNamespace(**json.loads((trial / "execution.json").read_text()))
        original_storage = persisted_session_storage(trial, result.session_id)
        audit = trial / "recovery-audit" / str(time.time_ns())
        audit.mkdir(parents=True)
        for filename in ("summary.json", "verifier.json", "usage.json", "error.txt"):
            if (trial / filename).exists():
                shutil.copy2(trial / filename, audit / filename)
    else:
        trial.mkdir(parents=True, exist_ok=False)
    workspace = trial / "workspace"
    workspace.mkdir(exist_ok=recovered)
    env = domain.setup(task, "openspace", 1)
    workspace = Path(env.get("workspace_dir", workspace))
    prompt = (
        json.loads((trial / "prompt.json").read_text())["prompt"]
        if recovered
        else domain.build_prompt(task, env)
    )
    save(trial / "task.json", task)
    save(trial / "prompt.json", {"prompt": prompt})
    before = skills_hash(skills)
    started = time.monotonic()
    usages = list(old_record.get("usage", []))
    outcomes = []
    app = None
    record = {
        "stage": stage,
        "domain": domain_name,
        "task_id": str(task.get("task_id", task["name"])),
        "title": task.get("title", task.get("occupation", task.get("topic", ""))),
        "pass_threshold": domain.pass_threshold() or 1.0,
        "passed": False,
        "reward": None,
        "infrastructure_error": None,
        "reasoning_effort": getattr(args, "reasoning_effort", "auto"),
    }
    if recovered:
        record.update(
            execution_elapsed_sec=old_record["execution_elapsed_sec"],
            recovered_execution=True,
            original_elapsed_sec=old_record["elapsed_sec"],
            previous_infrastructure_error=old_record.get("infrastructure_error"),
            execution_reasoning_effort=old_record.get("reasoning_effort", "auto"),
        )
    print(f"[{stage}][{domain_name}] {task['name']} START", flush=True)
    progress = getattr(args, "progress", None)
    try:
        app = await make_app(
            args,
            trial,
            skills,
            store,
            train=stage == "train",
            domain_name=domain_name,
            workspace=workspace,
        )
        app.get_llm_client().set_usage_callback(
            lambda model, usage: usages.append(
                {"model": model, "source": get_call_source(), **serial(usage)}
            )
        )
        if not recovered:
            execution_started = time.monotonic()
            result = await app.execute(
                ExecutionRequest(
                    prompt=prompt,
                    task_id=f"{stage}:{domain_name}:{task['name']}",
                    context={"permission_mode": "bypassPermissions", "memory_mode": "off"},
                    workspace_dir=workspace,
                    capture_skill_dir=str(skills),
                    max_iterations=args.max_iterations,
                )
            )
            record["execution_elapsed_sec"] = round(time.monotonic() - execution_started, 2)
            save(trial / "execution.json", serial(result))
            (trial / "response.md").write_text(result.text)
        record.update(
            agent_status=result.status,
            agent_error=result.error,
            iterations=result.iterations,
            tool_executions=len(result.tool_executions),
            skills_used=serial(result.skills_used),
            active_skills=serial(result.active_skills),
        )
        verification_started = time.monotonic()
        if progress:
            progress.activity("官方验证")
        verifier = await asyncio.to_thread(
            domain.verify, task, env, trial, {"response": result.text}
        )
        record["verification_elapsed_sec"] = round(time.monotonic() - verification_started, 2)
        save(trial / "verifier.json", verifier)
        reward = official_reward(verifier, domain_name)
        record.update(
            reward=reward,
            passed=reward >= record["pass_threshold"],
            verifier=verifier,
            grading_status=(
                "invalid_submission"
                if verifier.get("error") == "no_code_extracted"
                else "officially_graded"
            ),
        )
        print(
            f"[{stage}][{domain_name}] {task['name']} reward={reward} REVIEW",
            flush=True,
        )
        if stage == "train":
            evolution_started = time.monotonic()
            if progress:
                progress.activity("技能演化审查")
            outcomes = await add_verifier_evidence(
                app,
                task,
                result,
                verifier,
                trial,
                domain_name=domain_name,
                prompt=prompt,
                pass_threshold=record["pass_threshold"],
                session_storage=original_storage,
            )
            save(trial / "evolution.json", serial(outcomes))
            record["evolution_elapsed_sec"] = round(time.monotonic() - evolution_started, 2)
        record.update(
            evolution_status=[o.status for o in outcomes],
            decisions=sum(len(o.decisions) for o in outcomes),
            committed_actions=sum(len(o.actions) for o in outcomes),
            candidates=sum(len(o.candidates) for o in outcomes),
            skill_stats=app.get_skill_store().get_stats(),
        )
    except Exception as exc:
        record["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
        (trial / "error.txt").write_text(traceback.format_exc())
        print(f"[{stage}][{domain_name}] {task['name']} ERROR {exc}", flush=True)
    finally:
        if app:
            await app.cleanup()
        domain.cleanup(task, env)
        after = skills_hash(skills)
        record.update(
            elapsed_sec=round(time.monotonic() - started + old_record.get("elapsed_sec", 0), 2),
            usage=usages,
            skills_before=before,
            skills_after=after,
        )
        save(trial / "usage.json", usages)
        save(trial / "summary.json", record)
        if stage == "test" and after != before:
            raise RuntimeError("Test modified frozen skill files")
    print(
        f"[{stage}][{domain_name}] {task['name']} DONE passed={record['passed']} "
        f"commits={record.get('committed_actions', 0)} elapsed={record['elapsed_sec']}s",
        flush=True,
    )
    return record
