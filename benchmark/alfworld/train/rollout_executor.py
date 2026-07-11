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
from typing import Any, Literal
from uuid import uuid4

from benchmark.alfworld.train.case_loader import get_task_type, normalize_alfworld_split
from openviking.message import Message, TextPart, ToolPart
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
_ALFWORLD_ENV_LOCK = threading.RLock()

AlfworldRolloutBackend = Literal["direct", "vikingbot"]
AlfworldExperienceLoaderMode = Literal["skill", "constraint", "direct_experience"]
DEFAULT_ALFWORLD_ROLLOUT_BACKEND: AlfworldRolloutBackend = "vikingbot"
DEFAULT_ALFWORLD_EXPERIENCE_LOADER_MODE: AlfworldExperienceLoaderMode = "skill"


def normalize_alfworld_rollout_backend(value: Any) -> AlfworldRolloutBackend:
    backend = str(value or DEFAULT_ALFWORLD_ROLLOUT_BACKEND).strip().lower()
    if backend not in {"direct", "vikingbot"}:
        raise ValueError("ALFWorld rollout backend must be 'direct' or 'vikingbot'")
    return backend  # type: ignore[return-value]


def normalize_alfworld_experience_loader_mode(value: Any) -> AlfworldExperienceLoaderMode:
    mode = str(value or DEFAULT_ALFWORLD_EXPERIENCE_LOADER_MODE).strip().lower()
    if mode not in {"skill", "constraint", "direct_experience"}:
        raise ValueError(
            "ALFWorld loader_mode must be 'skill', 'constraint', or 'direct_experience'"
        )
    return mode  # type: ignore[return-value]


def make_alfworld_rollout_executor(
    *,
    backend: Any = DEFAULT_ALFWORLD_ROLLOUT_BACKEND,
    options: dict[str, Any] | None = None,
) -> Any:
    opts = dict(options or {})
    selected = normalize_alfworld_rollout_backend(backend)
    common = {
        "max_steps": int(opts.get("max_steps") or opts.get("max_iterations") or 50),
        "seed": int(opts.get("seed") or 42),
        "eval_dataset": _optional_str(opts.get("eval_dataset")),
        "is_train": _optional_bool(opts.get("is_train")),
        "concurrency": int(opts.get("env_concurrency") or 1),
        "show_progress": _bool_option(opts.get("show_progress"), default=False),
        "progress_label": str(opts.get("progress_label") or "alfworld"),
    }
    if selected == "vikingbot":
        return VikingBotAlfworldRolloutExecutor(
            **common,
            config_path=_optional_str(opts.get("config_path")),
            keep_default_tools=_bool_option(opts.get("keep_default_tools"), default=False),
            loader_mode=normalize_alfworld_experience_loader_mode(opts.get("loader_mode")),
            direct_experience_content=_optional_str(opts.get("direct_experience_content")),
            direct_experience_name=_optional_str(opts.get("direct_experience_name")),
            direct_experience_uri=_optional_str(opts.get("direct_experience_uri")),
        )
    return AlfworldRolloutExecutor(
        **common,
        max_api_workers=int(opts.get("max_api_workers") or 8),
        max_completion_tokens=int(opts.get("max_completion_tokens") or 16384),
    )


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


@dataclass(slots=True)
class VikingBotAlfworldRolloutExecutor:
    """Execute ALFWorld rollouts through VikingBot with ALFWorld tools.

    This mirrors tau2's VikingBot backend shape: the environment is exposed as
    tools, while optional ``experience_loader`` tools let the agent search/read
    OpenViking case-linked experiences at runtime.
    """

    max_steps: int = 50
    seed: int = 42
    eval_dataset: str | None = None
    is_train: bool | None = None
    concurrency: int = 1
    show_progress: bool = False
    progress_label: str = "alfworld"
    config_path: str | None = None
    keep_default_tools: bool = False
    loader_mode: AlfworldExperienceLoaderMode = DEFAULT_ALFWORLD_EXPERIENCE_LOADER_MODE
    direct_experience_content: str | None = None
    direct_experience_name: str | None = None
    direct_experience_uri: str | None = None

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be > 0")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        self.loader_mode = normalize_alfworld_experience_loader_mode(self.loader_mode)
        if (
            self.loader_mode == "direct_experience"
            and not str(self.direct_experience_content or "").strip()
        ):
            raise ValueError(
                "direct_experience_content is required when loader_mode='direct_experience'"
            )

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        del policy_set
        case_list = list(cases)
        if not case_list:
            return []
        progress = ProgressPrinter(
            total=len(case_list),
            label=_progress_stage_label(context.metadata.get("stage"), default=self.progress_label),
            enabled=self.show_progress,
            description=(
                f"Running {len(case_list)} ALFWorld VikingBot rollouts, "
                f"concurrency={self.concurrency}"
            ),
        )
        progress.render()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(index: int, case: Case) -> Rollout:
            async with semaphore:
                progress.start_one()
                try:
                    rollout = await self._execute_one(case, context, index)
                    progress.complete_one()
                    return rollout
                except Exception:
                    progress.fail_one()
                    raise

        try:
            return list(
                await asyncio.gather(*(run_one(i, case) for i, case in enumerate(case_list)))
            )
        finally:
            progress.finish()

    async def _execute_one(self, case: Case, context: ExecutionContext, case_index: int) -> Rollout:
        started_at = time.perf_counter()
        eval_dataset = str(
            self.eval_dataset
            or case.input.get("eval_dataset")
            or normalize_alfworld_split(case.input.get("split", "test"))
        )
        is_train = self.is_train if self.is_train is not None else eval_dataset == "train"
        gamefile = str(case.input.get("gamefile") or "")
        env_manager = build_alfworld_env(
            env_num=1,
            eval_dataset=eval_dataset,
            seed=self.seed + case_index,
            is_train=bool(is_train),
            specific_gamefiles=[gamefile] if gamefile else None,
        )
        controller = _AlfworldToolController(
            env_manager=env_manager,
            case=case,
            max_steps=self.max_steps,
        )
        await controller.reset()

        agent = await asyncio.to_thread(
            _build_vikingbot_agent,
            self.config_path,
            max_iterations=self.max_steps + 8,
        )
        _configure_alfworld_vikingbot_tools(
            agent,
            controller,
            keep_default_tools=self.keep_default_tools,
            loader_mode=self.loader_mode,
        )
        system_prompt = _build_alfworld_vikingbot_system_prompt(loader_mode=self.loader_mode)
        user_prompt = controller.initial_prompt()
        SessionKey = _tau2_vikingbot_imports()["SessionKey"]
        stage = _safe_session_fragment(str(context.metadata.get("stage") or "rollout"))
        session_key = SessionKey(
            type="cli",
            channel_id="alfworld",
            chat_id=f"alfworld_{stage}_{_safe_session_fragment(case.name)}",
        )
        (
            final_content,
            final_reasoning_content,
            tools_used,
            token_usage,
            iteration,
            memory_content,
            experience_reminder,
            experience_loader_skill,
            runtime_messages,
        ) = await _run_alfworld_vikingbot_agent(
            agent=agent,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_key=session_key,
            loader_mode=self.loader_mode,
            direct_experience_content=self.direct_experience_content,
            direct_experience_name=self.direct_experience_name,
            direct_experience_uri=self.direct_experience_uri,
        )
        won = bool(controller.won)
        fail_reason = "" if won else controller.fail_reason()
        messages = _build_vikingbot_rollout_messages(
            runtime_messages,
            final_content=final_content,
        )
        metadata = {
            "rollout_backend": "alfworld_vikingbot",
            "task_type": case.input.get("task_type") or get_task_type(gamefile),
            "gamefile": gamefile,
            "task_description": controller.task_goal,
            "hard": 1 if won else 0,
            "soft": 1.0 if won else 0.0,
            "n_turns": len(controller.history),
            "fail_reason": fail_reason,
            "agent_ok": True,
            "conversation": list(controller.history),
            "tools_used": tools_used,
            "token_usage": token_usage,
            "iterations": iteration,
            "memory": memory_content,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "final_content": final_content,
            "final_reasoning_content": final_reasoning_content,
            "experience_loader_mode": self.loader_mode,
            "experience_loader_skill": experience_loader_skill,
            "experience_reminder": experience_reminder,
            "direct_experience": _direct_experience_metadata(
                content=self.direct_experience_content,
                name=self.direct_experience_name,
                uri=self.direct_experience_uri,
                enabled=self.loader_mode == "direct_experience",
            ),
            "execution_metadata": dict(context.metadata),
            "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
        }
        return Rollout(
            case=case,
            messages=messages,
            policy_snapshot_id=context.policy_snapshot_id,
            evaluation=_alfworld_evaluation(won=won, fail_reason=fail_reason),
            metadata=metadata,
        )


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

    with _ALFWORLD_ENV_LOCK:
        config = _load_alfworld_config()
        config["general"]["random_seed"] = seed
        config["general"]["use_cuda"] = False
        config["env"]["type"] = "AlfredTWEnv"
        config["env"]["task_types"] = _task_type_ids_from_gamefiles(specific_gamefiles)
        train_eval = "train" if is_train else normalize_alfworld_split(eval_dataset)
        resolved_gamefiles = _resolve_alfworld_gamefiles(specific_gamefiles)
        if resolved_gamefiles:
            _validate_alfworld_gamefiles(resolved_gamefiles)
            env = _instantiate_alfworld_env_with_gamefiles(
                get_environment("AlfredTWEnv"),
                config,
                train_eval=train_eval,
                gamefiles=resolved_gamefiles,
            )
        else:
            env = get_environment("AlfredTWEnv")(config, train_eval=train_eval)
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


def _instantiate_alfworld_env_with_gamefiles(
    env_cls: Any,
    config: dict[str, Any],
    *,
    train_eval: str,
    gamefiles: list[str],
) -> Any:
    """Instantiate AlfredTWEnv without scanning the whole split.

    Official ALFWorld's ``AlfredTWEnv.__init__`` always walks the complete
    train/eval split and loads every ``traj_data.json`` before callers can
    override ``env.game_files``. OpenViking cases already resolve to explicit
    ``game.tw-pddl`` files, so scanning the whole split is unnecessary and can
    make a good case fail because of an unrelated partial/corrupt dataset file.
    """

    class ExplicitGamefileAlfredTWEnv(env_cls):  # type: ignore[misc, valid-type]
        def collect_game_files(self, verbose: bool = False) -> None:  # noqa: ARG002
            self.game_files = list(gamefiles)
            self.num_games = len(self.game_files)
            mode = "Training" if self.train_eval == "train" else "Evaluating"
            print(
                f"{mode} with {len(self.game_files)} explicit ALFWorld "
                f"game{'s' if len(self.game_files) != 1 else ''}"
            )

    return ExplicitGamefileAlfredTWEnv(config, train_eval=train_eval)


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
    with _ALFWORLD_ENV_LOCK:
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

        with _ALFWORLD_ENV_LOCK:
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


class _AlfworldToolController:
    def __init__(self, *, env_manager: Any, case: Case, max_steps: int) -> None:
        self.env_manager = env_manager
        self.case = case
        self.max_steps = max_steps
        self.obs = ""
        self.infos: dict[str, Any] = {}
        self.step_count = 0
        self.done = False
        self.won = False
        self.task_goal = ""
        self.history: list[dict[str, Any]] = []

    async def reset(self) -> None:
        with _ALFWORLD_ENV_LOCK:
            obs, infos = self.env_manager.reset()
        self.obs = str(obs[0] if obs else "")
        self.infos = infos
        self.task_goal = _extract_alfworld_task_goal(self.obs)
        self.step_count = 0
        self.done = False
        self.won = bool(_info_value(infos, "won", 0, default=False))

    def initial_prompt(self) -> str:
        return "\n".join(
            [
                "You are controlling one ALFWorld text-game episode.",
                "Use `alfworld_step` with exactly one command from the current admissible_commands.",
                "After each `alfworld_step` result, choose the next admissible command.",
                "Call `done` only after the environment reports won=true/done=true, or when no useful action remains.",
                "",
                self._state_json(),
            ]
        )

    async def step(self, action: str) -> str:
        action = str(action or "").strip()
        if self.done:
            return self._state_json(error="episode already done")
        admissible = self.admissible_commands()
        if admissible and action not in admissible:
            return self._state_json(
                error=(
                    f"invalid action {action!r}; choose exactly one command from "
                    "admissible_commands"
                )
            )
        with _ALFWORLD_ENV_LOCK:
            obs, rewards, dones, infos = self.env_manager.step([action])
        observation = str(obs[0] if obs else "")
        reward = float(rewards[0]) if rewards else 0.0
        done = bool(dones[0]) if dones else False
        won = bool(_info_value(infos, "won", 0, default=False))
        self.step_count += 1
        self.obs = observation
        self.infos = infos
        self.done = done or self.step_count >= self.max_steps
        self.won = won
        record = {
            "step": self.step_count - 1,
            "action": action,
            "env_feedback": observation,
            "reward": reward,
            "done": self.done,
            "won": won,
        }
        self.history.append(record)
        return self._state_json(last_action=action, reward=reward)

    def finish(self, reason: str = "") -> str:
        return self._state_json(done_requested=True, reason=reason)

    def admissible_commands(self) -> list[str]:
        value = _info_value(self.infos, "admissible_commands", 0, default=[]) or []
        return [str(item) for item in value]

    def fail_reason(self) -> str:
        if self.won:
            return ""
        if self.step_count >= self.max_steps:
            return f"Timeout after {self.max_steps} steps"
        if self.done:
            return "Episode ended without completing the task"
        return "Agent stopped without completing the task"

    def _state_json(self, **extra: Any) -> str:
        payload = {
            "task_goal": self.task_goal,
            "observation": self.obs,
            "admissible_commands": self.admissible_commands(),
            "step": self.step_count,
            "max_steps": self.max_steps,
            "done": self.done,
            "won": self.won,
            **extra,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_alfworld_task_goal(observation: str) -> str:
    match = re.search(r"(?im)^Your task is to:\s*(.+?)\s*$", observation or "")
    return match.group(1).strip() if match else ""


def _build_alfworld_vikingbot_system_prompt(
    *,
    loader_mode: AlfworldExperienceLoaderMode,
) -> str:
    parts = [
        "You are an expert ALFWorld text-game agent.",
        "Your only environment-changing action is `alfworld_step(action)`.",
        "Always choose an action that appears in the latest admissible_commands.",
        "Keep the original task_goal in mind across all steps; do not optimize only for the latest observation.",
        "For look_at_obj_in_light tasks, explicitly reason about both the target object and the light source.",
    ]
    if loader_mode == "skill":
        parts.append(
            "Before taking ALFWorld actions, use the required `experience_loader` skill: "
            "call `search_experience` with the task goal/task type, gate candidate experiences, "
            "and call `read_experience` for applicable experience URIs."
        )
    elif loader_mode == "direct_experience":
        parts.append(
            "A direct experimental experience may be injected before the task. Use it only if it "
            "matches the current ALFWorld task and observation."
        )
    else:
        parts.append("Use any injected experience reminders only when they match the current task.")
    return "\n".join(parts)


def _build_vikingbot_agent(config_path: str | None, *, max_iterations: int):
    from benchmark.tau2.train.rollout_executor_vikingbot import _build_agent

    return _build_agent(config_path, max_iterations=max_iterations)


def _tau2_vikingbot_imports() -> dict[str, Any]:
    from benchmark.tau2.train.rollout_executor_vikingbot import _vikingbot_imports

    return _vikingbot_imports()


def _configure_alfworld_vikingbot_tools(
    agent: Any,
    controller: _AlfworldToolController,
    *,
    keep_default_tools: bool,
    loader_mode: AlfworldExperienceLoaderMode,
) -> None:
    for tool_name in list(agent.tools.tool_names):
        if keep_default_tools and not str(tool_name).startswith("openviking_"):
            continue
        if loader_mode == "skill" and tool_name == "read_file":
            continue
        agent.tools.unregister(tool_name)
    if loader_mode == "skill":
        from benchmark.tau2.train.rollout_executor_vikingbot import (
            _make_read_experience_tool,
            _make_search_experience_tool,
        )

        agent.tools.register(_make_search_experience_tool())
        agent.tools.register(_make_read_experience_tool())
    agent.tools.register(_make_alfworld_step_tool(controller))
    agent.tools.register(_make_alfworld_done_tool(controller))


def _make_alfworld_step_tool(controller: _AlfworldToolController):
    Tool = _tau2_vikingbot_imports()["Tool"]

    class AlfworldStepTool(Tool):
        @property
        def name(self) -> str:
            return "alfworld_step"

        @property
        def description(self) -> str:
            return "Execute one exact ALFWorld admissible command and return the next state."

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Exact command from the latest admissible_commands list.",
                    }
                },
                "required": ["action"],
            }

        async def execute(self, tool_context: Any, action: str, **kwargs: Any) -> str:
            del tool_context, kwargs
            return await controller.step(action)

    return AlfworldStepTool()


def _make_alfworld_done_tool(controller: _AlfworldToolController):
    Tool = _tau2_vikingbot_imports()["Tool"]

    class AlfworldDoneTool(Tool):
        @property
        def name(self) -> str:
            return "done"

        @property
        def description(self) -> str:
            return "Stop the rollout after ALFWorld is complete or no useful action remains."

        @property
        def parameters(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "Short stop reason."}},
            }

        async def execute(self, tool_context: Any, reason: str = "", **kwargs: Any) -> str:
            del tool_context, kwargs
            return controller.finish(reason=reason)

    return AlfworldDoneTool()


async def _run_alfworld_vikingbot_agent(
    *,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    session_key: Any,
    loader_mode: AlfworldExperienceLoaderMode,
    direct_experience_content: str | None = None,
    direct_experience_name: str | None = None,
    direct_experience_uri: str | None = None,
) -> tuple[
    Any,
    Any,
    list[dict[str, Any]],
    Any,
    Any,
    str | None,
    str | None,
    str | None,
    list[dict[str, Any]],
]:
    from benchmark.tau2.train.rollout_executor_vikingbot import (
        _build_direct_experience_reminder,
        _case_memory_context_from_tools,
        _execute_required_experience_loader_read,
        _extract_experience_content,
        _extract_memory_content,
        _insert_experience_reminder_message,
        _merge_memories,
        _prepare_experience_loader_skill,
    )

    loader_mode = normalize_alfworld_experience_loader_mode(loader_mode)
    message_context = agent.context
    experience_loader_skill = None
    if loader_mode == "skill":
        message_context = await _prepare_experience_loader_skill(
            agent=agent,
            session_key=session_key,
            system_prompt_profile="minimal",
        )
        experience_loader_skill = getattr(
            message_context,
            "latest_experience_loader_skill_content",
            None,
        )
    messages = await message_context.build_messages(
        history=[],
        current_message=user_prompt,
        session_key=session_key,
        ov_tools_enable=False,
        experience_recall_enable=False,
        media=None,
        profile_user_list=[],
        system_prompt_profile="minimal",
    )
    if system_prompt:
        messages.insert(1, {"role": "system", "content": system_prompt})
    direct_experience_reminder = None
    if loader_mode == "direct_experience":
        direct_experience_reminder = _build_direct_experience_reminder(
            content=direct_experience_content,
            name=direct_experience_name,
            uri=direct_experience_uri,
        )
        _insert_experience_reminder_message(messages, direct_experience_reminder)

    user_memory = None
    experience_reminder_text = None
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if not isinstance(content, str):
            continue
        if "[Experience Reminder]" in content and "## Relevant Agent Experience" in content:
            experience_reminder_text = content
            continue
        if content.startswith("## Current Session"):
            user_memory = _extract_memory_content(content)
    exp_content = (
        _extract_experience_content(experience_reminder_text) if experience_reminder_text else None
    )
    memory_content = _merge_memories(user_memory, exp_content)
    required_skill_tool = None
    if experience_loader_skill and str(experience_loader_skill).strip():
        required_skill_tool = await _execute_required_experience_loader_read(
            agent=agent,
            messages=messages,
            session_key=session_key,
            sender_id="alfworld_user",
        )
    result = await agent._run_agent_loop(
        messages=messages,
        session_key=session_key,
        publish_events=False,
        sender_id="alfworld_user",
        ov_tools_enable=False,
        stop_tool_names=["done"],
    )
    final_content, final_reasoning_content, tools_used, token_usage, iteration = result
    runtime_messages = list(getattr(result, "messages", []) or [])
    if required_skill_tool is not None:
        tools_used = [required_skill_tool, *tools_used]
    case_memory_context = _case_memory_context_from_tools(tools_used)
    memory_content = _merge_memories(memory_content, case_memory_context)
    return (
        final_content,
        final_reasoning_content,
        tools_used,
        token_usage,
        iteration,
        memory_content,
        experience_reminder_text or direct_experience_reminder,
        experience_loader_skill,
        runtime_messages,
    )


def _build_vikingbot_rollout_messages(
    runtime_messages: list[dict[str, Any]],
    *,
    final_content: str | None,
) -> list[Message]:
    messages: list[Message] = []
    tool_inputs_by_id: dict[str, dict[str, Any]] = {}
    for raw in runtime_messages or []:
        if not isinstance(raw, dict):
            continue
        for call in raw.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if call_id:
                tool_inputs_by_id[call_id] = _as_tool_input(function.get("arguments", {}))
    for idx, raw in enumerate(runtime_messages or []):
        msg = _runtime_message_to_rollout_message(raw, idx=idx, tool_inputs_by_id=tool_inputs_by_id)
        if msg is not None:
            messages.append(msg)
    if final_content and str(final_content).strip():
        final_text = str(final_content)
        if not any(
            message.role == "assistant" and message.content == final_text for message in messages
        ):
            messages.append(
                Message(
                    id="alfworld-vikingbot-final",
                    role="assistant",
                    parts=[TextPart(text=final_text)],
                )
            )
    return messages


def _runtime_message_to_rollout_message(
    raw: dict[str, Any],
    *,
    idx: int,
    tool_inputs_by_id: dict[str, dict[str, Any]],
) -> Message | None:
    role = str(raw.get("role") or "user")
    content = raw.get("content")
    if role == "system":
        return Message(
            id=f"alfworld-runtime-{idx}",
            role="user",
            parts=[TextPart(text=f"system:\n{_runtime_content_to_text(content)}")],
        )
    if role == "tool":
        tool_call_id = str(raw.get("tool_call_id") or f"alfworld-runtime-tool-{idx}")
        tool_name = str(raw.get("name") or raw.get("tool_name") or "unknown")
        return Message(
            id=f"alfworld-runtime-{idx}",
            role="user",
            parts=[
                ToolPart(
                    tool_id=tool_call_id,
                    tool_name=tool_name,
                    tool_input=tool_inputs_by_id.get(tool_call_id, {}),
                    tool_output=_runtime_content_to_text(content),
                    tool_status="completed",
                )
            ],
        )
    if role not in {"user", "assistant"}:
        role = "user"
    if raw.get("tool_calls") and not str(content or "").strip():
        return None
    text = _runtime_content_to_text(content)
    if not text.strip():
        return None
    return Message(
        id=f"alfworld-runtime-{idx}",
        role=role,  # type: ignore[arg-type]
        parts=[TextPart(text=text)],
    )


def _runtime_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _as_tool_input(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"arguments": args}
        if isinstance(parsed, dict):
            return parsed
        return {"arguments": parsed}
    return {"arguments": args}


def _direct_experience_metadata(
    *,
    content: str | None,
    name: str | None,
    uri: str | None,
    enabled: bool,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    from hashlib import sha256

    content_text = str(content or "")
    exp_name = str(name or "").strip() or "direct_experience"
    return {
        "name": exp_name,
        "uri": str(uri or "").strip() or f"direct://experience/{exp_name}",
        "content_chars": len(content_text),
        "content_sha256": sha256(content_text.encode("utf-8")).hexdigest(),
    }


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


def _validate_alfworld_gamefiles(gamefiles: list[str]) -> None:
    for gamefile in gamefiles:
        path = Path(gamefile)
        if not path.exists():
            raise FileNotFoundError(f"ALFWorld gamefile does not exist: {path}")
        try:
            with path.open("r", encoding="utf-8") as fh:
                gamedata = json.load(fh)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid ALFWorld gamefile JSON: {path}: {exc}") from exc
        if not isinstance(gamedata, dict):
            raise RuntimeError(f"Invalid ALFWorld gamefile JSON object: {path}")
        if gamedata.get("solvable") is False:
            raise RuntimeError(f"ALFWorld gamefile is marked unsolvable: {path}")


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


def _bool_option(value: Any, *, default: bool) -> bool:
    parsed = _optional_bool(value)
    return default if parsed is None else parsed


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean option: {value!r}")
    return bool(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _safe_session_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:80] or "rollout"
