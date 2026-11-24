import logging
from unittest.mock import MagicMock, patch

import httpx
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


@pytest.mark.parametrize(
    "log_payloads,level", [(False, logging.DEBUG), (True, logging.INFO), (True, logging.DEBUG)]
)
@patch("openviking.service.rule_jev.httpx.Client")
def test_judgment_logs_safe_debug_summaries(
    mock_client_class, monkeypatch, caplog, log_payloads, level
):
    task_text = "private-task-sentinel"
    scenario_text = "private-scenario-sentinel"
    response_text = "private-response-sentinel"
    api_key = "private-key-sentinel"
    capture_logger = logging.getLogger("test.judgment.summary")
    monkeypatch.setattr("openviking.service.rule_jev.logger", capture_logger)
    caplog.set_level(level, logger=capture_logger.name)
    mock_client_class.return_value.post.return_value = httpx.Response(
        200,
        json={
            "answers": {"unrelated_0": {"type": "noul", "noul": 0.93}},
            "untrusted": response_text,
        },
        request=httpx.Request("POST", "https://example.com"),
    )
    client = ScenarioJevClient.from_config(
        JevConfig(provider="typesafe", api_key=api_key, log_payloads=log_payloads)
    )
    assert client is not None
    try:
        assert client.judge_scenarios(task_text, [scenario_text]) == [0.93]
    finally:
        client.close()

    records = [record for record in caplog.records if record.name == capture_logger.name]
    assert len(records) == (2 if level == logging.DEBUG and log_payloads else 0)
    for record in records:
        assert record.operation == "ScenarioJev"
        assert record.payload is True
        metadata = str(record.__dict__)
        for sensitive in (task_text, scenario_text, response_text, api_key):
            assert sensitive not in metadata
            assert sensitive not in record.getMessage()


@pytest.mark.parametrize("failure", ["http", "exception", "missing_answers"])
@patch("openviking.service.rule_jev.httpx.Client")
def test_judgment_fallback_logs_once_without_sensitive_text(
    mock_client_class, monkeypatch, caplog, failure
):
    task_text = "private-task-sentinel"
    rule_text = "private-rule-sentinel"
    response_text = "private-response-sentinel"
    api_key = "private-key-sentinel"
    capture_logger = logging.getLogger("test.judgment.fallback")
    monkeypatch.setattr("openviking.service.rule_jev.logger", capture_logger)
    caplog.set_level(logging.DEBUG, logger=capture_logger.name)
    request = httpx.Request("POST", "https://example.com")
    if failure == "http":
        mock_client_class.return_value.post.return_value = httpx.Response(
            503, text=response_text, request=request
        )
    elif failure == "exception":
        mock_client_class.return_value.post.side_effect = RuntimeError(response_text)
    else:
        mock_client_class.return_value.post.return_value = httpx.Response(
            200, json={"untrusted": response_text}, request=request
        )
    client = RuleJevClient.from_config(JevConfig(provider="typesafe", api_key=api_key))
    assert client is not None
    try:
        assert (
            client.judge_rules(task_text, [RuleJevInput(rule_text, rule_text, "Deploy", "")])
            is None
        )
    finally:
        client.close()

    [record] = [record for record in caplog.records if record.name == capture_logger.name]
    assert record.levelno == logging.INFO
    assert record.operation == "RuleJev"
    assert record.degraded is True
    assert record.exc_info is None
    expected_message = {
        "http": "request rejected",
        "exception": "judgment failed",
        "missing_answers": "answers missing",
    }
    assert expected_message[failure] in record.getMessage()
    for sensitive in (task_text, rule_text, response_text, api_key):
        assert sensitive not in record.getMessage()
        assert sensitive not in str(record.__dict__)


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
    assert (
        client.judge_rules(
            "Unrelated topic.",
            [
                RuleJevInput("Global rule", "Require approval.", "", ""),
                RuleJevInput("Another global rule", "Keep the receipt.", " \n", ""),
            ],
        )
        == [RuleJevScore(applies_when=1.0, exception=0.0)] * 2
    )
    mock_client_class.return_value.post.assert_not_called()
    client.close()
