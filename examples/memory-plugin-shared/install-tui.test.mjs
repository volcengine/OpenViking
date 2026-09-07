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
  // The handler restores the cursor on /dev/tty; with no controlling terminal
  // that redirection must not narrate itself ahead of the real diagnostic.
  assert.doesNotMatch(result.stderr, /\/dev\/tty/);
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

test("keeping the stored API key leaves the wizard on its feet", () => {
  const result = runInstallerPrelude(`
tui_menu() { TUI_MENU_CHOICE=1; }
INTERACTIVE=1
exec 3< <(printf '\\n')
prompt_connection "https://example.invalid/openviking" "stored-key"
printf '%s|%s\\n' "$WIZ_URL" "$WIZ_KEY"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(
    result.stdout.trim().split("\n").pop(),
    "https://api.vikingdb.cn-beijing.volces.com/openviking|__OPENVIKING_KEEP__",
  );
});

test("the credentials step writes ovcli.conf when the stored key is kept", (t) => {
  const home = makeTempHome(t);
  const conf = join(home, "ovcli.conf");
  writeFileSync(conf, `${JSON.stringify({ url: "http://127.0.0.1:1933", api_key: "stored-key", output: "table" }, null, 2)}\n`);

  const result = runInstallerPrelude(`
tui_menu() { TUI_MENU_CHOICE=1; }
INTERACTIVE=1
OV_HOME=${JSON.stringify(home)}
OVCLI_CONF=${JSON.stringify(conf)}
exec 3< <(printf '\\n')
configure_ovcli
`);

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(conf, "utf8")), {
    url: "https://api.vikingdb.cn-beijing.volces.com/openviking",
    api_key: "stored-key",
    output: "table",
  });
});

test("menu digit shortcuts move the cursor instead of confirming", () => {
  // Confirming on the digit itself leaves the Enter most users press right
  // after it in the tty buffer, where the next prompt reads it as an empty
  // answer. Driving the real menus needs a pty, so pin the key handlers.
  const menuArm = /\[1-9\]\)([\s\S]*?);;/.exec(installerSource);
  assert.ok(menuArm, "tui_menu no longer has a [1-9] case arm");
  assert.doesNotMatch(menuArm[1], /\bbreak\b/);

  const chooseFormat = /^tui_choose_cli_format\(\) \{$[\s\S]*?^\}$/m.exec(installerSource);
  assert.ok(chooseFormat, "tui_choose_cli_format not found");
  assert.match(chooseFormat[0], /^ +1\) cursor=0 ;;$/m);
  assert.match(chooseFormat[0], /^ +2\) cursor=1 ;;$/m);
});

test("an empty element in a comma-separated list is skipped, not fatal", () => {
  // Assignments, like the call sites: a trailing comma used to make the loop --
  // and so the function -- exit 1, which `set -e` turned into an abort.
  const result = runInstallerPrelude(`
bins="$(normalize_bin_list "claude," claude)"
items="$(split_csv_list ",,seed, claude ,")"
harnesses="$(split_harnesses "claude,,CODEX,")"
printf '%s|%s|%s\\n' "$bins" "$items" "$harnesses"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "claude|seed\nclaude|claude\ncodex");
});

test("the credentials step names the field it changed", (t) => {
  const home = makeTempHome(t);
  const conf = join(home, "ovcli.conf");
  const url = "https://api.vikingdb.cn-beijing.volces.com/openviking";
  writeFileSync(conf, `${JSON.stringify({ url, api_key: "stored-key" }, null, 2)}\n`);

  const result = runInstallerPrelude(`
tui_menu() { TUI_MENU_CHOICE=1; }
INTERACTIVE=1
OV_HOME=${JSON.stringify(home)}
OVCLI_CONF=${JSON.stringify(conf)}
exec 3< <(printf 'rotated-key\\n')
configure_ovcli
`);

  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stdout, /Updated: url:/);
  assert.match(result.stdout, /Updated: api_key: stor…-key \(10\) -> rota…-key \(11\)/);
  assert.equal(JSON.parse(readFileSync(conf, "utf8")).api_key, "rotated-key");
});
