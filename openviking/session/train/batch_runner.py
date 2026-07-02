"""Generic remote benchmark batch train/eval orchestration."""

from __future__ import annotations

import inspect
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
from openviking.session.train.components.dataset_service import rollout_from_dict, rollout_to_dict
from openviking.session.train.components.event_recorder import (
    CompositeEventRecorder,
    JsonlEventRecorder,
    JsonlPipelineEventHook,
)
from openviking.session.train.components.progress import format_label, label_style
from openviking.session.train.components.remote import RemoteCaseLoader, RemoteRolloutExecutor
from openviking.session.train.components.report_builder import PipelineReportBuilder
from openviking.session.train.components.reporter import (
    _accuracy_style,
    _style_plain,
    emit_run_summary,
    fmt_percent,
    fmt_percentage_point_abs,
)
from openviking.session.train.components.rollout_artifact_recorder import (
    RolloutArtifactEventRecorder,
    RolloutArtifactRecorder,
)
from openviking.session.train.components.session_commit import SessionCommitPolicyTrainer
from openviking.session.train.components.snapshotter import ContentHashPolicySnapshotter
from openviking.session.train.context import ExecutionContext, PipelineContext
from openviking.session.train.domain import (
    Case,
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
    concurrency: int = 200
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
    commit_concurrency: int = 200
    train_index: int | str | list[int] | tuple[int, ...] | None = None
    eval_index: int | str | list[int] | tuple[int, ...] | None = None
    benchmark_service_url: str | None = None
    baseline_force_recompute: bool = False
    skip_baseline_eval: bool = False
    eval_each_epoch: bool = False
    eval_split: str | None = "test"
    skip_final_eval: bool = False
    trials: int = 8
    train_trials: int = 1
    reuse_train_rollout_cache: bool = False
    clean_result: bool = True
    keep_recent_results: int = 5
    events_path: str | None = None
    result_dir_name: str = "train"
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
        self.train_index = _normalize_index_filter(self.train_index, label="train_index")
        self.eval_index = _normalize_index_filter(self.eval_index, label="eval_index")
        if self.eval_split is not None:
            normalized_eval_split = str(self.eval_split).strip().lower()
            if normalized_eval_split in {"", "none"}:
                self.eval_split = None
            elif normalized_eval_split not in {"train", "test"}:
                raise ValueError("eval_split must be train, test, or none")
            else:
                self.eval_split = normalized_eval_split
        if self.trials <= 0:
            raise ValueError("trials must be > 0")
        if self.train_trials <= 0:
            raise ValueError("train_trials must be > 0")
        if self.benchmark_service_url is not None and not self.benchmark_service_url.strip():
            raise ValueError("benchmark_service_url must not be empty")
        if self.keep_recent_results < 0:
            raise ValueError("keep_recent_results must be >= 0")
        if not str(self.result_dir_name or "").strip():
            raise ValueError("result_dir_name must not be empty")


def _normalize_index_filter(value: Any, *, label: str) -> list[int] | None:
    if value is None:
        return None
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, int):
        raw_items = [value]
    else:
        raw_items = []
        try:
            iterable = list(value)
        except TypeError as exc:
            raise ValueError(f"{label} must be an integer or comma-separated integers") from exc
        for item in iterable:
            if isinstance(item, str) and "," in item:
                raw_items.extend(part.strip() for part in item.split(",") if part.strip())
            else:
                raw_items.append(item)
    result: list[int] = []
    for item in raw_items:
        try:
            index = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be an integer or comma-separated integers") from exc
        if index < 0:
            raise ValueError(f"{label} must be >= 0")
        if index not in result:
            result.append(index)
    if not result:
        raise ValueError(f"{label} must not be empty")
    return result


@dataclass(slots=True)
class BatchTrainEvalReport:
    """Serializable report for remote benchmark batch train/eval."""

    dataset: str
    domain: str
    epochs: int
    batch_size: int | None
    concurrency: int
    commit_concurrency: int
    train_index: int | list[int] | None
    eval_index: int | list[int] | None
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
    eval_split: str | None = "test"
    skip_baseline_eval: bool = False
    trials: int = 8
    train_trials: int = 1
    reuse_train_rollout_cache: bool = False
    rollouts_root: str | None = None
    rollouts_index_path: str | None = None
    latest_failed_rollout: str | None = None
    clean_result: bool = True
    keep_recent_results: int = 5
    events_path: str | None = None
    result_dir_name: str = "train"
    baseline_cache_path: str | None = None
    baseline_cache_hit: bool = False
    baseline_force_recompute: bool = False
    skip_final_eval: bool = False
    final_eval_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "domain": self.domain,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "concurrency": self.concurrency,
            "commit_concurrency": self.commit_concurrency,
            "train_index": self.train_index,
            "eval_index": self.eval_index,
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
            "eval_split": self.eval_split,
            "skip_baseline_eval": self.skip_baseline_eval,
            "trials": self.trials,
            "train_trials": self.train_trials,
            "reuse_train_rollout_cache": self.reuse_train_rollout_cache,
            "rollouts_root": self.rollouts_root,
            "rollouts_index_path": self.rollouts_index_path,
            "latest_failed_rollout": self.latest_failed_rollout,
            "clean_result": self.clean_result,
            "keep_recent_results": self.keep_recent_results,
            "events_path": self.events_path,
            "result_dir_name": self.result_dir_name,
            "baseline_cache_path": self.baseline_cache_path,
            "baseline_cache_hit": self.baseline_cache_hit,
            "baseline_force_recompute": self.baseline_force_recompute,
            "skip_final_eval": self.skip_final_eval,
            "final_eval_source": self.final_eval_source,
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
            train_index=_index_payload(config.train_index),
            eval_index=_index_payload(config.eval_index),
            trials=config.trials,
            train_trials=config.train_trials,
            reuse_train_rollout_cache=config.reuse_train_rollout_cache,
            clean_result=config.clean_result,
            keep_recent_results=config.keep_recent_results,
            result_dir_name=config.result_dir_name,
            baseline_force_recompute=config.baseline_force_recompute,
            skip_baseline_eval=config.skip_baseline_eval,
            eval_split=config.eval_split,
            skip_final_eval=config.skip_final_eval,
            baseline_cache_path=(
                None
                if config.skip_baseline_eval or config.eval_split is None
                else str(_baseline_cache_path(config))
            ),
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
        if isinstance(remote_executor, (RemoteRolloutExecutor, CachedEpochZeroTrainRolloutExecutor)):
            remote_executor.on_rollout_complete = (
                rollout_artifact_recorder.record_rollout_completion
            )
        policy_trainer.event_recorder = CompositeEventRecorder(
            (event_recorder, RolloutArtifactEventRecorder(rollout_artifact_recorder))
        )

        baseline_eval: dict[str, Any] | None = None
        baseline_cache_hit = False
        baseline_cache_path = (
            None
            if config.skip_baseline_eval or config.eval_split is None
            else _baseline_cache_path(config)
        )
        final_eval: dict[str, Any] | None = None
        report_builder = PipelineReportBuilder(trial_index_key="eval_trial")

        eval_loader = (
            None
            if config.eval_split is None
            else _case_loader(
                config,
                split=config.eval_split,
                sample_index=config.eval_index,
            )
        )
        if (
            eval_loader is not None
            and not config.skip_baseline_eval
            and await eval_loader.split_exists()
        ):
            baseline_result, baseline_cache_hit = await _load_or_run_baseline_eval(
                config=config,
                pipeline=pipeline,
                case_loader=eval_loader,
                policy_set=policy_set,
                report_builder=report_builder,
                event_recorder=event_recorder,
            )
            if baseline_result is not None:
                rollout_artifact_recorder.record_eval(
                    label=_eval_rollout_stage("baseline", config.eval_split),
                    epoch=-1,
                    analyses=baseline_result.analyses,
                )
                baseline_eval = baseline_result.metadata["report"]
            else:
                assert baseline_cache_path is not None
                baseline_eval = _load_baseline_cache(baseline_cache_path)
                if baseline_eval is not None:
                    _print_baseline_cache_hit(baseline_eval, baseline_cache_path)

        train_loader = _case_loader(
            config,
            split="train",
            sample_index=config.train_index,
        )

        train_context = _pipeline_context(
            epoch=0,
            training=True,
            max_epochs=config.epochs,
            eval_each_epoch_case_loader=(
                eval_loader
                if (
                    eval_loader is not None
                    and config.eval_each_epoch
                    and await eval_loader.split_exists()
                )
                else None
            ),
            rollout_stage=(
                _eval_rollout_stage("epoch", config.eval_split)
                if config.eval_split is not None
                else None
            ),
            eval_split=config.eval_split,
            eval_trials=config.trials,
            train_trials=config.train_trials,
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

        epoch_eval_reports = _epoch_eval_reports(train_result)
        final_eval_source: str | None = None
        if config.skip_final_eval:
            if epoch_eval_reports:
                final_eval = dict(epoch_eval_reports[-1])
                final_eval_source = "last_epoch_eval"
        elif eval_loader is not None and await eval_loader.split_exists():
            final_result = await pipeline.eval(
                case_loader=eval_loader,
                policy_set=policy_set,
                context=_pipeline_context(
                    epoch=config.epochs,
                    training=False,
                    max_epochs=1,
                    rollout_stage=_eval_rollout_stage("final", config.eval_split),
                    eval_split=config.eval_split,
                    eval_trials=config.trials,
                    trial_index_key="eval_trial",
                    report_builder=report_builder,
                    event_recorder=event_recorder,
                ),
            )
            rollout_artifact_recorder.record_eval(
                label=_eval_rollout_stage("final", config.eval_split),
                epoch=config.epochs,
                analyses=final_result.analyses,
            )
            final_eval = final_result.metadata["report"]
            final_eval_source = f"final_{config.eval_split}"

        accuracy_delta = report_builder.accuracy_delta(baseline_eval, final_eval)
        rollout_artifact_index = rollout_artifact_recorder.finalize()
        report = BatchTrainEvalReport(
            dataset=config.dataset,
            domain=config.domain,
            epochs=config.epochs,
            batch_size=config.batch_size,
            concurrency=config.concurrency,
            commit_concurrency=config.commit_concurrency,
            train_index=_index_payload(config.train_index),
            eval_index=_index_payload(config.eval_index),
            policy_root_uri=policy_root_uri,
            baseline_eval=baseline_eval,
            train_epochs=list(train_result.metadata.get("train_reports", [])),
            epoch_evals=epoch_eval_reports,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=_default_output_path(config),
            trace_id=tracer.get_trace_id() or None,
            run_id=policy_trainer.run_id,
            server_url=client_url(client),
            benchmark_service_url=config.benchmark_service_url,
            eval_each_epoch=config.eval_each_epoch,
            eval_split=config.eval_split,
            skip_baseline_eval=config.skip_baseline_eval,
            trials=config.trials,
            train_trials=config.train_trials,
            reuse_train_rollout_cache=config.reuse_train_rollout_cache,
            rollouts_root=rollout_artifact_index.rollouts_root,
            rollouts_index_path=str(run_dir / "rollouts_index.json"),
            latest_failed_rollout=rollout_artifact_index.latest_failed_rollout,
            clean_result=config.clean_result,
            keep_recent_results=config.keep_recent_results,
            events_path=str(_events_path(config)),
            result_dir_name=config.result_dir_name,
            baseline_cache_path=(
                str(baseline_cache_path) if baseline_cache_path is not None else None
            ),
            baseline_cache_hit=baseline_cache_hit,
            baseline_force_recompute=config.baseline_force_recompute,
            skip_final_eval=config.skip_final_eval,
            final_eval_source=final_eval_source,
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
                "train_trials": config.train_trials,
                "reuse_train_rollout_cache": config.reuse_train_rollout_cache,
                "run_id": policy_trainer.run_id,
                "trace_id": report.trace_id,
                "baseline_cache_hit": report.baseline_cache_hit,
                "skip_baseline_eval": report.skip_baseline_eval,
                "eval_split": report.eval_split,
                "skip_final_eval": report.skip_final_eval,
                "final_eval_source": report.final_eval_source,
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
                eval_split=config.eval_split,
            )
            return None, True

    await event_recorder.record(
        "baseline_cache_recompute" if cache_path.exists() else "baseline_cache_miss",
        stage="baseline_cache",
        baseline_cache_path=str(cache_path),
        eval_split=config.eval_split,
    )
    baseline_result = await pipeline.eval(
        case_loader=case_loader,
        policy_set=policy_set,
        context=_pipeline_context(
            epoch=-1,
            training=False,
            max_epochs=1,
            rollout_stage=_eval_rollout_stage("baseline", config.eval_split),
            eval_split=config.eval_split,
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
        eval_split=config.eval_split,
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
        "split": config.eval_split,
        "eval_index": _index_payload(config.eval_index),
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
    label = str(report.get("rollout_stage") or "baseline_test_rollout")
    trial_count = int(report.get("trial_count") or 1)
    cache_info = f"(from cache: {cache_path.name})"
    label_text = _style_plain(format_label(label), label_style(label))
    if trial_count > 1:
        accuracy_mean = report.get("accuracy_mean")
        accuracy_std = report.get("accuracy_std")
        cases_per_trial = report.get("case_count_per_trial") or "varies"
        print(
            f"{label_text} baseline_cache_hit=1 "
            f"accuracy={_style_plain(fmt_percent(accuracy_mean), _accuracy_style(accuracy_mean))} "
            f"± {_style_plain(fmt_percentage_point_abs(accuracy_std), 'yellow')} "
            f"trials={trial_count} cases_per_trial={cases_per_trial} "
            f"{cache_info}"
        )
        return
    accuracy = report.get("accuracy")
    passed = report.get("passed_count")
    total = report.get("case_count")
    print(
        f"{label_text} baseline_cache_hit=1 "
        f"accuracy={_style_plain(fmt_percent(accuracy), _accuracy_style(accuracy))} "
        f"passed={passed}/{total} "
        f"{cache_info}"
    )


def _eval_rollout_stage(kind: str, split: str | None) -> str:
    eval_split = str(split or "test")
    if kind == "epoch" and eval_split == "train":
        return "eval_train_rollout"
    return f"{kind}_{eval_split}_rollout"


def _build_pipeline(
    config: BatchTrainEvalConfig,
    policy_trainer: SessionCommitPolicyTrainer,
) -> OfflinePolicyOptimizationPipeline:
    rollout_executor: Any = RemoteRolloutExecutor(
        service_url=_require_benchmark_service_url(config),
        concurrency=config.concurrency,
        show_progress=True,
        progress_label="rollout",
        options={
            "config_path": config.config_path,
            "keep_default_tools": config.keep_default_tools,
            "max_iterations": config.max_iterations,
        },
    )
    if config.reuse_train_rollout_cache:
        rollout_executor = CachedEpochZeroTrainRolloutExecutor(
            delegate=rollout_executor,
            cache_dir=_train_rollout_cache_dir(config),
            cache_key_prefix=_train_rollout_cache_key_prefix(config),
        )
    return OfflinePolicyOptimizationPipeline(
        snapshotter=ContentHashPolicySnapshotter(prefix=f"{config.dataset}-policy-snapshot"),
        rollout_executor=rollout_executor,
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
    train_trials: int = 1,
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
        train_trials=train_trials,
        trial_index_key=trial_index_key,
        report_builder=report_builder,
        **({"lifecycle_hooks": hooks} if hooks is not None else {}),
    )


def _case_loader(
    config: BatchTrainEvalConfig,
    *,
    split: str,
    sample_index: list[int] | None,
) -> RemoteCaseLoader:
    filters: dict[str, Any] = {}
    if sample_index is not None:
        filters["task_indices"] = list(sample_index)
    return RemoteCaseLoader(
        service_url=_require_benchmark_service_url(config),
        dataset=config.dataset,
        domain=config.domain,
        split=split,
        batch_size=config.batch_size,
        filters=filters,
    )


@dataclass(slots=True)
class CachedEpochZeroTrainRolloutExecutor:
    """Reuse cached train rollouts only for epoch 0.

    The first training epoch is the no-new-memory rollout used to generate the
    initial training signal. Later epochs intentionally execute again so they
    can observe policy/memory updates from earlier commits.
    """

    delegate: Any
    cache_dir: Path
    cache_key_prefix: str
    on_rollout_complete: Any | None = None

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        metadata = dict(context.metadata or {})
        if not bool(metadata.get("training")) or int(metadata.get("epoch", 0) or 0) != 0:
            return await self.delegate.execute(cases, policy_set, context)

        case_list = list(cases)
        results: list[Rollout | None] = [None] * len(case_list)
        misses: list[tuple[int, Case]] = []
        for index, case in enumerate(case_list):
            cached = self._load(case)
            if cached is None:
                misses.append((index, case))
            else:
                cached.policy_snapshot_id = context.policy_snapshot_id
                metadata = dict(cached.metadata or {})
                metadata["train_rollout_cache_hit"] = True
                metadata["train_rollout_cache_path"] = str(self._path(case))
                cached.metadata = metadata
                results[index] = cached
                await self._emit_rollout_complete(
                    rollout=cached,
                    index=index,
                    context=context,
                )

        if misses:
            miss_rollouts = await self.delegate.execute(
                [case for _, case in misses],
                policy_set,
                context,
            )
            for (index, case), rollout in zip(misses, miss_rollouts, strict=True):
                self._write(case, rollout)
                results[index] = rollout
                await self._emit_rollout_complete(
                    rollout=rollout,
                    index=index,
                    context=context,
                )

        return [rollout for rollout in results if rollout is not None]

    def _path(self, case: Case) -> Path:
        payload = {
            "prefix": self.cache_key_prefix,
            "case": {
                "name": case.name,
                "task_signature": case.task_signature,
                "input": case.input,
                "metadata": case.metadata,
            },
        }
        stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = sha256(stable.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{_cache_slug(case.name)}_{digest}.json"

    def _load(self, case: Case) -> Rollout | None:
        path = self._path(case)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("cache_version") != 1:
            raise ValueError(f"unsupported train rollout cache version in {path}")
        rollout_data = payload.get("rollout")
        if not isinstance(rollout_data, dict):
            raise ValueError(f"train rollout cache file has no rollout: {path}")
        return rollout_from_dict(rollout_data)

    def _write(self, case: Case, rollout: Rollout) -> None:
        path = self._path(case)
        payload = {
            "cache_version": 1,
            "cache_key_prefix": self.cache_key_prefix,
            "case_name": case.name,
            "task_signature": case.task_signature,
            "created_at": datetime.now().isoformat(),
            "rollout": rollout_to_dict(rollout),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _emit_rollout_complete(
        self,
        *,
        rollout: Rollout,
        index: int,
        context: ExecutionContext,
    ) -> None:
        if self.on_rollout_complete is None:
            return
        result = self.on_rollout_complete(
            rollout=rollout,
            index=index,
            context=context,
        )
        if inspect.isawaitable(result):
            await result


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
    async def estimate(
        self, analysis: RolloutAnalysis, experience_set: ExperienceSet, context: Any
    ):
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
    return _result_base_dir(config) / "cache" / "baseline" / f"{_baseline_cache_key(config)}.json"


def _train_rollout_cache_dir(config: BatchTrainEvalConfig) -> Path:
    return (
        _result_base_dir(config)
        / "cache"
        / "train_rollouts"
        / _train_rollout_cache_key_prefix(config)
    )


def _train_rollout_cache_key_prefix(config: BatchTrainEvalConfig) -> str:
    payload = {
        "dataset": config.dataset,
        "domain": config.domain,
        "split": "train",
        "train_index": _index_payload(config.train_index),
        "train_trials": config.train_trials,
        "max_iterations": config.max_iterations,
        "keep_default_tools": config.keep_default_tools,
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = sha256(stable.encode("utf-8")).hexdigest()[:16]
    index = _index_label(config.train_index)
    return f"{_cache_slug(config.domain)}_train_index-{index}_trials-{config.train_trials}_{digest}"


def _baseline_cache_key(config: BatchTrainEvalConfig) -> str:
    payload = {
        "dataset": config.dataset,
        "domain": config.domain,
        "split": config.eval_split,
        "eval_index": _index_payload(config.eval_index),
        "trials": config.trials,
        "max_iterations": config.max_iterations,
        "keep_default_tools": config.keep_default_tools,
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = sha256(stable.encode("utf-8")).hexdigest()[:16]
    index = _index_label(config.eval_index)
    split = _cache_slug(str(config.eval_split or "none"))
    return f"{_cache_slug(config.domain)}_{split}_index-{index}_trials-{config.trials}_{digest}"


def _index_payload(indices: list[int] | None) -> int | list[int] | None:
    if indices is None:
        return None
    return indices[0] if len(indices) == 1 else list(indices)


def _index_label(indices: list[int] | None) -> str:
    if indices is None:
        return "all"
    if len(indices) == 1:
        return str(indices[0])
    return "multi-" + "-".join(str(item) for item in indices)


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
    result_dir.mkdir(parents=True, exist_ok=True)

    protected_names = {_run_dir_name(config)}
    run_dirs = []
    removed = 0
    if result_dir.exists():
        for child in result_dir.iterdir():
            if child.name in protected_names:
                continue
            if child.is_dir() and not child.is_symlink():
                if _is_default_run_dir(child, config):
                    run_dirs.append(child)
                continue

    run_dirs.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    keep_count = config.keep_recent_results
    for stale_dir in run_dirs[keep_count:]:
        shutil.rmtree(stale_dir)
        removed += 1

    print(
        f"[batch-train-eval] clean_result=1 path={result_dir} "
        f"keep_recent_results={keep_count} removed={removed}",
        flush=True,
    )


def _is_default_run_dir(path: Path, config: BatchTrainEvalConfig) -> bool:
    prefix = f"run_{config.domain}_"
    if not path.name.startswith(prefix):
        return False
    suffix = path.name[len(prefix) :]
    return len(suffix) == 15 and suffix[8] == "_" and suffix[:8].isdigit() and suffix[9:].isdigit()


def _run_dir_name(config: BatchTrainEvalConfig) -> str:
    return f"run_{config.domain}_{config.run_timestamp}"


def _run_output_dir(config: BatchTrainEvalConfig) -> Path:
    if config.output_path:
        output_path = Path(config.output_path).expanduser()
        return output_path.parent
    return _result_base_dir(config) / _run_dir_name(config)


def _result_base_dir(config: BatchTrainEvalConfig) -> Path:
    return _repo_root() / "result" / config.dataset / config.result_dir_name


def _latest_rollouts_path(config: BatchTrainEvalConfig) -> Path:
    return _repo_root() / "result" / config.dataset / config.result_dir_name / "latest_rollouts"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
