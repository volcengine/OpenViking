"""``hermes openviking doctor`` / ``status`` against an injected transport and a temp HERMES_HOME."""

import argparse
import ast
import importlib
import json
from pathlib import Path

import httpx
import pytest

_KEY = "sk-user-0123456789abcdef"


def _cli(module):
    package = module.__name__ if hasattr(module, "__path__") else module.__name__.rpartition(".")[0]
    return importlib.import_module(f"{package}.cli")


def _transport(*, mcp_status=200, health=None, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json=health or {"status": "ok", "healthy": True, "version": "0.3.1"})
        if path == "/api/v1/system/status":
            return httpx.Response(200, json={"status": "ok", "result": {"user": "alice", "account": "acme"}})
        if path == "/mcp":
            return httpx.Response(mcp_status, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handle))


@pytest.fixture
def profile(external_provider):
    home, _provider, module, _settings = external_provider("p1")
    (home / ".env").write_text(
        f"OPENVIKING_ENDPOINT=http://ov.test:1933\nOPENVIKING_API_KEY={_KEY}\n", encoding="utf-8")
    return home, _cli(module)


def test_cli_module_does_not_import_the_provider_at_import_time():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "cli.py").read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all(n.level == 0 for n in top_level if isinstance(n, ast.ImportFrom))
    assert {a.name.split(".")[0] for n in top_level for a in getattr(n, "names", [])} <= {
        "annotations", "json", "Path", "SimpleNamespace", "Any", "Optional"}


def test_register_cli_builds_doctor_and_status(profile):
    _home, cli = profile
    parser = argparse.ArgumentParser()
    cli.register_cli(parser)
    args = parser.parse_args(["doctor", "--offline", "--json"])
    assert (args.openviking_command, args.offline, args.json, args.func) == ("doctor", True, True, cli.openviking_command)
    assert parser.parse_args(["status"]).openviking_command == "status"


def test_doctor_online_reports_identity_mcp_and_masks_the_key(profile):
    home, cli = profile
    seen = []
    report = cli.collect_report(str(home), transport=_transport(seen=seen))
    assert report["ok"], report["problems"]
    cfg = report["config"]
    assert cfg["values"]["endpoint"] == "http://ov.test:1933"
    assert cfg["values"]["api_key"] == "****" + _KEY[-4:]
    assert cfg["sources"]["api_key"] == "env OPENVIKING_API_KEY"
    assert cfg["sources"]["agent"] == "config.yaml memory.openviking"
    assert report["credential_source"] == "env OPENVIKING_API_KEY"
    assert report["server"]["identity"] == "modern" and report["server"]["version"] == "0.3.1"
    assert report["server"]["user"] == "alice" and report["server"]["authenticated"]
    assert report["mcp"]["reachable"]
    assert _KEY not in json.dumps(report)
    anonymous = next(r for r in seen if r.url.path == "/health")
    assert "authorization" not in anonymous.headers
    mcp = next(r for r in seen if r.url.path == "/mcp")
    assert mcp.headers["authorization"] == f"Bearer {_KEY}"


def test_doctor_offline_touches_no_network_and_reports_local_state(profile):
    home, cli = profile
    from_catalog = importlib.import_module(cli.__name__.rpartition(".")[0] + ".core.tool_catalog")
    from_catalog.write_disk_cache(str(home), "http://ov.test:1933", [{"name": "search"}, {"name": "read"}])
    pending = home / "openviking" / "pending_sessions"
    pending.mkdir(parents=True)
    (pending / "s1.gen.json").write_text(json.dumps({"session_id": "s1", "owner_run_id": "r1"}), encoding="utf-8")

    class Refuse:
        def __getattr__(self, name):
            raise AssertionError("network used offline")

    report = cli.collect_report(str(home), offline=True, transport=Refuse())
    assert report["server"] == {"checked": False} and report["mcp"] == {"checked": False}
    assert report["tool_cache"]["state"] == "fresh" and report["tool_cache"]["tools"] == 2
    assert [p["session_id"] for p in report["pending_sessions"]] == ["s1"]
    assert not report["ok"] and "1 session(s) not yet committed" in report["problems"][0]


def test_tool_cache_for_another_endpoint_is_stale(profile):
    home, cli = profile
    catalog = importlib.import_module(cli.__name__.rpartition(".")[0] + ".core.tool_catalog")
    catalog.write_disk_cache(str(home), "http://elsewhere:1933", [{"name": "search"}])
    assert cli.collect_report(str(home), offline=True)["tool_cache"]["state"] == "stale"


def test_doctor_reports_unhealthy_server_and_failed_mcp(profile):
    home, cli = profile
    report = cli.collect_report(str(home), transport=_transport(
        mcp_status=401, health={"status": "ok", "healthy": False, "version": "0.3.1"}))
    assert not report["ok"]
    assert report["server"]["identity"] == "unhealthy" and not report["server"]["authenticated"]
    assert report["mcp"]["status_code"] == 401 and not report["mcp"]["reachable"]
    assert any("unhealthy" in p for p in report["problems"])


def test_command_json_and_status_output(profile, capsys):
    home, cli = profile
    code = cli.openviking_command(argparse.Namespace(openviking_command="doctor", offline=True, json=True),
                                  hermes_home=str(home))
    out = capsys.readouterr().out
    assert code == 0 and json.loads(out)["config"]["values"]["api_key"] == "****" + _KEY[-4:]
    assert _KEY not in out

    code = cli.openviking_command(argparse.Namespace(openviking_command=None, offline=False),
                                  hermes_home=str(home), transport=_transport())
    out = capsys.readouterr().out
    assert code == 0
    assert "connected (user alice, OpenViking 0.3.1)" in out and "http://ov.test:1933" in out
    assert len(out.strip().splitlines()) <= 5

    cli.openviking_command(argparse.Namespace(openviking_command="doctor", offline=True, json=False),
                           hermes_home=str(home))
    out = capsys.readouterr().out
    assert "Server: not checked (offline)" in out and _KEY not in out
