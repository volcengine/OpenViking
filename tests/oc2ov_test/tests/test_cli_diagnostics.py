import subprocess
from unittest.mock import patch

from cli_diagnostics import compact_subprocess_diagnostic
from openclaw_cli_client import OpenClawCLIClient
from openclaw_cli_smoke import main, run_smoke


def test_compact_subprocess_diagnostic_redacts_and_truncates():
    value = "api_key=super-secret Bearer bearer-secret sk-1234567890 " + ("x" * 1000)

    diagnostic = compact_subprocess_diagnostic(value, limit=80)

    assert "super-secret" not in diagnostic
    assert "bearer-secret" not in diagnostic
    assert "sk-1234567890" not in diagnostic
    assert diagnostic.endswith("...<truncated>")


@patch("openclaw_cli_client._wait_for_session_lock_release", return_value=True)
@patch("openclaw_cli_client.subprocess.run")
def test_client_reports_safe_stderr_when_stdout_is_empty(run, _wait):
    run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="token=private-value provider unavailable"
    )

    result = OpenClawCLIClient().send_message("hello")

    assert result["success"] is False
    assert "命令返回空输出" in result["error"]
    assert "token=<redacted>" in result["error"]
    assert "private-value" not in result["error"]


@patch("openclaw_cli_smoke.subprocess.run")
def test_smoke_rejects_empty_stdout_with_safe_diagnostic(run):
    run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="Authorization: hidden-value"
    )

    try:
        run_smoke("test-session")
    except RuntimeError as exc:
        assert "empty stdout" in str(exc)
        assert "Authorization: <redacted>" in str(exc)
        assert "hidden-value" not in str(exc)
    else:
        raise AssertionError("run_smoke should reject empty stdout")


@patch("openclaw_cli_smoke.run_smoke", side_effect=RuntimeError("bounded failure"))
def test_smoke_main_returns_nonzero_and_emits_action_error(_run, capsys):
    assert main(["--session-id", "test-session"]) == 1
    assert capsys.readouterr().out.strip() == "::error::bounded failure"
