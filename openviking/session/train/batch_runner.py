"""Generic remote benchmark batch train/eval orchestration."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from openviking.server.config import load_server_config
from openviking.server.identity import AuthMode
from openviking.session.train.components.event_recorder import (
    CompositeEventRecorder,
    JsonlEventRecorder,
    JsonlPipelineEventHook,
)
from openviking.session.train.components.remote import RemoteCaseLoader, RemoteRolloutExecutor
from openviking.session.train.components.report_builder import PipelineReportBuilder
from openviking.session.train.components.reporter import emit_run_summary
from openviking.session.train.components.rollout_artifact_recorder import (
    RolloutArtifactEventRecorder,
    RolloutArtifactRecorder,
)
from openviking.session.train.components.session_commit import SessionCommitPolicyTrainer
from openviking.session.train.components.snapshotter import ContentHashPolicySnapshotter
from openviking.session.train.context import PipelineContext
from openviking.session.train.domain import (
    ExperienceSet,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
)
from openviking.session.train.pipeline import OfflinePolicyOptimizationPipeline
from openviking.telemetry import tracer
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@dataclass(slots=True)
class BatchTrainEvalConfig:
    """Configuration for one remote benchmark batch train/eval run."""

    domain: str
    dataset: str
    epochs: int = 1
    batch_size: int | None = None
    concurrency: int = 150
    config_path: str | None = None
    output_path: str | None = None
    keep_default_tools: bool = True
    max_iterations: int = 30
    server_url: str | None = None
    api_key: str | None = None
    account_id: str = "default"
    user_id: str = "default"
    commit_keep_recent_count: int = 0
    commit_poll_interval_seconds: float = 2.0
    commit_timeout_seconds: float | None = None
    commit_concurrency: int = 100
    train_limit: int | None = None
    eval_limit: int | None = None
    benchmark_service_url: str | None = None
    baseline_force_recompute: bool = False
    eval_each_epoch: bool = False
    trials: int = 8
    clean_result: bool = True
    events_path: str | None = None
    run_timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    def __post_init__(self) -> None:
        if not self.dataset:
            raise ValueError("dataset is required")
        if not self.domain:
            raise ValueError("domain is required")
        if self.epochs < 0:
            raise ValueError("epochs must be >= 0")
        if self.batch_size is not None and self.batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be > 0")
        if self.commit_poll_interval_seconds <= 0:
            raise ValueError("commit_poll_interval_seconds must be > 0")
        if self.commit_timeout_seconds is not None and self.commit_timeout_seconds <= 0:
            raise ValueError("commit_timeout_seconds must be > 0")
        if self.commit_concurrency <= 0:
            raise ValueError("commit_concurrency must be > 0")
        if self.train_limit is not None and self.train_limit <= 0:
            raise ValueError("train_limit must be > 0")
        if self.eval_limit is not None and self.eval_limit <= 0:
            raise ValueError("eval_limit must be > 0")
        if self.trials <= 0:
            raise ValueError("trials must be > 0")
        if self.benchmark_service_url is not None and not self.benchmark_service_url.strip():
            raise ValueError("benchmark_service_url must not be empty")


@dataclass(slots=True)
class BatchTrainEvalReport:
    """Serializable report for remote benchmark batch train/eval."""

    dataset: str
    domain: str
    epochs: int
    batch_size: int | None
    concurrency: int
    commit_concurrency: int
    train_limit: int | None
    eval_limit: int | None
    policy_root_uri: str
    baseline_eval: dict[str, Any] | None
    train_epochs: list[dict[str, Any]] = field(default_factory=list)
    epoch_evals: list[dict[str, Any]] = field(default_factory=list)
    final_eval: dict[str, Any] | None = None
    accuracy_delta: float | None = None
    output_path: str | None = None
    trace_id: str | None = None
    run_id: str = ""
    server_url: str = ""
    benchmark_service_url: str | None = None
    eval_each_epoch: bool = False
    trials: int = 8
    rollouts_root: str | None = None
    rollouts_index_path: str | None = None
    latest_failed_rollout: str | None = None
    clean_result: bool = True
    events_path: str | None = None
    baseline_cache_path: str | None = None
    baseline_cache_hit: bool = False
    baseline_force_recompute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "domain": self.domain,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "concurrency": self.concurrency,
            "commit_concurrency": self.commit_concurrency,
            "train_limit": self.train_limit,
            "eval_limit": self.eval_limit,
            "policy_root_uri": self.policy_root_uri,
            "baseline_eval": self.baseline_eval,
            "train_epochs": self.train_epochs,
            "epoch_evals": self.epoch_evals,
            "final_eval": self.final_eval,
            "accuracy_delta": self.accuracy_delta,
            "output_path": self.output_path,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "server_url": self.server_url,
            "benchmark_service_url": self.benchmark_service_url,
            "eval_each_epoch": self.eval_each_epoch,
            "trials": self.trials,
            "rollouts_root": self.rollouts_root,
            "rollouts_index_path": self.rollouts_index_path,
            "latest_failed_rollout": self.latest_failed_rollout,
            "clean_result": self.clean_result,
            "events_path": self.events_path,
            "baseline_cache_path": self.baseline_cache_path,
            "baseline_cache_hit": self.baseline_cache_hit,
            "baseline_force_recompute": self.baseline_force_recompute,
        }


@tracer("train.batch_train_eval.run", ignore_result=True, ignore_args=True)
async def run_batch_train_eval(config: BatchTrainEvalConfig) -> BatchTrainEvalReport:
    """Run baseline eval, commit-based train epochs, and final eval for one dataset/domain."""

    _configure_openviking_config(config.config_path)
    _clean_result_dir(config)
    client = _build_http_client(config)
    await client.initialize()
    try:
        policy_root_uri = "viking://user/memories/experiences"
        policy_set = ExperienceSet(
            root_uri=policy_root_uri,
            policies=[],
            metadata=_policy_set_metadata(config, client),
        )
        run_dir = _run_output_dir(config)
        event_recorder = JsonlEventRecorder(
            path=_events_path(config),
            default_fields={
                "dataset": config.dataset,
                "domain": config.domain,
                "run_timestamp": config.run_timestamp,
            },
        )
        await event_recorder.record(
            "run_start",
            stage="run_start",
            epochs=config.epochs,
            concurrency=config.concurrency,
            commit_concurrency=config.commit_concurrency,
            train_limit=config.train_limit,
            eval_limit=config.eval_limit,
            trials=config.trials,
            clean_result=config.clean_result,
            baseline_force_recompute=config.baseline_force_recompute,
            baseline_cache_path=str(_baseline_cache_path(config)),
        )
        policy_trainer = SessionCommitPolicyTrainer(
            client=client,
            keep_recent_count=config.commit_keep_recent_count,
            poll_interval_seconds=config.commit_poll_interval_seconds,
            timeout_seconds=config.commit_timeout_seconds,
            commit_concurrency=config.commit_concurrency,
            show_progress=True,
            progress_label="train",
        )
        event_recorder.default_fields["run_id"] = policy_trainer.run_id
        pipeline = _build_pipeline(config, policy_trainer)
        rollout_artifact_recorder = RolloutArtifactRecorder(
            run_dir=run_dir,
            client=client,
            latest_pointer_path=_latest_rollouts_path(config),
        )
        remote_executor = getattr(pipeline, "rollout_executor", None)
        if isinstance(remote_executor, RemoteRolloutExecutor):
            remote_executor.on_rollout_complete = (
                rollout_artifact_recorder.record_rollout_completion
            )
        policy_trainer.event_recorder = CompositeEventRecorder(
            (event_recorder, RolloutArtifactEventRecorder(rollout_artifact_recorder))
        )

        baseline_eval: dict[str, Any] | None = None
        baseline_cache_hit = False
        baseline_cache_path = _baseline_cache_path(config)
        final_eval: dict[str, Any] | None = None
        report_builder = PipelineReportBuilder(trial_index_key="eval_trial")

        test_loader = _case_loader(config, split="test", limit=config.eval_limit)
        if await test_loader.split_exists():
            baseline_result, baseline_cache_hit = await _load_or_run_baseline_eval(
                config=config,
                pipeline=pipeline,
                case_loader=test_loader,
                policy_set=policy_set,
                report_builder=report_builder,
                event_recorder=event_recorder,
            )
            if baseline_result is not None:
                rollout_artifact_recorder.record_eval(
                    label="baseline_test_rollout",
                    epoch=-1,
                    analyses=baseline_result.analyses,
                )
                baseline_eval = baseline_result.metadata["report"]
            else:
                baseline_eval = _load_baseline_cache(baseline_cache_path)
                if baseline_eval is not None:
                    _print_baseline_cache_hit(baseline_eval, baseline_cache_path)

        train_loader = _case_loader(config, split="train", limit=config.train_limit)

        train_context = _pipeline_context(
            epoch=0,
            training=True,
            max_epochs=config.epochs,
            eval_each_epoch_case_loader=test_loader
            if config.eval_each_epoch and await test_loader.split_exists()
            else None,
            eval_trials=config.trials,
            trial_index_key="eval_trial",
            report_builder=report_builder,
            event_recorder=event_recorder,
        )
        # Register rollout artifact recorder as a lifecycle hook so rollouts
        # are written incrementally after each epoch/eval, instead of waiting
        # for the full run to finish.
        train_context.lifecycle_hooks = list(train_context.lifecycle_hooks) + [
            rollout_artifact_recorder
        ]
        train_result = await pipeline.train(
            case_loader=train_loader,
            policy_set=policy_set,
            context=train_context,
        )
        policy_set = train_result.apply_result.updated_policy_set
        # Note: per-epoch rollout artifacts are written incrementally via the
        # rollout_artifact_recorder lifecycle hook registered on train_context.

        if await test_loader.split_exists():
            final_result = await pipeline.eval(
                case_loader=test_loader,
                policy_set=policy_set,
                context=_pipeline_context(
                    epoch=config.epochs,
                    training=False,
                    max_epochs=1,
                    rollout_stage="final_test_rollout",
                    eval_split="test",
                    eval_trials=config.trials,
                    trial_index_key="eval_trial",
                    report_builder=report_builder,
                    event_recorder=event_recorder,
                ),
            )
            rollout_artifact_recorder.record_eval(
                label="final_test_rollout",
                epoch=config.epochs,
                analyses=final_result.analyses,
            )
            final_eval = final_result.metadata["report"]

        accuracy_delta = report_builder.accuracy_delta(baseline_eval, final_eval)
        rollout_artifact_index = rollout_artifact_recorder.finalize()
        report = BatchTrainEvalReport(
            dataset=config.dataset,
            domain=config.domain,
            epochs=config.epochs,
            batch_size=config.batch_size,
            concurrency=config.concurrency,
            commit_concurrency=config.commit_concurrency,
            train_limit=config.train_limit,
            eval_limit=config.eval_limit,
            policy_root_uri=policy_root_uri,
            baseline_eval=baseline_eval,
            train_epochs=list(train_result.metadata.get("train_reports", [])),
            epoch_evals=_epoch_eval_reports(train_result),
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=_default_output_path(config),
            trace_id=tracer.get_trace_id() or None,
            run_id=policy_trainer.run_id,
            server_url=client_url(client),
            benchmark_service_url=config.benchmark_service_url,
            eval_each_epoch=config.eval_each_epoch,
            trials=config.trials,
            rollouts_root=rollout_artifact_index.rollouts_root,
            rollouts_index_path=str(run_dir / "rollouts_index.json"),
            latest_failed_rollout=rollout_artifact_index.latest_failed_rollout,
            clean_result=config.clean_result,
            events_path=str(_events_path(config)),
            baseline_cache_path=str(baseline_cache_path),
            baseline_cache_hit=baseline_cache_hit,
            baseline_force_recompute=config.baseline_force_recompute,
        )
        _write_report(report, config)
        await event_recorder.record(
            "run_result",
            stage="run_result",
            trace_id=report.trace_id,
            output_path=report.output_path,
            rollouts_root=report.rollouts_root,
            rollouts_index_path=report.rollouts_index_path,
            latest_failed_rollout=report.latest_failed_rollout,
            accuracy_delta=report.accuracy_delta,
            baseline_cache_path=report.baseline_cache_path,
            baseline_cache_hit=report.baseline_cache_hit,
        )
        await emit_run_summary(
            train_context,
            title="batch train/eval",
            fields={
                "dataset": config.dataset,
                "domain": config.domain,
                "epochs": config.epochs,
                "trials": config.trials,
                "run_id": policy_trainer.run_id,
                "trace_id": report.trace_id,
                "baseline_cache_hit": report.baseline_cache_hit,
            },
            baseline_eval=baseline_eval,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=report.output_path,
            rollouts_root=report.rollouts_root,
            rollouts_index_path=report.rollouts_index_path,
            latest_failed_rollout=report.latest_failed_rollout,
        )
        return report
    finally:
        await client.close()


def _configure_openviking_config(config_path: str | None) -> None:
    if config_path:
        os.environ["OPENVIKING_CONFIG_FILE"] = str(Path(config_path).expanduser())
    OpenVikingConfigSingleton.reset_instance()


def _build_http_client(config: BatchTrainEvalConfig) -> AsyncHTTPClient:
    server_url = config.server_url
    api_key = config.api_key
    auth_mode: AuthMode | None = None
    if config.config_path or server_url is None or api_key is None:
        server_config = load_server_config(config.config_path)
        auth_mode = server_config.get_effective_auth_mode()
        server_url = server_url or f"http://{server_config.host}:{server_config.port}"
        api_key = api_key or server_config.root_api_key
    if auth_mode is None:
        auth_mode = AuthMode.API_KEY

    # Trusted mode uses X-API-Key as the gateway/root key and takes identity from
    # X-OpenViking-Account/User. In api_key/dev modes, user API keys already pin
    # identity and account/user assertion headers must not be sent.
    account = config.account_id if auth_mode == AuthMode.TRUSTED else None
    user = config.user_id if auth_mode == AuthMode.TRUSTED else None
    return AsyncHTTPClient(
        url=server_url,
        api_key=api_key,
        account=account,
        user=user,
        profile_enabled=False,
        timeout=max(60.0, (config.commit_timeout_seconds or 600.0) + 30.0),
    )



def _epoch_eval_reports(train_result: Any) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for evaluation in getattr(train_result, "evaluation_passes", []) or []:
        report = getattr(evaluation, "metadata", {}).get("report")
        if isinstance(report, dict):
            reports.append(dict(report))
    return reports


def _policy_set_metadata(config: BatchTrainEvalConfig, client: AsyncHTTPClient) -> dict[str, Any]:
    return {
        "source": "remote_session_commit",
        "openviking_url": client_url(client),
        "openviking_api_key": getattr(client, "_api_key", None),
        "openviking_account": config.account_id,
        "openviking_user": config.user_id,
    }

def client_url(client: AsyncHTTPClient) -> str:
    return str(getattr(client, "_url", ""))


async def _load_or_run_baseline_eval(
    *,
    config: BatchTrainEvalConfig,
    pipeline: OfflinePolicyOptimizationPipeline,
    case_loader: RemoteCaseLoader,
    policy_set: ExperienceSet,
    report_builder: PipelineReportBuilder,
    event_recorder: JsonlEventRecorder,
) -> tuple[Any | None, bool]:
    cache_path = _baseline_cache_path(config)
    if not config.baseline_force_recompute:
        cached_report = _load_baseline_cache(cache_path)
        if cached_report is not None:
            await event_recorder.record(
                "baseline_cache_hit",
                stage="baseline_cache",
                baseline_cache_path=str(cache_path),
            )
            return None, True

    await event_recorder.record(
        "baseline_cache_recompute" if cache_path.exists() else "baseline_cache_miss",
        stage="baseline_cache",
        baseline_cache_path=str(cache_path),
    )
    baseline_result = await pipeline.eval(
        case_loader=case_loader,
        policy_set=policy_set,
        context=_pipeline_context(
            epoch=-1,
            training=False,
            max_epochs=1,
            rollout_stage="baseline_test_rollout",
            eval_split="test",
            eval_trials=config.trials,
            trial_index_key="eval_trial",
            report_builder=report_builder,
            event_recorder=event_recorder,
        ),
    )
    _write_baseline_cache(cache_path, baseline_result.metadata["report"], config=config)
    await event_recorder.record(
        "baseline_cache_write",
        stage="baseline_cache",
        baseline_cache_path=str(cache_path),
    )
    return baseline_result, False


def _write_baseline_cache(
    path: Path,
    report: dict[str, Any],
    *,
    config: BatchTrainEvalConfig,
) -> None:
    payload = {
        "cache_version": 1,
        "cache_key": _baseline_cache_key(config),
        "dataset": config.dataset,
        "domain": config.domain,
        "split": "test",
        "eval_limit": config.eval_limit,
        "trials": config.trials,
        "max_iterations": config.max_iterations,
        "keep_default_tools": config.keep_default_tools,
        "created_at": datetime.now().isoformat(),
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_baseline_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("cache_version") != 1:
        raise ValueError(f"unsupported baseline cache version in {path}")
    report = payload.get("report")
    if not isinstance(report, dict):
        raise ValueError(f"baseline cache file has no report: {path}")
    return {
        **report,
        "baseline_cache_hit": True,
        "baseline_cache_path": str(path),
    }


def _print_baseline_cache_hit(report: dict[str, Any], cache_path: Path) -> None:
    """Print cached baseline info before training starts so users see it immediately."""
    trial_count = int(report.get("trial_count") or 1)
    cache_info = f" (from cache: {cache_path.name})"
    if trial_count > 1:
        accuracy_mean = report.get("accuracy_mean")
        accuracy_std = report.get("accuracy_std")
        reward_mean = report.get("average_reward_mean")
        reward_std = report.get("average_reward_std")
        cases_per_trial = report.get("case_count_per_trial") or "varies"
        print(
            f"[baseline_test_rollout] baseline_cache_hit=1 accuracy="
            f"{_fmt_percent(accuracy_mean)} ± {_fmt_pp_abs(accuracy_std)} "
            f"avg_reward={_fmt_score(reward_mean)} ± {_fmt_score(reward_std)} "
            f"trials={trial_count} cases_per_trial={cases_per_trial}"
            f"{cache_info}"
        )
        return
    accuracy = report.get("accuracy")
    passed = report.get("passed_count")
    total = report.get("case_count")
    avg_reward = report.get("average_reward")
    print(
        f"[baseline_test_rollout] baseline_cache_hit=1 accuracy={_fmt_percent(accuracy)} "
        f"passed={passed}/{total} avg_reward={_fmt_score(avg_reward)}"
        f"{cache_info}"
    )


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _fmt_pp_abs(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}pp"


def _fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.6f}"



def _build_pipeline(
    config: BatchTrainEvalConfig,
    policy_trainer: SessionCommitPolicyTrainer,
) -> OfflinePolicyOptimizationPipeline:
    return OfflinePolicyOptimizationPipeline(
        snapshotter=ContentHashPolicySnapshotter(prefix=f"{config.dataset}-policy-snapshot"),
        rollout_executor=RemoteRolloutExecutor(
            service_url=_require_benchmark_service_url(config),
            concurrency=config.concurrency,
            show_progress=True,
            progress_label="rollout",
            options={
                "config_path": config.config_path,
                "keep_default_tools": config.keep_default_tools,
                "max_iterations": config.max_iterations,
            },
        ),
        rollout_analyzer=UnusedRolloutAnalyzer(),
        gradient_estimator=UnusedGradientEstimator(),
        policy_optimizer=UnusedPolicyOptimizer(),
        policy_updater=UnusedPolicyUpdater(),
        policy_trainer=policy_trainer,
    )


def _pipeline_context(
    *,
    epoch: int,
    training: bool,
    max_epochs: int = 1,
    rollout_stage: str | None = None,
    eval_split: str | None = None,
    eval_each_epoch_case_loader: Any = None,
    eval_trials: int = 1,
    trial_index_key: str = "trial",
    report_builder: Any = None,
    event_recorder: JsonlEventRecorder | None = None,
) -> PipelineContext:
    execution_metadata = {"epoch": epoch, "training": training}
    if rollout_stage is not None:
        execution_metadata["rollout_stage"] = rollout_stage
    if eval_split is not None:
        execution_metadata["eval_split"] = eval_split
    hooks = None
    if event_recorder is not None:
        from openviking.session.train.components.report_builder import PipelineReportHook
        from openviking.session.train.components.reporter import ConsolePipelineReporter

        hooks = [
            PipelineReportHook(),
            JsonlPipelineEventHook(event_recorder),
            ConsolePipelineReporter(),
        ]
    return PipelineContext(
        analysis_context={"epoch": epoch},
        execution_metadata=execution_metadata,
        max_epochs=max_epochs,
        eval_each_epoch_case_loader=eval_each_epoch_case_loader,
        eval_trials=eval_trials,
        trial_index_key=trial_index_key,
        report_builder=report_builder,
        **({"lifecycle_hooks": hooks} if hooks is not None else {}),
    )


def _case_loader(config: BatchTrainEvalConfig, *, split: str, limit: int | None) -> RemoteCaseLoader:
    return RemoteCaseLoader(
        service_url=_require_benchmark_service_url(config),
        dataset=config.dataset,
        domain=config.domain,
        split=split,
        batch_size=config.batch_size,
        limit=limit,
    )


def _require_benchmark_service_url(config: BatchTrainEvalConfig) -> str:
    if not config.benchmark_service_url:
        raise ValueError(
            "benchmark_service_url is required; start benchmark service and pass "
            "--benchmark-service-url"
        )
    return config.benchmark_service_url


class UnusedRolloutAnalyzer:
    async def analyze(self, rollout: Rollout, context: Any = None) -> RolloutAnalysis:
        raise RuntimeError("eval uses rollout.evaluation; training is handled by policy_trainer")


class UnusedGradientEstimator:
    async def estimate(self, analysis: RolloutAnalysis, experience_set: ExperienceSet, context: Any):
        raise RuntimeError("policy_trainer handles training; gradient estimator must not run")


class UnusedPolicyOptimizer:
    async def plan(self, gradients: list[Any], policy_set: ExperienceSet, context: Any):
        raise RuntimeError("policy_trainer handles training; policy optimizer must not run")


class UnusedPolicyUpdater:
    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any,
        *,
        transaction_handle: Any = None,
    ):
        del transaction_handle
        raise RuntimeError("policy_trainer handles training; policy updater must not run")


def _write_report(report: BatchTrainEvalReport, config: BatchTrainEvalConfig) -> None:
    output_path = Path(_default_output_path(config)).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report.output_path = str(output_path)


def _events_path(config: BatchTrainEvalConfig) -> Path:
    if config.events_path:
        return Path(config.events_path).expanduser()
    return _run_output_dir(config) / "events.jsonl"


def _default_output_path(config: BatchTrainEvalConfig) -> str:
    if config.output_path:
        return str(Path(config.output_path).expanduser())
    return str(_run_output_dir(config) / "report.json")


def _baseline_cache_path(config: BatchTrainEvalConfig) -> Path:
    return (
        _result_base_dir(config)
        / "cache"
        / "baseline"
        / f"{_baseline_cache_key(config)}.json"
    )


def _baseline_cache_key(config: BatchTrainEvalConfig) -> str:
    payload = {
        "dataset": config.dataset,
        "domain": config.domain,
        "split": "test",
        "eval_limit": config.eval_limit,
        "trials": config.trials,
        "max_iterations": config.max_iterations,
        "keep_default_tools": config.keep_default_tools,
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = sha256(stable.encode("utf-8")).hexdigest()[:16]
    limit = "all" if config.eval_limit is None else str(config.eval_limit)
    return f"{_cache_slug(config.domain)}_test_limit-{limit}_trials-{config.trials}_{digest}"


def _cache_slug(value: str) -> str:
    return (
        "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value).strip("-")
        or "default"
    )


def _clean_result_dir(config: BatchTrainEvalConfig) -> None:
    if not config.clean_result:
        return
    if config.output_path:
        output_path = Path(config.output_path).expanduser()
        if output_path.exists() and output_path.is_file():
            output_path.unlink()
        return

    result_dir = _result_base_dir(config)
    if result_dir.exists():
        for child in result_dir.iterdir():
            if child.name == "cache":
                continue
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    result_dir.mkdir(parents=True, exist_ok=True)
    print(f"[batch-train-eval] clean_result=1 path={result_dir}", flush=True)


def _run_output_dir(config: BatchTrainEvalConfig) -> Path:
    if config.output_path:
        output_path = Path(config.output_path).expanduser()
        return output_path.parent
    return _result_base_dir(config) / f"{config.domain}_{config.run_timestamp}"


def _result_base_dir(config: BatchTrainEvalConfig) -> Path:
    return _repo_root() / "result" / config.dataset / "train"


def _latest_rollouts_path(config: BatchTrainEvalConfig) -> Path:
    return _repo_root() / "result" / config.dataset / "train" / "latest_rollouts"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
