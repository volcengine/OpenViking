import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
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

function makeTempHome(t) {
  const home = mkdtempSync(join(tmpdir(), "openviking-trae-"));
  t.after(() => rmSync(home, { recursive: true, force: true }));
  return home;
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

test("a pre-existing TRAE home directory keeps TRAE Desktop as the default", (t) => {
  const home = makeTempHome(t);
  mkdirSync(join(home, ".trae"));

  const result = runInstallerPrelude(`
PATH=/usr/bin:/bin
HOME=${JSON.stringify(home)}
refresh_available_harnesses
HAVE_CURSOR=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s:%s\\n' "$HAVE_TRAE" "$SELECTED_HARNESSES"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "1:trae");
});

test("TRAE CLI configuration does not auto-select the TRAE CLI harness", (t) => {
  const home = makeTempHome(t);
  const cliHome = join(home, ".trae", "cli");
  mkdirSync(cliHome, { recursive: true });
  writeFileSync(join(cliHome, "hooks.json"), "{}\n");

  const result = runInstallerPrelude(`
PATH=/usr/bin:/bin
HOME=${JSON.stringify(home)}
refresh_available_harnesses
HAVE_CURSOR=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s\\n' "$SELECTED_HARNESSES"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "trae");
});

test("TraeCode CLI 2.0 command aliases use the Codex-format selection", (t) => {
  const home = makeTempHome(t);
  const cliHome = join(home, ".trae", "cli");
  mkdirSync(cliHome, { recursive: true });
  writeFileSync(join(cliHome, "hooks.json"), "{}\n");
  for (const command of ["traecli", "traex"]) {
    const bin = join(home, `bin-${command}`);
    mkdirSync(bin);
    writeFileSync(join(bin, command), "#!/bin/sh\nexit 0\n");
    chmodSync(join(bin, command), 0o755);

    const result = runInstallerPrelude(`
PATH=${JSON.stringify(`${bin}:/usr/bin:/bin`)}
HOME=${JSON.stringify(home)}
refresh_available_harnesses
TUI_CODEX_BINS="$CODEX_BINS"
add_detected_traecode_cli_alias
refresh_available_harnesses
HAVE_CURSOR=0
tui_reset_bin_selection
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
if tui_bin_detected codex ${command}; then detected=yes; else detected=no; fi
label="$(tui_bin_label codex ${command})"
printf '%s:%s:%s\\n' "$SELECTED_HARNESSES" "$detected" "$label"
`);

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), "codex,trae:yes:TraeCode CLI 2.0", command);
  }
});

test("trae-cli is the public harness and resolves to the Codex plugin internally", (t) => {
  const home = makeTempHome(t);
  const bin = join(home, "bin");
  mkdirSync(bin);
  writeFileSync(join(bin, "trae-cli"), "#!/bin/sh\nexit 0\n");
  chmodSync(join(bin, "trae-cli"), 0o755);

  const result = runInstallerPrelude(`
PATH=${JSON.stringify(`${bin}:/usr/bin:/bin`)}
HOME=${JSON.stringify(home)}
refresh_available_harnesses
TUI_CODEX_BINS="$CODEX_BINS"
INTERACTIVE=0
REQUESTED_HARNESSES="trae-cli"
select_harnesses
printf '%s:%s:%s\\n' "$SELECTED_HARNESSES" "$(list_words "$CODEX_BINS")" "$(tui_bin_label codex trae-cli)"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "codex:trae-cli:TraeCode CLI 2.0");
});
