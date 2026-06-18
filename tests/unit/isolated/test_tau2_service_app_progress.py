# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations


def test_tau2_service_configures_tau2_loguru_at_warning(monkeypatch):
    import benchmark.tau2.train.service_app as service_app

    calls = []

    class FakeLoguruLogger:
        def remove(self):
            calls.append(("remove",))

        def add(self, sink, *, level):
            del sink
            calls.append(("add", level))

    monkeypatch.setitem(
        __import__("sys").modules,
        "loguru",
        type("FakeLoguruModule", (), {"logger": FakeLoguruLogger()})(),
    )

    service_app.configure_tau2_service_logging()

    assert calls == [("remove",), ("add", "WARNING")]


def test_tau2_service_disables_rollout_progress_by_default(monkeypatch):
    import benchmark.tau2.train.service_app as service_app

    calls = []

    def fake_create_dataset_service_app(**kwargs):
        calls.append(kwargs)
        return kwargs

    class FakeExecutor:
        pass

    def fake_make_tau2_rollout_executor(**kwargs):
        calls.append({"factory": kwargs})
        return FakeExecutor()

    monkeypatch.setattr(service_app, "create_dataset_service_app", fake_create_dataset_service_app)
    monkeypatch.setattr(service_app, "make_tau2_rollout_executor", fake_make_tau2_rollout_executor)

    app = service_app.create_app(rollout_backend="native")
    executor = app["make_rollout_executor"]({"rollout_backend": "vikingbot", "max_iterations": 5})

    assert isinstance(executor, FakeExecutor)
    assert calls[-1]["factory"]["backend"] == "vikingbot"
    assert calls[-1]["factory"]["options"]["max_iterations"] == 5
    assert calls[-1]["factory"]["options"]["show_progress"] is False


def test_tau2_service_keeps_explicit_rollout_progress_override(monkeypatch):
    import benchmark.tau2.train.service_app as service_app

    calls = []

    def fake_create_dataset_service_app(**kwargs):
        return kwargs

    def fake_make_tau2_rollout_executor(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(service_app, "create_dataset_service_app", fake_create_dataset_service_app)
    monkeypatch.setattr(service_app, "make_tau2_rollout_executor", fake_make_tau2_rollout_executor)

    app = service_app.create_app(rollout_backend="native")
    app["make_rollout_executor"]({"rollout_backend": "native", "show_progress": True})

    assert calls[-1]["options"]["show_progress"] is True
