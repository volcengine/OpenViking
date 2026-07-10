# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from pathlib import Path


def test_alfworld_case_loader_filters_explicit_gamefiles():
    from benchmark.alfworld.train.case_loader import AlfworldCaseLoader

    loader = AlfworldCaseLoader(
        domain="pick_and_place",
        split="test",
        data_root="/tmp/alfworld",
        gamefiles=["valid_unseen/pick_and_place/task/game.tw-pddl"],
    )

    cases = loader.load_cases()

    assert len(cases) == 1
    assert cases[0].input["eval_dataset"] == "eval_out_of_distribution"
    assert cases[0].input["task_type"] == "pick_and_place"
    assert cases[0].input["gamefile"] == str(
        (Path("/tmp/alfworld") / "valid_unseen/pick_and_place/task/game.tw-pddl").resolve()
    )


def test_alfworld_case_loader_returns_empty_when_data_missing_by_default():
    from benchmark.alfworld.train.case_loader import AlfworldCaseLoader

    loader = AlfworldCaseLoader(domain="all", split="train", data_root="/missing", case_count=2)

    assert loader.load_cases() == []


def test_alfworld_case_loader_can_emit_pseudo_cases_when_enabled():
    from benchmark.alfworld.train.case_loader import AlfworldCaseLoader

    loader = AlfworldCaseLoader(
        domain="all",
        split="train",
        data_root="/missing",
        case_count=2,
        allow_pseudo_cases=True,
    )

    cases = loader.load_cases()

    assert [case.input["gamefile"] for case in cases] == ["", ""]
    assert [case.input["eval_dataset"] for case in cases] == ["train", "train"]


def test_alfworld_service_wires_generic_dataset_service(monkeypatch):
    import benchmark.alfworld.train.service_app as service_app

    captured = {}

    def fake_create_dataset_service_app(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(service_app, "create_dataset_service_app", fake_create_dataset_service_app)

    app = service_app.create_app(data_root="/tmp/alfworld", default_case_count=3)
    loader = app["make_case_loader"](
        "alfworld",
        "all",
        "test",
        {"task_indices": [1], "case_count": 4},
    )
    executor = app["make_rollout_executor"]({"max_iterations": 7, "show_progress": True})
    vikingbot_executor = app["make_rollout_executor"](
        {"rollout_backend": "vikingbot", "loader_mode": "skill", "max_iterations": 9}
    )

    assert captured["service_name"] == "alfworld"
    assert loader.task_indices == [1]
    assert loader.case_count == 4
    assert executor.max_steps == 7
    assert executor.show_progress is True
    assert vikingbot_executor.max_steps == 9
    assert vikingbot_executor.loader_mode == "skill"


def test_alfworld_explicit_gamefiles_do_not_collect_whole_split():
    from benchmark.alfworld.train.rollout_executor import _instantiate_alfworld_env_with_gamefiles

    class FakeAlfredTWEnv:
        def __init__(self, config, train_eval="train"):
            self.config = config
            self.train_eval = train_eval
            self.collect_game_files()

        def collect_game_files(self, verbose=False):
            raise AssertionError("default split scan should not be used for explicit gamefiles")

    env = _instantiate_alfworld_env_with_gamefiles(
        FakeAlfredTWEnv,
        {},
        train_eval="train",
        gamefiles=["/tmp/alfworld/game.tw-pddl"],
    )

    assert env.game_files == ["/tmp/alfworld/game.tw-pddl"]
    assert env.num_games == 1
