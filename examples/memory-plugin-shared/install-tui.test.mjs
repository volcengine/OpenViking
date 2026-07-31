import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const installer = fileURLToPath(new URL("./install.sh", import.meta.url));
const installerSource = readFileSync(installer, "utf8");
const mainMarker = "# ---------------------------------------------------------------------------\n# Main\n";
const installerPrelude = installerSource.slice(0, installerSource.indexOf(mainMarker));

function runInstallerPrelude(body) {
  return spawnSync("/bin/bash", [], {
    encoding: "utf8",
    env: { ...process.env, OPENVIKING_LANG: "en" },
    input: `${installerPrelude}\n${body}\n`,
    timeout: 10_000,
  });
}

test("finishing TUI selection succeeds when the final harness is not selected", () => {
  const result = runInstallerPrelude(`
SEL_CLAUDE_BINS=claude
SEL_CODEX_BINS=codex
SEL_OPENCODE=1
SEL_PI=1
SEL_CURSOR_APP=1
SEL_TRAE=1
SEL_TRAE_CN=0
tui_finish_selection
printf '%s\\n' "$SELECTED_HARNESSES"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "claude,codex,opencode,pi,cursor,trae");
});

test("unexpected installer failures include actionable diagnostics", () => {
  const result = runInstallerPrelude(`
installer_test_failure() {
  false
}
installer_test_failure
`);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /OpenViking installer stopped unexpectedly\./);
  assert.match(result.stderr, /Exit status: 1/);
  assert.match(result.stderr, /Script line: [0-9]+/);
  assert.match(result.stderr, /Command: false/);
});
