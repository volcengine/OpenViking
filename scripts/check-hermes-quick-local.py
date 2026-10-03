#!/usr/bin/env python3
"""Run real Quick Local installation, local embeddings and profile lifecycle.

Uses a static test LLM configuration. It does not call a remote LLM; extraction
and interactive setup are separate live release checks.
"""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def wait_for_provider(provider, timeout=180):
    """Exercise retained-provider autostart, never the controller's start path."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if provider._ensure_client() is not None:
            return
        time.sleep(.1)
    raise AssertionError("The retained external provider did not recover its managed server")


def check_llm_transports(home, ql, runtime_python):
    """Resolve persisted Hermes routes and observe real installed OV requests."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    requests = []
    request_headers = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.path)
            request_headers.append(dict(self.headers))
            print("Observed LLM request: " + self.path, flush=True)
            if self.path in {"/v1/chat/completions", "/v1beta/openai/chat/completions"}:
                body = {"id": "chat-test", "object": "chat.completion", "model": "gpt-test",
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "transport-ok"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            elif self.path in {"/anthropic/v1/messages", "/coding/v1/messages"}:
                body = {"id": "msg-test", "type": "message", "role": "assistant", "model": "claude-test",
                        "content": [{"type": "text", "text": "transport-ok"}], "stop_reason": "end_turn",
                        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
            else:
                self.send_error(404)
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    token = set_hermes_home_override(home)
    try:
        home.mkdir(parents=True, exist_ok=True)
        for mode, suffix, model, expected in (
            ("chat_completions", "/v1", "gpt-test", "/v1/chat/completions"),
            ("anthropic_messages", "/anthropic", "claude-test", "/anthropic/v1/messages"),
            ("anthropic_messages", "/anthropic/v1", "claude-test", "/anthropic/v1/messages"),
            ("anthropic_messages", "/anthropic/v1/", "claude-test", "/anthropic/v1/messages"),
            ("chat_completions", "/v1beta", "gemini-test", "/v1beta/openai/chat/completions"),
            ("anthropic_messages", "/coding", "claude-test", "/coding/v1/messages"),
        ):
            base = f"http://127.0.0.1:{server.server_port}{suffix}"
            if suffix == "/v1beta":
                base = "https://generativelanguage.googleapis.com/v1beta"
            elif suffix == "/coding":
                base = "https://api.kimi.com/coding"
            config = {"model": {"provider": "custom:transport", "default": model}, "custom_providers": [
                {"name": "transport", "base_url": base,
                 "api_key": "static-test-key", "api_mode": mode}
            ]}
            (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            vlm = ql.resolve_hermes_vlm_config()
            if suffix in {"/v1beta", "/coding"}:
                from urllib.parse import urlparse

                # Resolve with the real provider host, then use the loopback
                # fixture only for observing the installed backend's request.
                vlm["api_base"] = f"http://127.0.0.1:{server.server_port}" + urlparse(vlm["api_base"]).path
            script = (
                "import json,sys; from openviking.models.vlm import VLMFactory; "
                "v=VLMFactory.create(json.load(sys.stdin)); "
                "assert v.get_completion(prompt='Reply transport-ok')=='transport-ok'"
            )
            count = len(requests)
            subprocess.run([str(runtime_python), "-c", script], input=json.dumps(vlm),
                           text=True, check=True, timeout=120)
            assert requests[count:] == [expected], requests[count:]
            if suffix == "/coding":
                assert request_headers[-1]["User-Agent"].startswith("HermesAgent/")
                assert request_headers[-1]["x-api-key"] == "static-test-key"
        return requests
    finally:
        reset_hermes_home_override(token)
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--legacy-uv", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    host = args.hermes_root.resolve()
    sys.path.insert(0, str(host))
    for key in list(os.environ):
        if key.startswith(("OPENVIKING_", "HERMES_", "__HERMES_")):
            del os.environ[key]
    os.environ.update(
        HERMES_HOME=str(root / "a"),
        HERMES_RUNTIME_DIR=str(root / "host-runtime"),
        HERMES_ENABLE_PROJECT_PLUGINS="0",
    )
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from plugins import memory

    memory._MEMORY_PLUGINS_DIR = root / "empty-bundled"
    source = Path(__file__).resolve().parents[1] / "examples/hermes-plugin"
    servers, providers = [], []
    evidence = {"legacy_uv": args.legacy_uv, "profiles": []}
    llm_requests = []

    class LLMHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path != "/v1/chat/completions" or self.headers.get("Authorization") not in {
                "Bearer isolated-static-test-key", "Bearer updated-isolated-static-key"
            }:
                self.send_error(401)
                return
            llm_requests.append(self.path)
            body = json.dumps({"id": "setup-test", "object": "chat.completion", "model": "test-model",
                               "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    llm_server = ThreadingHTTPServer(("127.0.0.1", 0), LLMHandler)
    llm_worker = threading.Thread(target=llm_server.serve_forever, daemon=True)
    llm_worker.start()
    try:
        for name in ("a", "b"):
            home = root / name
            shutil.copytree(
                source,
                home / "plugins/openviking",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("tests", "__pycache__"),
            )
            config = {
                "model": {"provider": "custom:test", "default": "test-model"},
                "custom_providers": [
                    {
                        "name": "test",
                        "base_url": f"http://127.0.0.1:{llm_server.server_port}/v1",
                        "api_key": "isolated-static-test-key",
                        "api_mode": "chat_completions",
                    }
                ],
                "memory": {"provider": "openviking", "openviking": {
                    "deployment": "quick_local", "use_ovcli_config": True,
                }},
                "plugins": {"enabled": ["openviking"]},
            }
            (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            if os.name != "nt":
                (home / "config.yaml").chmod(0o600)
            token = set_hermes_home_override(home)
            try:
                assert memory.find_provider_dir("openviking") == home / "plugins/openviking"
                provider = memory.load_memory_provider("openviking", register_skills=False)
                assert type(provider).__module__.startswith("_hermes_user_memory.")
                ql = importlib.import_module(type(provider).__module__ + ".quick_local")
                lifecycle = importlib.import_module(type(provider).__module__ + ".local_server")
                if args.legacy_uv:
                    assert not ql._pm_available(), "Use a pre-PM Hermes host for the legacy test"
                server = lifecycle.LocalServer(home)
                servers.append(server)
                providers.append(provider)
                module = importlib.import_module(type(provider).__module__)
                setup = ql.QuickLocalSetup(health_check=module._validate_openviking_reachability)
                setup.provision(hermes_home=home)
                assert len(llm_requests) >= len(evidence["profiles"]) + 1
                before = {path: path.read_bytes() for path in (
                    server.paths.server_config, server.paths.ovcli_config
                )}
                config["custom_providers"][0]["api_key"] = "invalid-static-test-key"
                (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
                try:
                    setup.provision(hermes_home=home)
                except ql.QuickLocalSetupError as error:
                    assert "LLM settings failed the access check" in str(error), str(error)
                else:
                    raise AssertionError("Invalid copied LLM key passed setup")
                finally:
                    config["custom_providers"][0]["api_key"] = "isolated-static-test-key"
                    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
                assert all(path.read_bytes() == data for path, data in before.items())
                assert not server.paths.restart_required_marker.exists()
                if name == "a":
                    evidence["llm_request_paths"] = check_llm_transports(
                        root / "transport-config", ql, server.paths.runtime_python
                    )
                provider.initialize("local-smoke-" + name, hermes_home=str(home), platform="cli")
                assert provider._client is not None
                provider.sync_turn(
                    "A captured local test message",
                    "Acknowledged",
                    session_id="local-smoke-" + name,
                )
                assert provider._drain_writers("local-smoke-" + name, timeout=15)
                session = provider._client.get("/api/v1/sessions/local-smoke-" + name)["result"]
                assert session["pending_tokens"] > 0
                browse = json.loads(
                    provider.handle_tool_call(
                        "viking_browse", {"path": "viking://", "action": "list"}
                    )
                )
                assert not browse.get("error")
                # Keep this provider/session alive through a crash and an
                # explicit stop. Later use must recover without a new agent.
                import psutil

                for action in ("crash", "stop"):
                    old_pid = server.status()["pid"]
                    assert isinstance(old_pid, int), "Managed server must have a verified PID"
                    if action == "crash":
                        process = psutil.Process(old_pid)
                        owned = [*process.children(recursive=True), process]
                        for child in owned:
                            try:
                                child.kill()
                            except psutil.NoSuchProcess:
                                pass
                        psutil.wait_procs(owned, timeout=5)
                    else:
                        server.stop()
                    assert provider._ensure_client() is None
                    wait_for_provider(provider)
                    assert server.status()["pid"] != old_pid
                    assert provider._client.get("/api/v1/sessions/local-smoke-" + name)["result"]["pending_tokens"] > 0
                old_pid = server.status()["pid"]
                updated = server._config()
                updated["vlm"]["api_key"] = "updated-isolated-static-key"
                server.configure(updated)
                server.wait_ready(server.start(), setup._health_check, timeout=120)
                assert server.status()["pid"] != old_pid
                assert (
                    provider._client.get("/api/v1/sessions/local-smoke-" + name)["result"][
                        "pending_tokens"
                    ]
                    > 0
                )
                probe = (
                    "from openviking.models.embedder.local_embedders import LocalDenseEmbedder; import math; e=LocalDenseEmbedder(model_name='bge-small-zh-v1.5-f16',cache_dir="
                    + repr(str(server.paths.model_cache))
                    + "); v=e.embed('local embedding validation').dense_vector; assert len(v)==512 and all(math.isfinite(x) for x in v) and any(v)"
                )
                subprocess.run(
                    [str(server.paths.runtime_python), "-c", probe], check=True, timeout=120
                )
                evidence["profiles"].append(server.status())
            finally:
                reset_hermes_home_override(token)
        assert servers[0].status()["endpoint"] != servers[1].status()["endpoint"]
        # A's saved port is taken by B while A is stopped. A must move and keep
        # its captured session, without stopping or reconfiguring B.
        a, b = servers
        previous = a.status()["endpoint"]
        a.stop()
        config = b._config()
        config["server"]["port"] = b.ql._endpoint_port(previous)
        b.configure(config)
        b.wait_ready(
            b.start(), lambda url: (b.ql.server_belongs_to_profile(b.paths, url), ""), timeout=120
        )
        foreign_pid = b.status()["pid"]
        token = set_hermes_home_override(a.paths.root.parent)
        try:
            assert providers[0]._ensure_client() is None
            wait_for_provider(providers[0])
            assert providers[0]._endpoint != previous and b.status()["pid"] == foreign_pid
            assert providers[0]._client.get("/api/v1/sessions/local-smoke-a")["result"]["pending_tokens"] > 0
        finally:
            reset_hermes_home_override(token)
        evidence.update(
            external_loader=True,
            real_embeddings=True,
            captured_turns=True,
            restart_preserved_data=True,
            foreign_port_recovery=True,
            retained_provider_recovery=True,
            llm_access_validated=True,
            llm_failure_preserved_configuration=True,
        )
        print(json.dumps(evidence))
    finally:
        for provider in providers:
            provider.shutdown()
        for server in reversed(servers):
            if server.paths.server_config.exists():
                server.stop()
        llm_server.shutdown()
        llm_server.server_close()
        llm_worker.join(timeout=5)
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
