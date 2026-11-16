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


@pytest.mark.parametrize("client_type", [RuleJevClient, ScenarioJevClient])
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
@pytest.mark.parametrize("log_payloads", [False, True])
@patch("openviking.service.rule_jev.httpx.Client")
def test_judgment_logs_safe_debug_summaries(
    mock_client_class, monkeypatch, caplog, client_type, level, log_payloads
):
    secret = "private-task private-rule private-response secret-key"
    capture_logger = logging.getLogger("test.judgment.summary")
    monkeypatch.setattr("openviking.service.rule_jev.logger", capture_logger)
    caplog.set_level(level, logger=capture_logger.name)
    response = MagicMock(status_code=200)
    response.json.return_value = {"answers": {}, "untrusted": secret}
    mock_client_class.return_value.post.return_value = response
    client = client_type.from_config(
        JevConfig(provider="typesafe", api_key=secret, log_payloads=log_payloads)
    )

    assert client._request_answers({"state": secret}, item_count=1) == {}
    records = [record for record in caplog.records if record.name == capture_logger.name]
    assert len(records) == (2 if level == logging.DEBUG and log_payloads else 0)
    assert all(record.levelno == logging.DEBUG and record.payload for record in records)
    assert secret not in caplog.text
    assert all(secret not in str(record.__dict__) for record in records)
    client.close()


@pytest.mark.parametrize("client_type", [RuleJevClient, ScenarioJevClient])
@pytest.mark.parametrize("failure", ["http", "exception", "missing_answers"])
@patch("openviking.service.rule_jev.httpx.Client")
def test_judgment_fallback_logs_once_without_sensitive_text(
    mock_client_class, monkeypatch, caplog, client_type, failure
):
    secret = "private-response /private/sensitive/file.md private-peer"
    capture_logger = logging.getLogger("test.judgment.fallback")
    monkeypatch.setattr("openviking.service.rule_jev.logger", capture_logger)
    caplog.set_level(logging.DEBUG, logger=capture_logger.name)
    if failure == "http":
        mock_client_class.return_value.post.return_value = httpx.Response(
            503, text=secret, request=httpx.Request("POST", "https://example.com")
        )
    elif failure == "exception":
        mock_client_class.return_value.post.side_effect = RuntimeError(secret)
    else:
        response = MagicMock(status_code=200)
        response.json.return_value = {"untrusted": secret}
        mock_client_class.return_value.post.return_value = response
    client = client_type.from_config(JevConfig(provider="typesafe", api_key="secret-key"))

    assert client._request_answers({"state": "private-task"}, item_count=1) is None
    records = [record for record in caplog.records if record.name == capture_logger.name]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].degraded is True
    assert records[0].exc_info is None
    assert secret not in caplog.text
    assert secret not in str(records[0].__dict__)
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
