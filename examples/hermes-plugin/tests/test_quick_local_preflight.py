"""LLM preflight retries and diagnostics through the external package."""

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
def ql(external_provider):
    _home, _provider, module, _settings = external_provider("preflight")
    return importlib.import_module(module.__name__ + ".quick_local")


@pytest.mark.parametrize("status,name,retries", [
    (401, "AuthenticationError", False),
    (403, "PermissionDeniedError", False),
    (404, "NotFoundError", False),
    (429, "RateLimitError", True),
    (500, "InternalServerError", True),
])
def test_preflight_script_retries_only_transient_errors(ql, tmp_path, monkeypatch, status, name, retries):
    import openai

    error = getattr(openai, name)(
        "secret response body", response=httpx.Response(status, request=httpx.Request("POST", "https://test")),
        body=None,
    )
    error.__context__ = httpx.HTTPStatusError(
        "secret response body", request=error.request, response=error.response,
    )
    error.__suppress_context__ = True  # The OpenAI SDK raises status errors from None.
    _check_script(ql, tmp_path, monkeypatch, error, retries, succeeds=False)


@pytest.mark.parametrize("error,retries", [
    (httpx.ReadTimeout("secret timeout text"), True),
    (TimeoutError("secret timeout text"), True),
    (ValueError("secret error text"), False),
])
def test_preflight_script_timeout_and_other_errors(ql, tmp_path, monkeypatch, error, retries):
    _check_script(ql, tmp_path, monkeypatch, error, retries)


def test_preflight_does_not_retry_connection_errors(ql, tmp_path, monkeypatch):
    import openai

    _check_script(ql, tmp_path, monkeypatch,
                  openai.APIConnectionError(request=httpx.Request("POST", "https://test")), False)


def test_preflight_sdk_timeout_stops_after_one_retry(ql, tmp_path, monkeypatch):
    import openai

    _check_script(ql, tmp_path, monkeypatch,
                  openai.APITimeoutError(request=httpx.Request("POST", "https://test")), True,
                  succeeds=False)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_wrapped_http_status_controls_retry_and_guidance(ql, tmp_path, monkeypatch, status):
    import openai

    request = httpx.Request("POST", "https://test")
    # LiteLLM can mask an Anthropic HTTP 403 as APIConnectionError(status=500).
    error = openai.APIConnectionError(request=request)
    error.status_code = 500
    error.__context__ = httpx.HTTPStatusError(
        "secret response body mentioning HTTP 500", request=request,
        response=httpx.Response(status, request=request),
    )
    _check_script(ql, tmp_path, monkeypatch, error, status in {429, 500, 503},
                  succeeds=False, expected=f"HTTP {status}")


@pytest.mark.parametrize("cause,retries,expected", [
    (httpx.ConnectError("secret endpoint"), False, "endpoint and network"),
    (httpx.ReadTimeout("secret endpoint"), True, "timed out"),
    (TimeoutError("secret endpoint"), True, "timed out"),
])
def test_wrapped_transport_errors_ignore_synthetic_500(
    ql, tmp_path, monkeypatch, cause, retries, expected,
):
    import openai

    request = httpx.Request("POST", "https://test")
    error = openai.InternalServerError(
        "secret wrapper", response=httpx.Response(500, request=request), body=None,
    )
    error.__cause__ = cause
    # Explicit causes take precedence over a different, implicit context.
    error.__context__ = openai.AuthenticationError(
        "secret earlier error", response=httpx.Response(401, request=request), body=None,
    )
    message = _check_script(ql, tmp_path, monkeypatch, error, retries,
                            succeeds=False, expected=expected)
    assert "HTTP 500" not in message


def test_cyclic_error_context_does_not_hang(ql, tmp_path, monkeypatch):
    import openai

    error = openai.APIConnectionError(request=httpx.Request("POST", "https://test"))
    error.status_code = 500
    error.__context__ = error
    _check_script(ql, tmp_path, monkeypatch, error, False, expected="endpoint and network")


def test_current_http_response_wins_over_earlier_context(ql, tmp_path, monkeypatch):
    request = httpx.Request("POST", "https://test")
    error = httpx.HTTPStatusError(
        "secret current response", request=request, response=httpx.Response(503, request=request),
    )
    error.__context__ = httpx.HTTPStatusError(
        "secret earlier response", request=request, response=httpx.Response(401, request=request),
    )
    _check_script(ql, tmp_path, monkeypatch, error, True, succeeds=False, expected="HTTP 503")


def _check_script(ql, tmp_path, monkeypatch, error, retries, succeeds=True, expected=None):
    """Execute the shipped child script, replacing only the LLM backend."""
    paths = ql.managed_paths(tmp_path)
    config = tmp_path / "check.json"
    config.write_text(json.dumps({"vlm": {"model": "test"}}), encoding="utf-8")
    vlm = MagicMock()
    vlm.get_completion.side_effect = [error, "OK" if succeeds else error]
    factory = MagicMock()
    factory.create.return_value = vlm
    backend = types.ModuleType("openviking.models.vlm")
    backend.VLMFactory = factory
    monkeypatch.setitem(sys.modules, "openviking.models.vlm", backend)
    monkeypatch.setattr(sys, "argv", ["check", str(config)])
    import time

    sleep = MagicMock()
    monkeypatch.setattr(time, "sleep", sleep)

    def run(command, **_kwargs):
        output = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(output):
            try:
                exec(compile(command[2], "<llm-preflight>", "exec"), {})
            except SystemExit as exc:
                code = exc.code
        return subprocess.CompletedProcess(command, code, stdout=output.getvalue(), stderr="secret stderr")

    monkeypatch.setattr(ql.subprocess, "run", run)
    message = ""
    if retries and succeeds:
        ql._validate_vlm(paths, config)
    else:
        with pytest.raises(ql.QuickLocalSetupError) as caught:
            ql._validate_vlm(paths, config)
        assert "secret" not in str(caught.value)
        message = str(caught.value)
        log = (tmp_path / "logs/openviking-server.log").read_text()
        assert "secret" not in log
        assert type(error).__name__ in message and type(error).__name__ in log
        if expected:
            assert expected in message
    assert vlm.get_completion.call_count == (2 if retries else 1)
    if retries:
        sleep.assert_called_once_with(1)
    else:
        sleep.assert_not_called()
    return message


@pytest.mark.parametrize("failure,expected", [
    ({"error": "AuthenticationError", "status": 401}, "API key and its permissions"),
    ({"error": "NotFoundError", "status": 404}, "model and endpoint"),
    ({"error": "RateLimitError", "status": 429, "attempts": 2}, "rate-limiting"),
    ({"error": "InternalServerError", "status": 500, "attempts": 2}, "temporary server error"),
    ({"error": "APITimeoutError", "timeout": True, "attempts": 2}, "timed out"),
])
def test_failure_metadata_is_actionable_and_private(ql, tmp_path, monkeypatch, failure, expected):
    result = subprocess.CompletedProcess("check", 1,
                                         stdout="import notice\n" + json.dumps(failure),
                                         stderr="Bearer secret credential")
    monkeypatch.setattr(ql.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(ql.QuickLocalSetupError, match=expected) as caught:
        ql._validate_vlm(ql.managed_paths(tmp_path), tmp_path / "check.json")
    log = tmp_path / "logs/openviking-server.log"
    assert failure["error"] in str(caught.value) and failure["error"] in log.read_text()
    assert "secret" not in str(caught.value) and "secret" not in log.read_text()
    if failure.get("status"):
        assert f"HTTP {failure['status']}" in str(caught.value)
    if os.name != "nt":
        assert log.stat().st_mode & 0o777 == 0o600


def test_unstructured_failure_never_exposes_child_output(ql, tmp_path, monkeypatch):
    result = subprocess.CompletedProcess("check", 1, stdout="secret credential", stderr="secret response")
    monkeypatch.setattr(ql.subprocess, "run", lambda *_args, **_kwargs: result)
    with pytest.raises(ql.QuickLocalSetupError, match="UnknownError") as caught:
        ql._validate_vlm(ql.managed_paths(tmp_path), tmp_path / "check.json")
    assert "secret" not in str(caught.value)
    assert "secret" not in (tmp_path / "logs/openviking-server.log").read_text()
