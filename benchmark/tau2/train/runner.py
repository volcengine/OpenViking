#!/usr/bin/env python3
"""Tau2 batch train/eval orchestration through OfflinePolicyOptimizationPipeline."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openviking.server.config import load_server_config
from openviking.server.identity import AuthMode
from openviking.session.train import (
    ContentHashPolicySnapshotter,
    ExperienceSet,
    OfflinePolicyOptimizationPipeline,
    PipelineContext,
    PipelineEvaluationResult,
    PipelineResult,
    Rollout,
    RolloutAnalysis,
    SessionCommitPolicyTrainer,
)
from openviking.session.train.components.remote import RemoteCaseLoader, RemoteRolloutExecutor
from openviking.session.train.domain import PolicyUpdatePlan
from openviking.telemetry import tracer
from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@dataclass(slots=True)
class Tau2BatchRunConfig:
    """Configuration for one tau2 batch train/eval run."""

    domain: str
    dataset: str = "tau2"
    epochs: int = 1
    batch_size: int | None = None
    concurrency: int = 20
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
    commit_timeout_seconds: float = 600.0
    commit_concurrency: int = 20
    train_limit: int | None = None
    eval_limit: int | None = None
    benchmark_service_url: str | None = None
    baseline_eval_enabled: bool = False

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
        if self.commit_timeout_seconds <= 0:
            raise ValueError("commit_timeout_seconds must be > 0")
        if self.commit_concurrency <= 0:
            raise ValueError("commit_concurrency must be > 0")
        if self.train_limit is not None and self.train_limit <= 0:
            raise ValueError("train_limit must be > 0")
        if self.eval_limit is not None and self.eval_limit <= 0:
            raise ValueError("eval_limit must be > 0")
        if self.benchmark_service_url is not None and not self.benchmark_service_url.strip():
            raise ValueError("benchmark_service_url must not be empty")


@dataclass(slots=True)
class Tau2BatchRunReport:
    """Serializable report for tau2 batch train/eval."""

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
    final_eval: dict[str, Any] | None = None
    accuracy_delta: float | None = None
    output_path: str | None = None
    trace_id: str | None = None
    run_id: str = ""
    server_url: str = ""
    benchmark_service_url: str | None = None
    baseline_eval_enabled: bool = False

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
            "final_eval": self.final_eval,
            "accuracy_delta": self.accuracy_delta,
            "output_path": self.output_path,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "server_url": self.server_url,
            "benchmark_service_url": self.benchmark_service_url,
            "baseline_eval_enabled": self.baseline_eval_enabled,
        }


@tracer("tau2.batch_train_eval.run", ignore_result=True, ignore_args=True)
async def run_tau2_batch_train_eval(config: Tau2BatchRunConfig) -> Tau2BatchRunReport:
    """Run baseline eval, commit-based train epochs, and final eval for one tau2 domain."""

    _configure_openviking_config(config.config_path)
    client = _build_http_client(config)
    await client.initialize()
    try:
        policy_root_uri = "viking://user/memories/experiences"
        policy_set = ExperienceSet(
            root_uri=policy_root_uri,
            policies=[],
            metadata={"source": "remote_session_commit"},
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
        pipeline = _build_pipeline(config, policy_trainer)

        baseline_eval: dict[str, Any] | None = None
        final_eval: dict[str, Any] | None = None
        train_epoch_reports: list[dict[str, Any]] = []

        test_loader = _case_loader(config, split="test", limit=config.eval_limit)
        if config.baseline_eval_enabled and await test_loader.split_exists():
            baseline_result = await pipeline.eval(
                case_loader=test_loader,
                policy_set=policy_set,
                context=_pipeline_context(epoch=-1, training=False),
            )
            baseline_eval = _evaluation_report(baseline_result)
            _print_eval_summary("baseline_eval", baseline_eval)

        for epoch in range(config.epochs):
            train_loader = _case_loader(config, split="train", limit=config.train_limit)
            result = await pipeline.train(
                case_loader=train_loader,
                policy_set=policy_set,
                context=_pipeline_context(epoch=epoch, training=True),
            )
            epoch_report = _train_result_report(result, epoch=epoch)
            train_epoch_reports.append(epoch_report)
            _print_train_summary(epoch_report)

        if await test_loader.split_exists():
            final_result = await pipeline.eval(
                case_loader=test_loader,
                policy_set=policy_set,
                context=_pipeline_context(epoch=config.epochs, training=False),
            )
            final_eval = _evaluation_report(final_result)
            _print_eval_summary("final_eval", final_eval)

        accuracy_delta = _accuracy_delta(baseline_eval, final_eval)
        report = Tau2BatchRunReport(
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
            train_epochs=train_epoch_reports,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=_default_output_path(config),
            trace_id=tracer.get_trace_id() or None,
            run_id=policy_trainer.run_id,
            server_url=client_url(client),
            benchmark_service_url=config.benchmark_service_url,
            baseline_eval_enabled=config.baseline_eval_enabled,
        )
        _write_report(report, config)
        _print_report_summary(report)
        return report
    finally:
        await client.close()


def _configure_openviking_config(config_path: str | None) -> None:
    if config_path:
        os.environ["OPENVIKING_CONFIG_FILE"] = str(Path(config_path).expanduser())
    OpenVikingConfigSingleton.reset_instance()


def _build_http_client(config: Tau2BatchRunConfig) -> AsyncHTTPClient:
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
        timeout=max(60.0, config.commit_timeout_seconds + 30.0),
    )


def client_url(client: AsyncHTTPClient) -> str:
    return str(getattr(client, "_url", ""))


def _build_pipeline(
    config: Tau2BatchRunConfig,
    policy_trainer: SessionCommitPolicyTrainer,
) -> OfflinePolicyOptimizationPipeline:
    return OfflinePolicyOptimizationPipeline(
        snapshotter=ContentHashPolicySnapshotter(prefix="tau2-policy-snapshot"),
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


def _pipeline_context(*, epoch: int, training: bool) -> PipelineContext:
    return PipelineContext(
        analysis_context={"epoch": epoch},
        execution_metadata={"epoch": epoch, "training": training},
        max_epochs=1,
    )


def _case_loader(config: Tau2BatchRunConfig, *, split: str, limit: int | None) -> RemoteCaseLoader:
    return RemoteCaseLoader(
        service_url=_require_benchmark_service_url(config),
        dataset=config.dataset,
        domain=config.domain,
        split=split,
        batch_size=config.batch_size,
        limit=limit,
    )


def _require_benchmark_service_url(config: Tau2BatchRunConfig) -> str:
    if not config.benchmark_service_url:
        raise ValueError("benchmark_service_url is required; start benchmark service and pass --benchmark-service-url")
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
    async def apply(self, plan: PolicyUpdatePlan, policy_set: ExperienceSet, context: Any):
        raise RuntimeError("policy_trainer handles training; policy updater must not run")


def _evaluation_report(result: PipelineEvaluationResult) -> dict[str, Any]:
    rewards = [float(analysis.evaluation.score) for analysis in result.analyses]
    passed_count = sum(1 for analysis in result.analyses if analysis.evaluation.passed)
    case_count = len(result.analyses)
    return {
        "epoch": result.epoch,
        "case_count": case_count,
        "accuracy": _ratio(passed_count, case_count),
        "passed_count": passed_count,
        "average_reward": _average(rewards),
        "rewards": rewards,
        "snapshot_ids": list(result.policy_snapshot_ids),
        "metadata": dict(result.metadata),
        "memory_usage": _memory_usage_from_analyses(result.analyses),
    }


def _train_result_report(result: PipelineResult, *, epoch: int) -> dict[str, Any]:
    rewards = [float(analysis.evaluation.score) for analysis in result.analyses]
    passed_count = sum(1 for analysis in result.analyses if analysis.evaluation.passed)
    case_count = len(result.analyses)
    commit_results = [
        item
        for epoch_result in result.epochs
        for item in epoch_result.apply_result.metadata.get("commit_results", [])
    ]
    errors = [error for item in commit_results if (error := item.get("error"))]
    snapshot_ids = [sid for item in result.epochs for sid in item.policy_snapshot_ids]
    return {
        "epoch": epoch,
        "case_count": case_count,
        "accuracy": _ratio(passed_count, case_count),
        "passed_count": passed_count,
        "average_reward": _average(rewards),
        "batch_count": len(snapshot_ids),
        "gradient_count": len(result.gradients),
        "committed_rollout_count": len(commit_results),
        "errors": errors,
        "snapshot_ids": snapshot_ids,
        "commit_results": commit_results,
        "metadata": dict(result.metadata),
        "memory_usage": _memory_usage_from_analyses(result.analyses),
    }


def _memory_usage_from_analyses(analyses: list[RolloutAnalysis]) -> dict[str, Any]:
    rollouts = [
        rollout
        for analysis in analyses
        if isinstance((rollout := analysis.metadata.get("rollout")), Rollout)
    ]
    rollout_count = len(rollouts)
    memory_context_count = 0
    memory_tool_call_rollout_count = 0
    memory_tool_call_total = 0
    for rollout in rollouts:
        metadata = rollout.metadata or {}
        if str(metadata.get("memory") or "").strip():
            memory_context_count += 1
        tool_call_count = _memory_tool_call_count(metadata.get("tools_used"))
        if tool_call_count:
            memory_tool_call_rollout_count += 1
            memory_tool_call_total += tool_call_count
    return {
        "rollout_count": rollout_count,
        "memory_context_count": memory_context_count,
        "memory_context_ratio": _ratio(memory_context_count, rollout_count),
        "memory_tool_call_rollout_count": memory_tool_call_rollout_count,
        "memory_tool_call_rollout_ratio": _ratio(
            memory_tool_call_rollout_count,
            rollout_count,
        ),
        "memory_tool_call_total": memory_tool_call_total,
    }


def _memory_tool_call_count(tools_used: Any) -> int:
    if not isinstance(tools_used, list):
        return 0
    count = 0
    for tool_info in tools_used:
        if not isinstance(tool_info, dict):
            continue
        tool_name = str(tool_info.get("tool_name") or "")
        if tool_name.startswith("openviking"):
            count += 1
    return count


def _accuracy_delta(
    baseline_eval: dict[str, Any] | None,
    final_eval: dict[str, Any] | None,
) -> float | None:
    if not baseline_eval or not final_eval:
        return None
    baseline = baseline_eval.get("accuracy")
    final = final_eval.get("accuracy")
    if baseline is None or final is None:
        return None
    return float(final) - float(baseline)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _write_report(report: Tau2BatchRunReport, config: Tau2BatchRunConfig) -> None:
    output_path = Path(_default_output_path(config)).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report.output_path = str(output_path)


def _default_output_path(config: Tau2BatchRunConfig) -> str:
    if config.output_path:
        return str(Path(config.output_path).expanduser())
    return str(
        Path(__file__).resolve().parent
        / "result"
        / f"{config.domain}_batch_train_eval.json"
    )


def _print_eval_summary(label: str, data: dict[str, Any]) -> None:
    print(
        f"[{label}] epoch={data['epoch']} cases={data['case_count']} "
        f"accuracy={_fmt_percent(data['accuracy'])} "
        f"passed={data['passed_count']}/{data['case_count']} "
        f"avg_reward={_fmt_score(data['average_reward'])}"
    )


def _print_train_summary(data: dict[str, Any]) -> None:
    print(
        f"[train_epoch] epoch={data['epoch']} cases={data['case_count']} "
        f"accuracy={_fmt_percent(data['accuracy'])} "
        f"passed={data['passed_count']}/{data['case_count']} "
        f"avg_reward={_fmt_score(data['average_reward'])} "
        f"commits={data['committed_rollout_count']} errors={len(data['errors'])}"
    )


def _print_report_summary(report: Tau2BatchRunReport) -> None:
    print("==== Tau2 Batch Train/Eval Report ====")
    print(f"dataset: {report.dataset}")
    print(f"domain: {report.domain}")
    print(f"epochs: {report.epochs}")
    print(f"commit_concurrency: {report.commit_concurrency}")
    print(f"run_id: {report.run_id}")
    print(f"server_url: {report.server_url}")
    print(f"policy_root_uri: {report.policy_root_uri}")
    if report.baseline_eval:
        print(
            "baseline accuracy: "
            f"{_fmt_percent(report.baseline_eval['accuracy'])} "
            f"({report.baseline_eval['passed_count']}/{report.baseline_eval['case_count']})"
        )
        print(f"baseline average reward: {_fmt_score(report.baseline_eval['average_reward'])}")
    if report.final_eval:
        print(
            "final accuracy: "
            f"{_fmt_percent(report.final_eval['accuracy'])} "
            f"({report.final_eval['passed_count']}/{report.final_eval['case_count']})"
        )
        print(f"final average reward: {_fmt_score(report.final_eval['average_reward'])}")
    if report.accuracy_delta is not None:
        print(f"accuracy delta: {_fmt_percentage_point(report.accuracy_delta)}")
    if report.benchmark_service_url:
        print(f"benchmark_service_url: {report.benchmark_service_url}")
    if report.trace_id:
        print(f"trace_id: {report.trace_id}")
    print(f"report: {report.output_path}")


def _fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.6f}"


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _fmt_percentage_point(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:+.2f}pp"
