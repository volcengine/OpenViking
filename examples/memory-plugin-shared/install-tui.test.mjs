import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const installer = fileURLToPath(new URL("./install.sh", import.meta.url));
const installerSource = readFileSync(installer, "utf8");
const mainMarker = "# ---------------------------------------------------------------------------\n# Main\n";
const installerPrelude = installerSource.slice(0, installerSource.indexOf(mainMarker));

// An `undefined` value removes the variable from the child's environment.
function preludeEnv(env) {
  const merged = { ...process.env, OPENVIKING_LANG: "en", ...env };
  for (const [key, value] of Object.entries(merged)) if (value === undefined) delete merged[key];
  return merged;
}

function runInstallerPrelude(body, env = {}) {
  return spawnSync("/bin/bash", [], {
    encoding: "utf8",
    env: preludeEnv(env),
    input: `${installerPrelude}\n${body}\n`,
    timeout: 10_000,
  });
}

// For bodies that talk to a server in this process, which spawnSync would
// block.
function runInstallerPreludeAsync(body, env = {}) {
  return new Promise((resolve) => {
    const child = spawn("/bin/bash", [], { env: preludeEnv(env) });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => resolve({ status, stdout, stderr }));
    child.stdin.end(`${installerPrelude}\n${body}\n`);
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
SEL_KIMICODE=0
SEL_CODEBUDDY=0
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
HAVE_TRAE_CN=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s:%s\\n' "$HAVE_TRAE" "$SELECTED_HARNESSES"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "1:trae,cli");
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
HAVE_TRAE_CN=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s\\n' "$SELECTED_HARNESSES"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), "trae,cli");
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
HAVE_TRAE_CN=0
tui_reset_bin_selection
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
if tui_bin_detected codex ${command}; then detected=yes; else detected=no; fi
label="$(tui_bin_label codex ${command})"
printf '%s:%s:%s\\n' "$SELECTED_HARNESSES" "$detected" "$label"
`);

    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), "codex,trae,cli:yes:TraeCode CLI 2.0", command);
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

test("choosing the cloud service says where to get an API key", () => {
  const result = runInstallerPrelude(`
tui_menu() { TUI_MENU_CHOICE=1; }
INTERACTIVE=1
exec 3< <(printf 'cloud-key\\n')
prompt_connection "" ""
printf '%s|%s\\n' "$WIZ_URL" "$WIZ_KEY"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /User Management → API Key/);
  assert.match(result.stdout, /https:\/\/console\.volcengine\.com\/vikingdb\/openviking\//);
  assert.equal(result.stdout.trim().split("\n").pop(), "https://api.vikingdb.cn-beijing.volces.com/openviking|cloud-key");
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
gather_credentials
write_ovcli
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

test("q at the install confirmation cancels rather than taking the default", () => {
  // Every other menu keeps its default on q; here the default is Proceed.
  const menu = /^tui_menu\(\) \{[\s\S]*?^\}$/m.exec(installerSource);
  assert.ok(menu, "tui_menu not found");
  assert.match(menu[0], /^ +q\|Q\) cursor="\$\{TUI_MENU_QUIT:-\$def\}"; break ;;$/m);
  const confirm = /^confirm_plan\(\) \{$[\s\S]*?^\}$/m.exec(installerSource);
  assert.ok(confirm, "confirm_plan not found");
  assert.match(confirm[0], /TUI_MENU_QUIT=1 tui_menu "[^\n]*" 0 "\$\(t 'Proceed' '继续'\)" "\$\(t 'Cancel' '取消'\)"$/m);
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
gather_credentials
write_ovcli
`);

  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stdout, /Updated: url:/);
  assert.match(result.stdout, /Updated: api_key: stor…-key \(10\) -> rota…-key \(11\)/);
  assert.equal(JSON.parse(readFileSync(conf, "utf8")).api_key, "rotated-key");
});

test("the API key reaches ovcli.conf without passing through a process argument", (t) => {
  const home = makeTempHome(t);
  const bin = join(home, "bin");
  mkdirSync(bin);
  const log = join(home, "node.log");
  writeFileSync(join(bin, "node"), `#!/bin/sh\necho "$*" >> "${log}"\nexec "${process.execPath}" "$@"\n`, { mode: 0o755 });
  const conf = join(home, "ovcli.conf");

  const result = runInstallerPrelude(`
OV_HOME=${JSON.stringify(home)}
OVCLI_CONF=${JSON.stringify(conf)}
CRED_URL="https://example.invalid/openviking"
CRED_KEY="secret-api-key"
CRED_ACCOUNT="acct"
CRED_USER="usr"
write_ovcli
`, { PATH: `${bin}:${process.env.PATH}` });

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(conf, "utf8")), {
    url: "https://example.invalid/openviking",
    api_key: "secret-api-key",
    account: "acct",
    user: "usr",
  });
  const calls = readFileSync(log, "utf8");
  assert.match(calls, /ovcli\.conf/);
  assert.doesNotMatch(calls, /secret-api-key/);
});

test("a first-time setup offers the local server first", () => {
  const result = runInstallerPrelude(`
tui_menu() { TUI_MENU_CHOICE="$2"; }
INTERACTIVE=1
exec 3< <(printf '\\n')
prompt_connection "" ""
printf '%s|%s\\n' "$WIZ_URL" "$WIZ_KEY"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim().split("\n").pop(), "http://127.0.0.1:1933|");
});

test("without a terminal, the language follows --lang, OPENVIKING_LANG, the locale, then the macOS setting", (t) => {
  const fake = makeTempHome(t);
  writeFileSync(join(fake, "uname"), '#!/bin/sh\nprintf \'%s\\n\' "$FAKE_UNAME"\n', { mode: 0o755 });
  writeFileSync(join(fake, "defaults"), [
    "#!/bin/sh",
    '[ "$*" = "read -g AppleLocale" ] && [ -n "$FAKE_APPLE_LOCALE" ] || exit 1',
    'printf \'%s\\n\' "$FAKE_APPLE_LOCALE"',
    "",
  ].join("\n"), { mode: 0o755 });
  const detect = (env, langArg = "") => {
    const result = runInstallerPrelude(`
INTERACTIVE=0
LANG_ARG=${JSON.stringify(langArg)}
select_language
printf '%s\\n' "$UI_LANG"
`, {
      OPENVIKING_LANG: undefined,
      LC_ALL: undefined,
      LANG: undefined,
      FAKE_UNAME: "Darwin",
      FAKE_APPLE_LOCALE: "zh_CN",
      PATH: `${fake}:/usr/bin:/bin`,
      ...env,
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };

  assert.equal(detect({ OPENVIKING_LANG: "zh" }, "en"), "en");
  assert.equal(detect({ OPENVIKING_LANG: "zh", LC_ALL: "en_US.UTF-8" }), "zh");
  assert.equal(detect({ OPENVIKING_LANG: "en", LANG: "zh_CN.UTF-8" }), "en");
  assert.equal(detect({ LC_ALL: "zh_CN.UTF-8", LANG: "en_US.UTF-8" }), "zh");
  assert.equal(detect({ LANG: "zh_TW.UTF-8" }), "zh");
  assert.equal(detect({ LANG: "en_US.UTF-8" }), "en");
  assert.equal(detect({ LANG: "C" }), "zh");
  assert.equal(detect({ LC_ALL: "POSIX" }), "zh");
  assert.equal(detect({ FAKE_APPLE_LOCALE: "zh-Hans_CN" }), "zh");
  assert.equal(detect({ FAKE_APPLE_LOCALE: "" }), "en");
  assert.equal(detect({ FAKE_UNAME: "Linux" }), "en");
});

test("the server check tells an unreachable server, a rejected key and other failures apart", async (t) => {
  const key = 'good"key\\';
  const seen = [];
  const server = createServer((req, res) => {
    const [, scenario, ...rest] = req.url.split("/");
    seen.push({ path: `/${rest.join("/")}`, headers: req.headers });
    let status = 200;
    if (rest.join("/") === "api/v1/system/status") {
      if (scenario === "authed") status = req.headers.authorization === `Bearer ${key}` ? 200 : 401;
      if (scenario === "broken") status = 500;
    }
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;
  const closed = createServer();
  await new Promise((resolve) => closed.listen(0, "127.0.0.1", resolve));
  const unreachable = `http://127.0.0.1:${closed.address().port}`;
  await new Promise((resolve) => closed.close(resolve));

  // Every curl call is logged, so the key can be checked to stay out of argv.
  const bin = makeTempHome(t);
  const log = join(bin, "curl.log");
  const realCurl = spawnSync("bash", ["-c", "command -v curl"], { encoding: "utf8" }).stdout.trim();
  writeFileSync(join(bin, "curl"), `#!/bin/sh\necho "$*" >> "${log}"\nexec "${realCurl}" "$@"\n`, { mode: 0o755 });

  const result = await runInstallerPreludeAsync(`
for target in "${base}/open||" "${base}/authed|$PROBE_KEY|acct" "${base}/authed|wrong|" "${base}/authed||" "${base}/broken||" "${unreachable}||"; do
  IFS='|' read -r url key account <<PROBE
$target
PROBE
  probe_server "$url/" "$key" "$account" "usr"
  printf '%s:%s\\n' "$PROBE_RESULT" "$PROBE_CODE"
done
`, { PROBE_KEY: key, PATH: `${bin}:${process.env.PATH}` });

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split("\n"), [
    "ok:200",
    "ok:200",
    "rejected:401",
    "key-required:401",
    "http:500",
    "unreachable:000",
  ]);
  const authed = seen.find((req) => req.headers.authorization === `Bearer ${key}`);
  assert.equal(authed.headers["x-openviking-account"], "acct");
  assert.equal(authed.headers["x-openviking-user"], "usr");
  assert.ok(seen.filter((req) => req.path === "/health").every((req) => !req.headers.authorization));
  assert.doesNotMatch(readFileSync(log, "utf8"), /good|wrong/);
});

test("re-entering after a failed server check probes the new values", async (t) => {
  const server = createServer((req, res) => {
    res.writeHead(req.headers.authorization === "Bearer fresh-key" || req.url.endsWith("/health") ? 200 : 401);
    res.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const url = `http://127.0.0.1:${server.address().port}`;

  // First answer: re-enter; then the custom-URL arm, whose prompt keeps the URL.
  const result = await runInstallerPreludeAsync(`
MENU_ANSWERS="0 2"
tui_menu() { set -- $MENU_ANSWERS; TUI_MENU_CHOICE="$1"; shift; MENU_ANSWERS="$*"; }
INTERACTIVE=1
exec 3< <(printf '\\nfresh-key\\n')
CRED_URL=${JSON.stringify(url)}
CRED_KEY="stale-key"
check_server
printf '%s|%s|%s\\n' "$PROBE_RESULT" "$CRED_URL" "$CRED_KEY"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /The server rejected the API key \(HTTP 401\)\./);
  assert.equal(result.stdout.trim().split("\n").pop(), `ok|${url}|fresh-key`);
});

test("the onboarding guide is linked on the docs host the downloads came from, in the UI language", () => {
  const cases = [
    ["https://docs.openviking.ai/dl", "en", "https://docs.openviking.ai/agents/en/onboarding.md"],
    ["https://docs.openviking.net/dl", "en", "https://docs.openviking.net/agents/en/onboarding.md"],
    ["https://docs.openviking.net/dl", "zh", "https://docs.openviking.net/agents/zh/onboarding.md"],
    ["https://docs.openviking.ai/dl", "zh", "https://docs.openviking.ai/agents/zh/onboarding.md"],
    // A mirror or a dev checkout names neither host: Chinese goes to the .net mirror.
    ["https://mirror.example.com/dl", "en", "https://docs.openviking.ai/agents/en/onboarding.md"],
    ["", "zh", "https://docs.openviking.net/agents/zh/onboarding.md"],
    // A look-alike host is not the docs site.
    ["https://docs.openviking.net.example.com/dl", "en", "https://docs.openviking.ai/agents/en/onboarding.md"],
  ];
  const body = cases.map(([base, lang]) => `DOWNLOAD_BASE=${JSON.stringify(base)}; UI_LANG=${lang}; onboarding_url`).join("\n");
  const result = runInstallerPrelude(body);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split("\n"), cases.map(([, , url]) => url));
});

// A copy away from the checkout, so the run is not in dev mode and the
// release installer is not fetched.
function detachedInstaller(t, transform = (source) => source) {
  const dir = mkdtempSync(join(realpathSync(tmpdir()), "openviking-flow-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const home = join(dir, "home");
  const bin = join(home, "bin");
  mkdirSync(bin, { recursive: true });
  const script = join(dir, "install.sh");
  writeFileSync(script, transform(installerSource));
  const dshLog = join(dir, "dsh.log");
  writeFileSync(join(bin, "dsh"), `#!/bin/sh\necho "$*" >> "${dshLog}"\necho "@openviking/dsh-memory-plugin openviking-memory-runtime"\n`, { mode: 0o755 });
  // Nothing here downloads, and a curl that cannot connect keeps the server
  // check away from whatever listens on the default port.
  writeFileSync(join(bin, "curl"), "#!/bin/sh\nexit 7\n", { mode: 0o755 });
  const run = (args, env = {}) => spawnSync("bash", [script, ...args], {
    cwd: home,
    encoding: "utf8",
    env: {
      ...process.env,
      HOME: home,
      PATH: `${bin}:${process.env.PATH}`,
      OPENVIKING_HOME: join(home, ".openviking"),
      OPENVIKING_DOWNLOAD_BASE: "https://tos.example.invalid",
      OPENVIKING_INSTALLER_REEXEC: "0",
      OPENVIKING_SKIP_VERSION_CHECK: "1",
      ...env,
    },
    timeout: 30_000,
  });
  return { home, dshLog, run };
}

test("cancelling at the confirmation leaves the machine untouched", (t) => {
  // Drive the interactive flow without a terminal: every menu takes its
  // default except the confirmation, which is cancelled.
  const { home, dshLog, run } = detachedInstaller(t, (source) => source.replace(mainMarker, `INTERACTIVE=1
exec 3</dev/null
tui_menu() {
  TUI_MENU_CHOICE="$2"
  case "$1" in *Proceed*) TUI_MENU_CHOICE=1 ;; esac
}
${mainMarker}`));

  const result = run(["--harness", "cursor,dsh", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "secret-api-key"]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /\[2\/5\] Review/);
  assert.match(result.stdout, /~\/\.cursor\/hooks\.json/);
  assert.match(result.stdout, /Shared hook runtime: ~\/\.openviking\/agent-integrations\/memory-plugin-shared\n/);
  assert.match(result.stdout, /Cancelled; nothing was changed\./);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /secret-api-key/);
  assert.deepEqual(readdirSync(home), ["bin"]);
  assert.equal(existsSync(dshLog), false);
});

test("an interactive run without --lang asks for the language first", (t) => {
  const { run } = detachedInstaller(t, (source) => source.replace(mainMarker, `INTERACTIVE=1
unset OPENVIKING_LANG
exec 3</dev/null
tui_menu() {
  printf 'menu: %s\\n' "$1"
  TUI_MENU_CHOICE="$2"
  case "$1" in Language*) TUI_MENU_CHOICE=1 ;; 开始安装*) TUI_MENU_CHOICE=1 ;; esac
}
${mainMarker}`));

  const result = run(["--harness", "cursor", "--url", "http://127.0.0.1:9", "--api-key", ""]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /^menu: Language \/ 语言\n/);
  assert.match(result.stdout, /已取消，未做任何修改。/);
});

test("the statusline is asked about before the review, which lists it only when chosen", (t) => {
  for (const [answer, listed] of [
    [0, /~\/\.claude\/settings\.json \(marketplace auto-update, statusline\)\n/],
    [1, /~\/\.claude\/settings\.json \(marketplace auto-update\)\n/],
  ]) {
    const { home, run } = detachedInstaller(t, (source) => source.replace(mainMarker, `INTERACTIVE=1
exec 3</dev/null
tui_menu() {
  printf 'menu: %s\\n' "$1"
  TUI_MENU_CHOICE="$2"
  case "$1" in *statusline*) TUI_MENU_CHOICE=${answer} ;; *Proceed*) TUI_MENU_CHOICE=1 ;; esac
}
${mainMarker}`));
    writeFileSync(join(home, "bin", "claude"), '#!/bin/sh\n[ "$1" != --version ] || echo "2.1.224 (Claude Code)"\n', { mode: 0o755 });

    const result = run(["--harness", "claude", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", ""]);

    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const asked = result.stdout.indexOf("menu: Enable the OpenViking statusline?");
    assert.ok(asked !== -1 && asked < result.stdout.indexOf("] Review"), result.stdout);
    assert.match(result.stdout, listed);
    assert.match(result.stdout, /Cancelled; nothing was changed\./);
  }
});

test("a non-interactive run with no server says so and how to re-run", (t) => {
  const { home, run } = detachedInstaller(t);
  const notice = /No server was given: the plugin is configured for http:\/\/127\.0\.0\.1:1933 without an API key\./;

  const result = run(["--harness", "dsh", "--lang", "en", "--yes"]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, notice);
  assert.ok(result.stdout.indexOf("Review") < result.stdout.search(notice));
  assert.match(result.stdout, /curl -fsSL https:\/\/openviking\.ai\/install \| bash -s -- --yes --harness dsh --url <server-url> --api-key <api-key>/);
  assert.match(result.stdout, /\[4\/4\] Validation/);
  assert.match(result.stdout, /DeepSeek Harness\n {4}Next: start a new dsh session: dsh --profile web\n {4}Updates: re-run this installer\n {4}Uninstall: dsh plugin --profile web rm @openviking\/dsh-memory-plugin/);
  assert.equal(JSON.parse(readFileSync(join(home, ".openviking", "ovcli.conf"), "utf8")).url, "http://127.0.0.1:1933");
  // The unreachable server is repeated at the end, then where the agent goes next.
  assert.match(result.stdout, /!! {2}Not done:\n {6}- Cannot reach http:\/\/127\.0\.0\.1:1933\. If the server is not running yet, start it before using the plugin\.\n==> Onboarding guide \(for your agent\): https:\/\/docs\.openviking\.ai\/agents\/en\/onboarding\.md\n$/);

  // Once a server is configured, a re-run has nothing to warn about.
  const again = run(["--harness", "dsh", "--lang", "en", "--yes"]);
  assert.equal(again.status, 0, `${again.stdout}\n${again.stderr}`);
  assert.doesNotMatch(again.stdout, notice);
});

test("what was asked for but not installed is listed again just before the onboarding link", (t) => {
  const { run } = detachedInstaller(t);

  // pi is selected but not on PATH; dsh is.
  const result = run(["--harness", "dsh,pi", "--lang", "en", "--yes"]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const tail = result.stdout.slice(result.stdout.lastIndexOf("Not done:"));
  assert.match(tail, /^Not done:\n {6}- Cannot reach [^\n]+\n {6}- pi CLI not found; skipping pi extension install\.\n==> Onboarding guide \(for your agent\): \S+\n$/);

  const zh = run(["--harness", "dsh,pi", "--lang", "zh", "--yes"]);
  assert.equal(zh.status, 0, `${zh.stdout}\n${zh.stderr}`);
  assert.match(zh.stdout, /未完成：\n {6}- 无法连接[^\n]+\n {6}- 未找到 pi 命令，跳过 pi 扩展安装。\n==> 接入引导（给 Agent 看）： https:\/\/docs\.openviking\.net\/agents\/zh\/onboarding\.md\n$/);
});

test("run from inside a dsh session, the plugin goes into that session's profile", (t) => {
  const { dshLog, run } = detachedInstaller(t);

  const result = run(["--harness", "dsh", "--lang", "en", "--yes"], { DSH_SHELL: "1", DSH_PROFILE: "tui" });

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(readFileSync(dshLog, "utf8"), /^plugin --profile tui add /m);
  assert.match(result.stdout, /Next: start a new dsh session: dsh --profile tui\n/);

  // --dsh-profile still wins, and DSH_PROFILE alone (outside a dsh shell) is not a session.
  const chosen = run(["--harness", "dsh", "--lang", "en", "--yes", "--dsh-profile", "headless"], { DSH_SHELL: "1", DSH_PROFILE: "tui" });
  assert.match(chosen.stdout, /Next: start a new dsh session: dsh --profile headless\n/);
  const outside = run(["--harness", "dsh", "--lang", "en", "--yes"], { DSH_PROFILE: "tui" });
  assert.match(outside.stdout, /Next: start a new dsh session: dsh --profile web\n/);
});

// DeepSeek Harness Desktop's launcher inside a fake app bundle. It behaves
// like the real one for the calls the installer makes: every boot of the
// desktop profile, --dump-config included, is refused, and so is a plugin
// command before Desktop has created its profile.
const desktopLauncher = "DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh";
// The installer looks in /Applications before ~/Applications, so on a Mac with
// Desktop installed these tests would find (and run) the real app instead.
const realDesktop = process.platform === "darwin" && existsSync(join("/Applications", desktopLauncher))
  && "DeepSeek Harness Desktop is installed in /Applications";

function fakeDsHDesktop(dir, { home = dir, apps = join(home, "Applications"), initialized = true, failAdd = false } = {}) {
  const launcher = join(apps, desktopLauncher);
  const log = join(dir, "dsh-desktop.log");
  const profile = join(home, ".dsh", "profiles", "desktop");
  mkdirSync(join(launcher, ".."), { recursive: true });
  mkdirSync(profile, { recursive: true });
  if (initialized) {
    writeFileSync(join(profile, "package.json"), '{"dsh":{"profile":{"bundles":["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app"]}}}\n');
  }
  writeFileSync(launcher, [
    "#!/bin/sh",
    `echo "$*" >> "${log}"`,
    'case " $* " in *" --dump-config "*) echo \'error: profile "desktop" is managed exclusively by the Electron application\' >&2; exit 1 ;; esac',
    '[ "$1" = plugin ] || { echo "error: profile \\"desktop\\" is managed exclusively by the Electron application" >&2; exit 1; }',
    'manifest="${DSH_HOME:-$HOME/.dsh}/profiles/desktop/package.json"',
    '[ -f "$manifest" ] || { echo "dsh: Error: Open DeepSeek Harness Desktop once to initialize its profile, then fully quit it before running dsh plugin --profile desktop." >&2; exit 1; }',
    'case "$4" in',
    failAdd
      ? '  add) echo "ERR_PNPM_FETCH_404 GET https://registry.example/@openviking%2fdsh-memory-plugin: Not Found - 404" >&2; exit 1 ;;'
      : '  add) echo \'{"dsh":{"profile":{"bundles":["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app","@openviking/dsh-memory-plugin"]}},"dependencies":{"@openviking/dsh-memory-plugin":"^1.0.0"}}\' > "$manifest" ;;',
    '  ls) cat "$manifest" ;;',
    "esac",
    "",
  ].join("\n"), { mode: 0o755 });
  return { launcher, log, profile };
}

function fakeUname(bin, os) {
  writeFileSync(join(bin, "uname"), `#!/bin/sh\ncase "$1" in -m) echo arm64 ;; *) echo ${os} ;; esac\n`, { mode: 0o755 });
}

// `running`: true or false for every call, or the number of calls that still
// find Desktop running.
function fakePgrep(bin, running) {
  const log = join(bin, "pgrep.log");
  writeFileSync(join(bin, "pgrep"), [
    "#!/bin/sh",
    `echo "$*" >> "${log}"`,
    typeof running === "number"
      ? `[ "$(wc -l < "${log}")" -le ${running} ]`
      : `exit ${running ? 0 : 1}`,
    "",
  ].join("\n"), { mode: 0o755 });
}

test("macOS detects DeepSeek Harness Desktop under ~/Applications and selects it", { skip: realDesktop }, (t) => {
  const home = makeTempHome(t);
  const bin = join(home, "fake-bin");
  mkdirSync(bin);
  fakeUname(bin, "Darwin");
  fakeDsHDesktop(home);

  const result = runInstallerPrelude(`
HAVE_CURSOR=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s:%s:%s\\n' "$HAVE_DSH" "$HAVE_DSH_DESKTOP" "$SELECTED_HARNESSES"
printf '%s\\n' "$(resolve_dsh_desktop_cli)"
tui_reset_bin_selection
tui_finish_selection
printf '%s|%s\\n' "$(tui_bin_label dsh-desktop dsh-desktop)" "$SELECTED_HARNESSES"
`, { HOME: home, PATH: `${bin}:/usr/bin:/bin`, OPENVIKING_DSH_DESKTOP_CLI: undefined, DSH_HOME: undefined });

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split("\n"), [
    "0:1:dsh-desktop,cli",
    join(home, "Applications", desktopLauncher),
    "DeepSeek Harness Desktop|dsh-desktop,cli",
  ]);
});

test("a dsh on PATH that is Desktop's launcher is Desktop, not the npm CLI", { skip: realDesktop }, (t) => {
  const home = makeTempHome(t);
  const bin = join(home, "fake-bin");
  mkdirSync(bin);
  fakeUname(bin, "Darwin");
  // Outside ~/Applications, so only the PATH link finds it.
  const { launcher } = fakeDsHDesktop(home, { apps: join(home, "elsewhere") });
  symlinkSync(launcher, join(bin, "dsh"));

  const result = runInstallerPrelude(`
printf '%s:%s:%s\\n' "$HAVE_DSH" "$HAVE_DSH_DESKTOP" "$(resolve_dsh_desktop_cli)"
install_dsh >&2
`, { HOME: home, PATH: `${bin}:/usr/bin:/bin`, OPENVIKING_DSH_DESKTOP_CLI: undefined, DSH_HOME: undefined });

  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), `0:1:${join(bin, "dsh")}`);
  // Asked for the npm CLI, it says why that dsh is not used.
  assert.match(result.stderr, /The dsh on PATH is the one inside DeepSeek Harness Desktop, not the npm dsh CLI/);
});

test("Linux never offers DeepSeek Harness Desktop", (t) => {
  const home = makeTempHome(t);
  const bin = join(home, "fake-bin");
  mkdirSync(bin);
  fakeUname(bin, "Linux");
  fakeDsHDesktop(home);

  const result = runInstallerPrelude(`
HAVE_CURSOR=0
INTERACTIVE=0
REQUESTED_HARNESSES=""
select_harnesses
printf '%s:%s\\n' "$HAVE_DSH_DESKTOP" "$SELECTED_HARNESSES"
idx=0
while [ "$idx" -lt "$(tui_selectable_count)" ]; do printf '%s ' "$(tui_item_at "$idx")"; idx=$((idx + 1)); done
printf '\\n'
SELECTED_HARNESSES=dsh-desktop
(validate_selected_harnesses) || printf 'rejected:%s\\n' "$?"
`, {
    HOME: home,
    PATH: `${bin}:/usr/bin:/bin`,
    OPENVIKING_DSH_DESKTOP_CLI: join(home, "Applications", desktopLauncher),
    DSH_HOME: undefined,
  });

  assert.equal(result.status, 0, result.stderr);
  const [detected, rows, rejected] = result.stdout.trim().split("\n");
  assert.equal(detected, "0:claude,codex,cli");
  assert.doesNotMatch(rows, /dsh-desktop/);
  assert.match(rows, /dsh\|dsh /);
  assert.equal(rejected, "rejected:2");
  assert.match(result.stderr, /macOS only/);
});

test("--dsh-profile desktop routes the dsh harness to dsh-desktop", () => {
  const result = runInstallerPrelude(`
SELECTED_HARNESSES="claude,dsh,dsh-desktop"
PUBLIC_SELECTED_HARNESSES=""
DSH_PROFILE_ARG=desktop
select_dsh_profile
printf '%s\\n' "$SELECTED_HARNESSES"
SELECTED_HARNESSES="dsh"
DSH_PROFILE_ARG=headless
select_dsh_profile
printf '%s:%s\\n' "$SELECTED_HARNESSES" "$DSH_PROFILE"
`);

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.trim().split("\n"), ["claude,dsh-desktop", "dsh:headless"]);
});

function desktopInstaller(t, options = {}, transform) {
  const flow = detachedInstaller(t, transform);
  const bin = join(flow.home, "bin");
  fakeUname(bin, "Darwin");
  fakePgrep(bin, options.running ?? false);
  const desktop = fakeDsHDesktop(join(flow.home, ".."), { home: flow.home, ...options });
  const log = () => (existsSync(desktop.log) ? readFileSync(desktop.log, "utf8") : "");
  return { ...flow, ...desktop, bin, log };
}

test("dsh-desktop installs through Desktop's own launcher and never asks it to boot", { skip: realDesktop }, (t) => {
  const { home, dshLog, launcher, log, bin, run } = desktopInstaller(t);

  // The npm dsh on PATH must not be handed Desktop's profile.
  const result = run(["--harness", "dsh", "--dsh-profile", "desktop", "--lang", "en", "--yes", "--url", "http://127.0.0.1:9", "--api-key", ""]);

  const out = `${result.stdout}\n${result.stderr}`;
  assert.equal(result.status, 0, out);
  assert.equal(existsSync(dshLog), false);
  assert.match(result.stdout, /Selected harnesses: dsh-desktop\n/);
  assert.match(result.stdout, /\[3\/4\] DeepSeek Harness Desktop bundle/);
  assert.match(log(), /^plugin --profile desktop add @openviking\/dsh-memory-plugin@latest$/m);
  assert.match(log(), /^plugin --profile desktop ls$/m);
  assert.doesNotMatch(log(), /--dump-config/);
  // Only this user's app main process: its Host and this launcher run the
  // same executable with arguments.
  assert.equal(readFileSync(join(bin, "pgrep.log"), "utf8"),
    `-u ${process.getuid()} -f DeepSeek Harness\\.app/Contents/MacOS/DeepSeek Harness( -psn_[0-9_]+)?$\n`);
  assert.match(result.stdout, /Reopen DeepSeek Harness Desktop to load it\./);
  assert.match(result.stdout, /dsh-desktop: @openviking\/dsh-memory-plugin installed in DeepSeek Harness Desktop/);
  assert.match(result.stdout, /dsh-desktop: plugin enabled in the Desktop profile/);
  assert.doesNotMatch(out, /Validation reported issues/);
  const shown = launcher.replace(home, "~");
  assert.ok(result.stdout.includes(`"${shown}" plugin --profile desktop add @openviking/dsh-memory-plugin@latest`), result.stdout);
  assert.ok(result.stdout.includes(`Uninstall: with Desktop quit, "${launcher}" plugin --profile desktop rm @openviking/dsh-memory-plugin; or Plugins page`), result.stdout);
});

test("an uninitialized Desktop profile gets the Plugins-page steps instead of a failing add", { skip: realDesktop }, (t) => {
  const { log, run } = desktopInstaller(t, { initialized: false });

  const result = run(["--harness", "dsh-desktop", "--lang", "en", "--yes", "--url", "http://127.0.0.1:9", "--api-key", ""]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.equal(log(), "");
  assert.match(result.stdout, /has not set up its profile yet: open it once, quit it with ⌘Q and re-run this installer/);
  assert.match(result.stdout, /Plugins → Add plugin → enter @openviking\/dsh-memory-plugin → Install, then turn it on if its switch is off/);
  assert.match(result.stdout, /DeepSeek Harness Desktop: nothing; the steps for its Plugins page are printed/);
});

test("a running Desktop is warned about with --yes and blocks the add when it is not quit", { skip: realDesktop }, (t) => {
  const yes = desktopInstaller(t, { running: true });
  const result = yes.run(["--harness", "dsh-desktop", "--lang", "en", "--yes", "--url", "http://127.0.0.1:9", "--api-key", ""]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /DeepSeek Harness Desktop is running; installing anyway\. Quit it with ⌘Q/);
  assert.match(yes.log(), /^plugin --profile desktop add /m);

  // Interactive: asked to quit, still running after Enter, so nothing is added.
  const interactiveFlow = `INTERACTIVE=1
exec 3</dev/null
tui_menu() { TUI_MENU_CHOICE="$2"; }
DSH_DESKTOP_QUIT_WAIT=1
${mainMarker}`;
  const asked = desktopInstaller(t, { running: true }, (source) => source.replace(mainMarker, interactiveFlow));
  const interactive = asked.run(["--harness", "dsh-desktop", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", ""]);
  assert.equal(interactive.status, 0, `${interactive.stdout}\n${interactive.stderr}`);
  assert.match(interactive.stdout, /Quit it with ⌘Q, then press Enter:/);
  assert.match(interactive.stdout, /Waiting for DeepSeek Harness Desktop to exit/);
  assert.match(interactive.stdout, /still running; it was not changed/);
  assert.doesNotMatch(asked.log(), /\badd\b/);
  assert.match(interactive.stdout, /Next: open DeepSeek Harness Desktop: Plugins → Add plugin/);

  // Quitting shuts the Host down first, so Desktop may outlive the Enter by a
  // few seconds: the installer waits for it rather than giving up.
  const quitting = desktopInstaller(t, { running: 2 }, (source) => source.replace(mainMarker, interactiveFlow));
  const waited = quitting.run(["--harness", "dsh-desktop", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", ""]);
  assert.equal(waited.status, 0, `${waited.stdout}\n${waited.stderr}`);
  assert.match(waited.stdout, /Waiting for DeepSeek Harness Desktop to exit/);
  assert.doesNotMatch(waited.stdout, /still running/);
  assert.match(quitting.log(), /^plugin --profile desktop add /m);
});

test("a failed Desktop add shows the launcher's own error", { skip: realDesktop }, (t) => {
  const { launcher, run } = desktopInstaller(t, { failAdd: true });

  const result = run(["--harness", "dsh-desktop", "--lang", "en", "--yes", "--url", "http://127.0.0.1:9", "--api-key", ""]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /dsh plugin add failed for DeepSeek Harness Desktop:\n {6}ERR_PNPM_FETCH_404/);
  assert.ok(result.stdout.includes(`run it manually: "${launcher}" plugin --profile desktop add @openviking/dsh-memory-plugin@latest`), result.stdout);
  assert.match(result.stdout, /Plugins → Add plugin → enter @openviking\/dsh-memory-plugin → Install/);
});

test("without Node.js, a Mac with Desktop is told how to install from its Plugins page", { skip: realDesktop }, (t) => {
  const { log, run } = desktopInstaller(t, {}, (source) => source.replace("command -v node >/dev/null 2>&1 || {", "false || {"));

  const result = run(["--harness", "dsh-desktop", "--lang", "en", "--yes"]);

  assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /node not found/);
  assert.match(result.stdout, /Plugins → Add plugin → enter @openviking\/dsh-memory-plugin → Install, then turn it on if its switch is off/);
  assert.match(result.stdout, /ovcli\.conf: \{"url": "<server-url>", "api_key": "<api-key>"\}/);
  assert.equal(log(), "");
});
