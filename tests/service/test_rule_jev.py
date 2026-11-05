from unittest.mock import MagicMock, patch

import pytest

from openviking.service.rule_jev import (
    RuleJevClient,
    RuleJevInput,
    RuleJevScore,
    ScenarioJevClient,
)
from openviking_cli.utils.config.jev_config import JevConfig


@pytest.mark.parametrize("applies_when", ["none", "(none)", "[]"])
def test_rule_jev_input_rejects_placeholder_applies_when(applies_when):
    with pytest.raises(ValueError, match="requires applies_when"):
        RuleJevInput(
            name="Protect production configuration",
            content="Obtain approval before changing production configuration.",
            applies_when=applies_when,
            exception="",
        )


@pytest.mark.parametrize("applies_when", ["", " \n"])
def test_rule_jev_input_accepts_empty_applies_when(applies_when):
    rule = RuleJevInput(
        name="Unconditional constraint",
        content="Always preserve confidentiality.",
        applies_when=applies_when,
        exception="",
    )
    assert rule.applies_when == ""


@patch("openviking.service.rule_jev.httpx.Client")
def test_scenario_jev_judge_uses_scenario_protocol_and_headers(mock_client_class):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "answers": {
            "unrelated_0": {
                "type": "noul",
                "noul": 0.93,
            }
        }
    }
    mock_client_class.return_value.post.return_value = response
    config = JevConfig(
        provider="typesafe",
        api_key="key",
        extra_headers={"X-Trace-ID": "trace"},
    )

    client = ScenarioJevClient.from_config(config)

    assert client is not None
    assert client.judge_scenarios("task", ["scenario"]) == [0.93]
    call = mock_client_class.return_value.post.call_args
    assert call.args == ("https://api.typesafe.ai/v1/systemone",)
    request = call.kwargs["json"]
    assert request["model"] == "jev-latest"
    assert request["state"] == {
        "task_context": "task",
        "scenarios": ["scenario"],
    }
    assert "query" not in request["state"]
    assert "candidate_documents" not in request["state"]
    question = request["questions"]["unrelated_0"]
    assert question["instructions"] == {
        "scenario_index": 0,
        "question": "Is scenarios[scenario_index] completely unrelated to task_context?",
    }
    assert question["criteria"] == {
        "true": (
            "The Scenario has no plausible connection to the current task, "
            "object, operation, evidence, or situation."
        ),
        "false": (
            "The Scenario is relevant, may apply, or there is any uncertainty "
            "about whether it could apply."
        ),
    }
    assert mock_client_class.call_args.kwargs["headers"]["X-Trace-ID"] == "trace"
    client.close()


@patch("openviking.service.rule_jev.httpx.Client")
def test_rule_jev_sends_rule_state_and_clause_criteria(mock_client_class):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "answers": {
            "applies_when_0": {"type": "noul", "noul": 0.91},
            "exception_0": {"type": "noul", "noul": 0.12},
        }
    }
    mock_client_class.return_value.post.return_value = response
    config = JevConfig(
        provider="custom",
        api_key="key",
        api_base="https://gateway.example.com/custom-rule-judge/",
        model="custom-jev",
    )
    rules = [
        RuleJevInput(
            name="Protect production configuration",
            content="Obtain approval before changing production configuration.",
            applies_when="The task changes production configuration.",
            exception="The task is limited to read-only inspection.",
        )
    ]

    client = RuleJevClient.from_config(config)

    assert client is not None
    assert client.judge_rules("Deploy production configuration.", rules) == [
        RuleJevScore(applies_when=0.91, exception=0.12)
    ]
    call = mock_client_class.return_value.post.call_args
    assert call.args == ("https://gateway.example.com/custom-rule-judge/",)
    request = call.kwargs["json"]
    assert request["state"] == {
        "task_context": "Deploy production configuration.",
        "rules": [
            {
                "name": "Protect production configuration",
                "content": "Obtain approval before changing production configuration.",
            }
        ],
    }
    assert "query" not in request["state"]
    assert "candidate_documents" not in request["state"]
    assert request["questions"]["applies_when_0"]["instructions"] == {
        "rule_index": 0,
        "question": (
            "Does rules[rule_index] apply to task_context? Judge only applicability, "
            "not whether the Rule is satisfied or violated."
        ),
    }
    assert request["questions"]["applies_when_0"]["criteria"] == {
        "true": "The task changes production configuration."
    }
    assert request["questions"]["exception_0"]["instructions"] == {
        "rule_index": 0,
        "question": "Does an exception to rules[rule_index] apply to task_context?",
    }
    assert request["questions"]["exception_0"]["criteria"] == {
        "true": "The task is limited to read-only inspection."
    }
    assert mock_client_class.return_value.post.call_count == 1
    client.close()


@patch("openviking.service.rule_jev.httpx.Client")
def test_rule_jev_rejects_missing_expected_answer(mock_client_class):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"answers": {}}
    mock_client_class.return_value.post.return_value = response
    client = RuleJevClient.from_config(JevConfig(provider="typesafe", api_key="key"))

    assert client is not None
    assert (
        client.judge_rules(
            "Deploy production configuration.",
            [
                RuleJevInput(
                    name="Protect production configuration",
                    content="Obtain approval before changing production configuration.",
                    applies_when="The task changes production configuration.",
                    exception="",
                )
            ],
        )
        is None
    )
    client.close()


@patch("openviking.service.rule_jev.httpx.Client")
def test_rule_jev_omits_empty_exception_question(mock_client_class):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"answers": {"applies_when_0": {"type": "noul", "noul": 0.84}}}
    mock_client_class.return_value.post.return_value = response
    client = RuleJevClient.from_config(JevConfig(provider="typesafe", api_key="key"))

    assert client is not None
    assert client.judge_rules(
        "Deploy production configuration.",
        [
            RuleJevInput(
                name="Protect production configuration",
                content="Obtain approval before changing production configuration.",
                applies_when="The task changes production configuration.",
                exception="",
            )
        ],
    ) == [RuleJevScore(applies_when=0.84, exception=0.0)]
    questions = mock_client_class.return_value.post.call_args.kwargs["json"]["questions"]
    assert set(questions) == {"applies_when_0"}
    client.close()


@patch("openviking.service.rule_jev.httpx.Client")
def test_rule_jev_empty_applies_when_judges_exception_only(mock_client_class):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"answers": {"exception_0": {"type": "noul", "noul": 0.9}}}
    mock_client_class.return_value.post.return_value = response
    client = RuleJevClient.from_config(JevConfig(provider="typesafe", api_key="key"))

    assert client is not None
    rules = [
        RuleJevInput("Global rule", "Require approval.", "", "Emergency recovery."),
        RuleJevInput("Other global rule", "Preserve confidentiality.", "", ""),
    ]
    assert client.judge_rules("Emergency recovery.", rules) == [
        RuleJevScore(applies_when=1.0, exception=0.9),
        RuleJevScore(applies_when=1.0, exception=0.0),
    ]
    questions = mock_client_class.return_value.post.call_args.kwargs["json"]["questions"]
    assert set(questions) == {"exception_0"}
    client.close()


@patch("openviking.service.rule_jev.httpx.Client")
def test_rule_jev_unconditional_rules_do_not_request_judgment(mock_client_class):
    client = RuleJevClient.from_config(JevConfig(provider="typesafe", api_key="key"))
    assert client is not None
    assert client.judge_rules(
        "Unrelated topic.", [RuleJevInput("Global rule", "Require approval.", "", "")]
    ) == [RuleJevScore(applies_when=1.0, exception=0.0)]
    mock_client_class.return_value.post.assert_not_called()
    client.close()
