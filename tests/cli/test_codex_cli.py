from pathlib import Path
from unittest.mock import patch

from openviking_cli.codex_cli import main


@patch("openviking_cli.codex_cli.resolve_codex_runtime_credentials")
@patch("openviking_cli.codex_cli.get_codex_auth_status")
def test_codex_status_json(mock_status, mock_resolve, capsys):
    mock_status.return_value = {
        "store_path": "/tmp/.openviking/codex_auth.json",
        "store_exists": True,
        "bootstrap_path": "/tmp/.codex/auth.json",
        "bootstrap_available": True,
        "env_override": False,
        "provider": "openai-codex",
        "expires_at": "2026-04-14T00:00:00Z",
    }
    mock_resolve.return_value = {
        "source": "openviking",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }

    code = main(["status", "--json"])
    out = capsys.readouterr().out

    assert code == 0
    assert '"ready": true' in out
    assert '"active_source": "openviking"' in out


@patch("openviking_cli.codex_cli.bootstrap_codex_auth")
@patch("openviking_cli.codex_cli.resolve_codex_runtime_credentials", side_effect=RuntimeError("missing"))
def test_codex_login_bootstraps_when_available(_mock_resolve, mock_bootstrap, capsys):
    mock_bootstrap.return_value = Path("/tmp/.openviking/codex_auth.json")

    code = main(["login"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Imported Codex OAuth into the OV auth store." in out


@patch("openviking_cli.codex_cli.bootstrap_codex_auth", return_value=None)
@patch("openviking_cli.codex_cli.login_codex_with_device_code")
@patch("openviking_cli.codex_cli.resolve_codex_runtime_credentials", side_effect=RuntimeError("missing"))
def test_codex_login_uses_device_flow_when_bootstrap_missing(
    _mock_resolve,
    mock_device_login,
    _mock_bootstrap,
    capsys,
):
    mock_device_login.return_value = Path("/tmp/.openviking/codex_auth.json")

    code = main(["login", "--device-only"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Codex OAuth login successful." in out


@patch("openviking_cli.codex_cli.delete_codex_auth_store", return_value=True)
def test_codex_logout_yes(mock_delete, capsys):
    code = main(["logout", "--yes"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Deleted OV Codex auth state." in out
    mock_delete.assert_called_once()
