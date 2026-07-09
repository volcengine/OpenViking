#!/usr/bin/env python3
"""ALFWorld RolloutExecutor implementation for OpenViking batch training."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from benchmark.alfworld.train.case_loader import get_task_type, normalize_alfworld_split
from openviking.message import Message, TextPart
from openviking.session.train import (
    Case,
    CriterionResult,
    ExecutionContext,
    ExperienceSet,
    Rollout,
    RubricEvaluation,
)
from openviking.session.train.components.progress import ProgressPrinter
from openviking_cli.utils.config import get_openviking_config

ALFWORLD_SYSTEM_PROMPT = "You are an expert agent operating in the ALFRED Embodied Environment."
_TEXTWORLD_COMPAT_PATCHED = False
_TEXTWORLD_PARSE_LOCK = threading.RLock()


@dataclass(slots=True)
class AlfworldRolloutExecutor:
    """Execute ALFWorld text-game rollouts through the official ALFWorld package."""

    max_steps: int = 50
    max_api_workers: int = 8
    temperature: float = 0.4
    max_completion_tokens: int = 16384
    seed: int = 42
    eval_dataset: str | None = None
    is_train: bool | None = None
    concurrency: int = 1
    show_progress: bool = False
    progress_label: str = "alfworld"

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if self.max_api_workers <= 0:
            raise ValueError("max_api_workers must be > 0")
        if self.max_completion_tokens <= 0:
            raise ValueError("max_completion_tokens must be > 0")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        case_list = list(cases)
        if not case_list:
            return []
        progress = ProgressPrinter(
            total=len(case_list),
            label=_progress_stage_label(context.metadata.get("stage"), default=self.progress_label),
            enabled=self.show_progress,
            description=f"Running {len(case_list)} ALFWorld rollouts",
        )
        progress.render()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(index: int, case: Case) -> Rollout:
            async with semaphore:
                progress.start_one()
                try:
                    rollout = await self._execute_batch([case], policy_set, context, index)
                    progress.complete_one()
                    return rollout[0]
                except Exception:
                    progress.fail_one()
                    raise

        try:
            return list(
                await asyncio.gather(*(run_one(i, case) for i, case in enumerate(case_list)))
            )
        finally:
            progress.finish()

    async def _execute_batch(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
        case_index_offset: int,
    ) -> list[Rollout]:
        started_at = time.perf_counter()
        first_case = cases[0]
        eval_dataset = self.eval_dataset or str(
            first_case.input.get("eval_dataset")
            or normalize_alfworld_split(first_case.input.get("split", "test"))
        )
        is_train = self.is_train if self.is_train is not None else eval_dataset == "train"
        gamefiles = [str(case.input.get("gamefile") or "") for case in cases]
        specific_gamefiles = [item for item in gamefiles if item]
        if specific_gamefiles and len(specific_gamefiles) != len(cases):
            raise ValueError("Either all ALFWorld cases in a batch need gamefile or none do")

        env_manager = build_alfworld_env(
            env_num=len(cases),
            eval_dataset=eval_dataset,
            seed=self.seed + case_index_offset,
            is_train=bool(is_train),
            specific_gamefiles=specific_gamefiles or None,
        )
        batch_results = await run_alfworld_batch(
            env_manager=env_manager,
            cases=cases,
            policy_set=policy_set,
            context=context,
            max_steps=self.max_steps,
            max_api_workers=self.max_api_workers,
            temperature=self.temperature,
            max_completion_tokens=self.max_completion_tokens,
        )
        for rollout in batch_results:
            rollout.metadata.setdefault(
                "duration_ms", round((time.perf_counter() - started_at) * 1000.0, 2)
            )
        return batch_results


def build_alfworld_env(
    env_num: int,
    eval_dataset: str = "eval_out_of_distribution",
    seed: int = 42,
    is_train: bool = False,
    specific_gamefiles: list[str] | None = None,
):
    """Build an official ALFWorld TextWorld gym environment."""

    _patch_textworld_alfworld_compat()
    try:
        from alfworld.agents.environment import get_environment
    except ImportError as exc:  # pragma: no cover - exercised only in ALFWorld envs
        raise RuntimeError(
            "ALFWorld rollout requires the official alfworld package. "
            "Install it with: python -m pip install -e ~/workspace/alfworld"
        ) from exc

    config = _load_alfworld_config()
    config["general"]["random_seed"] = seed
    config["general"]["use_cuda"] = False
    config["env"]["type"] = "AlfredTWEnv"
    config["env"]["task_types"] = _task_type_ids_from_gamefiles(specific_gamefiles)
    train_eval = "train" if is_train else normalize_alfworld_split(eval_dataset)
    env = get_environment("AlfredTWEnv")(config, train_eval=train_eval)
    resolved_gamefiles = _resolve_alfworld_gamefiles(specific_gamefiles)
    if resolved_gamefiles:
        env.game_files = resolved_gamefiles
        env.num_games = len(resolved_gamefiles)
    if not getattr(env, "game_files", None):
        data_root = os.getenv("ALFWORLD_DATA", "").strip()
        raise RuntimeError(
            f"No ALFWorld games found for split={train_eval!r}. "
            "Set ALFWORLD_DATA to the downloaded ALFWorld data directory "
            f"(current: {data_root or '<unset>'}) or pass explicit gamefiles."
        )
    return env.init_env(batch_size=env_num)


def _patch_textworld_alfworld_compat() -> None:
    """Patch TextWorld/ALFWorld compatibility issues in this service process.

    The official ALFWorld stack currently pulls TextWorld/TatSu code that is not
    safe to initialize concurrently: both PDDL and CSG parsers keep mutable
    parser state in module-level singletons. The dataset service can execute
    rollout requests from many worker threads, so parser calls must be
    serialized or TatSu may fail with ``IndexError: pop from empty list``.

    TextWorld's CSG ``EvalSymbol.derive`` also relies on ``locals().update()``
    making grammar variables visible to ``eval()``. That is not reliable on
    modern Python and fails on ALFWorld grammar snippets such as ``{r.name}``.
    Evaluate against an explicit locals dict instead.
    """

    global _TEXTWORLD_COMPAT_PATCHED
    if _TEXTWORLD_COMPAT_PATCHED:
        return

    import textworld.envs.pddl.logic as pddl_logic
    import textworld.envs.pddl.textgen as textgen

    original_logic_parse = pddl_logic._parse_and_convert
    original_textgen_parse = textgen._parse_and_convert
    original_eval_derive = textgen.EvalSymbol.derive

    def locked_logic_parse(*args: Any, **kwargs: Any) -> Any:
        with _TEXTWORLD_PARSE_LOCK:
            return original_logic_parse(*args, **kwargs)

    def locked_textgen_parse(*args: Any, **kwargs: Any) -> Any:
        with _TEXTWORLD_PARSE_LOCK:
            return original_textgen_parse(*args, **kwargs)

    def eval_symbol_derive(self: Any, context: dict[str, Any] | None = None) -> list[Any]:
        context = context or self.context
        try:
            value = eval(self.expression, {}, dict(context["variables"]))  # noqa: S307
        except Exception:
            # Preserve TextWorld's original behavior/error shape if the
            # explicit-locals path fails for an expression that depended on
            # another quirk of the old implementation.
            return original_eval_derive(self, context)
        return [textgen.TerminalSymbol(value)]

    pddl_logic._parse_and_convert = locked_logic_parse
    textgen._parse_and_convert = locked_textgen_parse
    textgen.EvalSymbol.derive = eval_symbol_derive
    _TEXTWORLD_COMPAT_PATCHED = True


async def run_alfworld_batch(
    *,
    env_manager: Any,
    cases: list[Case],
    policy_set: ExperienceSet,
    context: ExecutionContext,
    max_steps: int = 50,
    max_api_workers: int = 8,
    temperature: float = 0.4,
    max_completion_tokens: int = 16384,
) -> list[Rollout]:
    """Run an ALFWorld env batch and return OpenViking Rollout objects."""

    del temperature  # Provider-specific temperature wiring lives in configured VLMs.
    skill_prompt = _build_skill_prompt(policy_set)
    obs, infos = env_manager.reset()
    env_num = len(obs)
    if env_num != len(cases):
        raise RuntimeError(f"ALFWorld env count mismatch: env={env_num}, cases={len(cases)}")
    env_dones = [False] * env_num
    successes = [False] * env_num
    conversations: list[list[dict[str, Any]]] = [[] for _ in range(env_num)]
    messages: list[list[Message]] = [[] for _ in range(env_num)]
    env_meta = [_env_metadata(i, infos) for i in range(env_num)]
    api_semaphore = asyncio.Semaphore(max_api_workers)

    for step_idx in range(max_steps):
        if all(env_dones):
            break
        active_indices = [i for i in range(env_num) if not env_dones[i]]
        prompts = {
            i: _build_step_prompt(
                observation=str(obs[i]),
                info=infos,
                env_index=i,
                skill_prompt=skill_prompt,
            )
            for i in active_indices
        }

        async def call_model(idx: int, prompt: str) -> tuple[int, str]:
            async with api_semaphore:
                return idx, await _complete_action_prompt(
                    prompt,
                    max_completion_tokens=max_completion_tokens,
                )

        model_responses = dict(
            await asyncio.gather(*(call_model(i, prompts[i]) for i in active_indices))
        )
        step_actions = ["look"] * env_num
        for i, response in model_responses.items():
            step_actions[i] = _normalize_action_for_env(response, infos, i)
            messages[i].append(_message("user", prompts[i]))
            messages[i].append(_message("assistant", response))

        obs, rewards, dones, infos = env_manager.step(step_actions)
        for i in active_indices:
            step_record = {
                "step": step_idx,
                "action": step_actions[i],
                "reasoning": _extract_think(model_responses[i]),
                "model_response": model_responses[i],
                "env_feedback": str(obs[i]),
                "reward": float(rewards[i]),
                "done": bool(dones[i]),
            }
            conversations[i].append(step_record)
        for i in range(env_num):
            if env_dones[i]:
                continue
            if dones[i]:
                env_dones[i] = True
                successes[i] = bool(_info_value(infos, "won", i, default=False))

    rollouts: list[Rollout] = []
    for i, case in enumerate(cases):
        won = successes[i]
        n_turns = len(conversations[i])
        fail_reason = ""
        if not won:
            fail_reason = (
                f"Timeout after {max_steps} steps"
                if not env_dones[i]
                else "Episode ended without completing the task"
            )
        metadata = {
            "rollout_backend": "alfworld_official",
            "task_type": env_meta[i].get("task_type") or case.input.get("task_type"),
            "gamefile": env_meta[i].get("gamefile") or case.input.get("gamefile"),
            "task_description": env_meta[i].get("task_description", ""),
            "hard": 1 if won else 0,
            "soft": 1.0 if won else 0.0,
            "n_turns": n_turns,
            "fail_reason": fail_reason,
            "agent_ok": True,
            "conversation": conversations[i],
            "execution_metadata": dict(context.metadata),
        }
        rollouts.append(
            Rollout(
                case=case,
                messages=messages[i],
                policy_snapshot_id=context.policy_snapshot_id,
                evaluation=_alfworld_evaluation(won=won, fail_reason=fail_reason),
                metadata=metadata,
            )
        )
    return rollouts


def _build_skill_prompt(policy_set: ExperienceSet) -> str:
    contents = [policy.content.strip() for policy in policy_set.policies if policy.content.strip()]
    if not contents:
        return ""
    return (
        "## Skill Knowledge\n"
        "Below are learned strategies from the current OpenViking policy set. "
        "Use them when relevant, but obey the current ALFWorld observation and action rules.\n\n"
        + "\n\n---\n\n".join(contents)
        + "\n"
    )


def _build_step_prompt(
    *,
    observation: str,
    info: dict[str, Any],
    env_index: int,
    skill_prompt: str,
) -> str:
    admissible = _info_value(info, "admissible_commands", env_index, default=[]) or []
    commands = "\n".join(f"- {command}" for command in admissible[:80])
    parts = []
    if skill_prompt:
        parts.append(skill_prompt)
    parts.extend(
        [
            "## Current Observation",
            observation,
            "",
            "## Valid Commands",
            commands or "(not provided)",
            "",
            "Choose exactly one next ALFWorld command.",
            "Respond with <think>short reasoning</think><action>command</action>.",
        ]
    )
    return "\n".join(parts)


def _normalize_action_for_env(model_response: str, info: dict[str, Any], env_index: int) -> str:
    action = (_extract_action(model_response) or "look").strip()
    admissible = _info_value(info, "admissible_commands", env_index, default=[]) or []
    if admissible and action not in admissible:
        return "look" if "look" in admissible else str(admissible[0])
    return action or "look"


async def _complete_action_prompt(prompt: str, *, max_completion_tokens: int) -> str:
    try:
        vlm = get_openviking_config().vlm
        response = await vlm.get_completion_async(
            prompt=_prompt_with_system(prompt),
            thinking=True,
            max_completion_tokens=max_completion_tokens,
        )
        text = _response_text(response).strip()
    except TypeError:
        response = await get_openviking_config().vlm.get_completion_async(
            prompt=_prompt_with_system(prompt),
            thinking=True,
        )
        text = _response_text(response).strip()
    except Exception:
        text = ""
    if not text:
        return "<think>empty model response</think><action>look</action>"
    if _extract_action(text) is None:
        return f"<think>missing action tag; fallback to look</think><action>look</action>\n\n{text}"
    return text


def _prompt_with_system(prompt: str) -> str:
    return f"{ALFWORLD_SYSTEM_PROMPT}\n\n{prompt}"


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is not None:
        return str(content)
    return str(response or "")


def _extract_action(model_response: str) -> str | None:
    match = re.search(r"<action>(.*?)</action>", model_response, re.DOTALL)
    return match.group(1).strip() if match else None


def _extract_think(model_response: str) -> str | None:
    match = re.search(r"<think>(.*?)</think>", model_response, re.DOTALL)
    return match.group(1).strip() if match else None


def _env_metadata(index: int, infos: Any) -> dict[str, Any]:
    gamefile = str(_info_value(infos, "extra.gamefile", index, default="") or "")
    if not gamefile:
        gamefile = str(_info_value(infos, "gamefile", index, default="") or "")
    return {
        "gamefile": gamefile,
        "task_type": get_task_type(gamefile),
        "task_description": "",
    }


def _alfworld_evaluation(*, won: bool, fail_reason: str) -> RubricEvaluation:
    score = 1.0 if won else 0.0
    return RubricEvaluation(
        passed=won,
        score=score,
        criterion_results=[
            CriterionResult(
                criterion_name="alfworld_success",
                passed=won,
                score=score,
                feedback=[] if won else [fail_reason or "ALFWorld task was not completed."],
                evidence=["won=True"] if won else [fail_reason or "won=False"],
                metadata={"hard": 1 if won else 0, "soft": score},
            )
        ],
        metadata={"hard": 1 if won else 0, "soft": score},
    )


def _message(role: str, text: str) -> Message:
    if role not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")
    return Message(
        id=f"alfworld-{role}-{uuid4().hex}",
        role=role,  # type: ignore[arg-type]
        parts=[TextPart(text=text)],
    )


def _load_alfworld_config() -> dict[str, Any]:
    import yaml

    config_path = Path(_resolve_alfworld_config_path()).expanduser()
    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not isinstance(config, dict):
        raise ValueError(f"ALFWorld config must be a mapping: {config_path}")
    return _expand_alfworld_config_paths(config)


def _expand_alfworld_config_paths(config: dict[str, Any]) -> dict[str, Any]:
    data_root = os.getenv("ALFWORLD_DATA", "").strip()
    if not data_root:
        return config
    text = json.dumps(config)
    text = text.replace("$ALFWORLD_DATA", data_root)
    return json.loads(text)


def _resolve_alfworld_config_path() -> str:
    env_path = os.getenv("ALFWORLD_CONFIG_PATH")
    if env_path:
        return os.path.expanduser(os.path.expandvars(env_path))
    try:
        import alfworld
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Cannot resolve ALFWorld config path without alfworld installed"
        ) from exc
    package_dir = Path(alfworld.__file__).resolve().parent
    candidates = [
        package_dir.parent / "configs" / "base_config.yaml",
        package_dir / "configs" / "base_config.yaml",
        Path.home() / "workspace" / "alfworld" / "configs" / "base_config.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("Could not find ALFWorld configs/base_config.yaml")


def _resolve_alfworld_gamefile(gamefile: str) -> str:
    path = os.path.expanduser(os.path.expandvars(str(gamefile)))
    if os.path.isabs(path):
        return path
    data_root = os.environ.get("ALFWORLD_DATA", "").strip()
    if not data_root:
        return path
    root = os.path.expanduser(os.path.expandvars(data_root))
    return os.path.abspath(os.path.join(root, path))


def _resolve_alfworld_gamefiles(gamefiles: list[str] | None) -> list[str] | None:
    if gamefiles is None:
        return None
    return [_resolve_alfworld_gamefile(gamefile) for gamefile in gamefiles]


def _task_type_ids_from_gamefiles(gamefiles: list[str] | None) -> list[int]:
    task_id_by_name = {
        "pick_and_place": 1,
        "look_at_obj_in_light": 2,
        "pick_clean_then_place_in_recep": 3,
        "pick_heat_then_place_in_recep": 4,
        "pick_cool_then_place_in_recep": 5,
        "pick_two_obj_and_place": 6,
    }
    if not gamefiles:
        return [1, 2, 3, 4, 5, 6]
    ids = []
    for gamefile in gamefiles:
        task_type = get_task_type(gamefile)
        task_id = task_id_by_name.get(task_type)
        if task_id is not None and task_id not in ids:
            ids.append(task_id)
    return ids or [1, 2, 3, 4, 5, 6]


def _info_value(info: Any, key: str, index: int, *, default: Any = None) -> Any:
    if isinstance(info, dict):
        value = info.get(key, default)
        if isinstance(value, list | tuple):
            return value[index] if index < len(value) else default
        return value
    if isinstance(info, list | tuple):
        item = info[index] if index < len(info) else {}
        if isinstance(item, dict):
            return item.get(key, default)
    return default


def _progress_stage_label(stage: Any, *, default: str) -> str:
    stage_text = str(stage or "")
    stage_name = stage_text.split(maxsplit=1)[0]
    if stage_name.endswith("_rollout"):
        return f"{stage_name}_start"
    if stage_name.endswith("_rollout_start"):
        return stage_name
    return default
