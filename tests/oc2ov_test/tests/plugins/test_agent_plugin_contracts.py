"""
Agent plugin contract tests for ovtest.

These tests are intentionally fast and mostly static. They capture the
installation and integration contracts that should hold before running heavier
live OpenCode or Hermes E2E checks.
"""

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class TestOpenCodePluginContract(unittest.TestCase):
    def test_source_install_uses_scanned_js_wrapper(self):
        wrapper = REPO_ROOT / "examples/opencode-plugin/wrappers/openviking.js"
        stale_wrapper = REPO_ROOT / "examples/opencode-plugin/wrappers/openviking.mjs"
        self.assertTrue(wrapper.exists(), "Source install must provide a .js wrapper OpenCode scans")
        self.assertFalse(stale_wrapper.exists(), ".mjs wrapper is not part of the source install contract")
        self.assertIn(
            'export { OpenVikingPlugin, default } from "./openviking/index.mjs"',
            wrapper.read_text(encoding="utf-8"),
        )

        doc_paths = [
            "examples/opencode-plugin/README.md",
            "examples/opencode-plugin/INSTALL.md",
            "examples/opencode-plugin/INSTALL-ZH.md",
            "docs/en/agent-integrations/08-community-plugins.md",
            "docs/zh/agent-integrations/08-community-plugins.md",
            "docs/images/agents/en/opencode.md",
            "docs/images/agents/zh/opencode.md",
        ]
        for path in doc_paths:
            text = read_text(path)
            self.assertIn("openviking.js", text, path)
            self.assertNotIn("openviking.mjs", text, path)

    def test_opencode_plugin_exposes_memory_tools_and_uri_guard(self):
        index = read_text("examples/opencode-plugin/index.mjs")
        memory_tools = read_text("examples/opencode-plugin/lib/memory-tools.mjs")
        guard = read_text("examples/opencode-plugin/lib/viking-uri-guard.mjs")
        package = json.loads(read_text("examples/opencode-plugin/package.json"))

        for tool_name in [
            "memsearch",
            "memread",
            "membrowse",
            "memcommit",
            "memgrep",
            "memglob",
            "memadd",
            "memwrite",
            "memremove",
            "memqueue",
        ]:
            self.assertIn(f"{tool_name}:", memory_tools, tool_name)

        self.assertIn('"tool.execute.before": vikingUriGuard', index)
        self.assertIn("createVikingUriGuard", index)
        self.assertIn('endpoint: "/api/v1/content/write"', memory_tools)
        self.assertIn('mode: args.mode ?? "create"', memory_tools)
        self.assertIn("actorPeerId,", memory_tools)
        self.assertIn("read:", guard)
        self.assertIn("glob:", guard)
        self.assertIn("grep:", guard)
        self.assertIn("viking:// URIs are OpenViking virtual paths", guard)
        self.assertIn("lib/viking-uri-guard.mjs", package["scripts"]["check"])

    def test_opencode_docs_capture_best_practice_validation_flow(self):
        install = read_text("examples/opencode-plugin/INSTALL.md")
        readme = read_text("examples/opencode-plugin/README.md")

        self.assertIn("npm install", install)
        self.assertIn("curl http://localhost:1933/health", install)
        self.assertIn("memwrite", install)
        self.assertIn("memread", install)
        self.assertIn("membrowse", install)
        self.assertIn("memsearch", install)
        self.assertIn("OpenCode's local `read`, `glob`, or `grep` tools", install)
        self.assertIn("OpenCode's local `read`, `glob`, and `grep` tools cannot read", readme)


class TestHermesOpenVikingContract(unittest.TestCase):
    def test_hermes_docs_describe_native_openviking_provider(self):
        doc_pairs = [
            ("docs/en/agent-integrations/05-hermes.md", ["built in", "No plugin to install"]),
            ("docs/zh/agent-integrations/05-hermes.md", ["内置 OpenViking", "无需安装插件"]),
            ("docs/images/agents/en/hermes.md", ["built-in memory provider", "No plugin installation"]),
            ("docs/images/agents/zh/hermes.md", ["内置 OpenViking", "无需安装插件"]),
        ]
        for path, expected_phrases in doc_pairs:
            text = read_text(path)
            for phrase in expected_phrases:
                self.assertIn(phrase, text, f"{path} should mention {phrase!r}")
            self.assertIn("hermes memory setup", text)
            self.assertIn("hermes memory status", text)

    def test_hermes_benchmark_preflight_verifies_openviking_target(self):
        runner = read_text("benchmark/locomo/hermes/run_full_eval.sh")

        self.assertIn("verify_hermes_openviking_target", runner)
        self.assertIn('if [[ "$SUITE_LABEL" != "e2e" ]]', runner)
        self.assertIn("HERMES_URL", runner)
        self.assertIn("OPENVIKING_URL", runner)
        self.assertIn("X-Hermes-Session-Id", runner)
        self.assertIn("/api/v1/sessions/", runner)
        self.assertIn("pending_tokens", runner)
        self.assertIn("message_count", runner)
        self.assertIn("Hermes completed the probe, but the configured OpenViking target did not receive it", runner)
        self.assertIn("requests.delete", runner)

    def test_hermes_eval_modes_and_required_services_are_documented(self):
        readme = read_text("benchmark/locomo/hermes/README.md")

        for suite in ["native", "e2e", "preingest"]:
            self.assertRegex(readme, rf"`{re.escape(suite)}`|--suite {re.escape(suite)}")
        self.assertIn("Hermes gateway running at `HERMES_URL`", readme)
        self.assertIn("OpenViking server running at `OPENVIKING_URL`", readme)
        self.assertIn("The default OpenViking benchmark setup is local and does not require an API key", readme)


class TestOvtestPluginFlow(unittest.TestCase):
    def test_runner_and_docs_expose_plugin_contract_flow(self):
        runner = read_text("tests/oc2ov_test/run_tests.py")
        shell_runner = read_text("tests/oc2ov_test/run.sh")
        docs = read_text("tests/oc2ov_test/README.md")

        self.assertIn('"plugins"', runner)
        self.assertIn("tests.plugins.test_agent_plugin_contracts", runner)
        self.assertIn("--plugins", shell_runner)
        self.assertIn("python run_tests.py --type plugins", shell_runner)
        self.assertIn("插件契约测试", docs)
        self.assertIn("python run_tests.py --type plugins", docs)
        for phrase in [
            "npm run check && npm test",
            "wrappers/openviking.js",
            "/experimental/tool/ids",
            "memwrite",
            "memsearch",
            "codeoutline",
            "HERMES_URL",
            "OPENVIKING_URL",
            "--suite e2e",
        ]:
            self.assertIn(phrase, docs)


if __name__ == "__main__":
    unittest.main()
