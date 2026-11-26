/**
 * Where the installer gets what it installs.
 *
 * Every file-based harness installs from one release bundle, downloaded once
 * per run and never through git. Claude Code on its URL marketplace needs no
 * bundle at all, and neither does its statusline, which resolves the installed
 * plugin when it runs.
 */

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const stageScript = join(ROOT, ".github", "scripts", "stage-memory-plugin-marketplace.sh");

// Real path: the installer's Node helpers do nothing when started through a
// symlinked directory, which the temp directory is on macOS.
const work = mkdtempSync(join(realpathSync(tmpdir()), "openviking-install-sources-"));
process.on("exit", () => rmSync(work, { recursive: true, force: true }));

// A copy away from the checkout runs the way `curl | bash` does: no sibling
// lib/, no dev mode, so everything has to come from the bundle.
const installer = join(work, "install.sh");
cpSync(join(ROOT, "examples", "memory-plugin-shared", "install.sh"), installer);

// The bundle sits where the default channel looks for it.
const tosBase = join(work, "tos");
mkdirSync(join(tosBase, "releases", "latest"), { recursive: true });
const staged = spawnSync("bash", [stageScript, join(work, "memory-plugin-marketplace")], { encoding: "utf8" });
assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);
const zipped = spawnSync("zip", ["-rq", join(tosBase, "releases", "latest", "memory-plugin-marketplace.zip"), "memory-plugin-marketplace"], {
  cwd: work,
  encoding: "utf8",
});
assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);

function writeExecutable(file, body) {
  writeFileSync(file, body, { mode: 0o755 });
}

function installEnv(home, bin, extraEnv) {
  return {
    ...process.env,
    HOME: home,
    CODEX_HOME: "",
    CODEX_CONFIG_FILE: "",
    CLAUDE_CONFIG_DIR: "",
    PATH: `${bin}:${process.env.PATH}`,
    OPENVIKING_HOME: join(home, ".openviking"),
    OPENVIKING_DOWNLOAD_BASE: `file://${tosBase}`,
    OPENVIKING_MARKETPLACE_ARCHIVE_URL: "",
    OPENVIKING_INSTALLER_REEXEC: "0",
    OPENVIKING_REPO_URL: "",
    OPENVIKING_REPO_REF: "",
    OPENVIKING_REPO_BRANCH: "",
    OPENVIKING_CODEX_TOS_GIT_URL: "",
    OPENVIKING_SKIP_VERSION_CHECK: "1",
    ...extraEnv,
  };
}

function run(home, bin, args, extraEnv = {}) {
  return spawnSync("bash", [installer, ...args], { cwd: home, env: installEnv(home, bin, extraEnv), encoding: "utf8" });
}

// For runs that talk to a server in this process, which spawnSync would block.
function runAsync(home, bin, args, extraEnv = {}) {
  return new Promise((done) => {
    const child = spawn("bash", [installer, ...args], { cwd: home, env: installEnv(home, bin, extraEnv) });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => done({ status, stdout, stderr }));
  });
}

test("a default install of cursor, zcode and opencode downloads the bundle once and never runs git", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const log = join(home, "calls.log");
  mkdirSync(bin);
  const realCurl = spawnSync("bash", ["-c", "command -v curl"], { encoding: "utf8" }).stdout.trim();
  writeExecutable(join(bin, "git"), `#!/bin/sh\necho "git $*" >> "${log}"\nexit 1\n`);
  writeExecutable(join(bin, "curl"), `#!/bin/sh\necho "curl $*" >> "${log}"\nexec "${realCurl}" "$@"\n`);
  writeExecutable(join(bin, "opencode"), "#!/bin/sh\nexit 0\n");

  const result = run(home, bin, [
    "--harness", "cursor,zcode,opencode", "--lang", "en",
    "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
  ]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  const calls = existsSync(log) ? readFileSync(log, "utf8").split("\n").filter(Boolean) : [];
  assert.deepEqual(calls.filter((line) => line.startsWith("git ")), []);
  assert.equal(calls.filter((line) => line.includes("memory-plugin-marketplace.zip")).length, 1, calls.join("\n"));
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "cursor", "scripts", "hook.mjs")));
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "zcode", "scripts", "hook.mjs")));
  assert.ok(existsSync(join(home, ".config", "opencode", "plugins", "openviking", "index.mjs")));
});

// It records its calls and keeps the one marketplace registration the
// installer reads back; `--version` answers with the URL-marketplace release.
function writeFakeClaude(bin, dir) {
  writeExecutable(join(bin, "claude"), `#!/bin/sh
echo "$*" >> "${dir}/calls.log"
case "$*" in
  --version) echo "2.1.224 (Claude Code)" ;;
  "plugin marketplace list --json") cat "${dir}/marketplaces.json" 2>/dev/null || echo "[]" ;;
  "plugin marketplace add "*) printf '[{"name":"openviking","path":"%s"}]' "$4" > "${dir}/marketplaces.json" ;;
esac
exit 0
`);
}

test("a GitHub-channel Claude install moves to TOS, and its statusline follows the installed plugin", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fake = join(home, "fake-claude");
  mkdirSync(bin);
  mkdirSync(fake);
  writeFakeClaude(bin, fake);
  const settingsPath = join(home, ".claude", "settings.json");
  const statusLine = () => JSON.parse(readFileSync(settingsPath, "utf8")).statusLine?.command;
  const setStatusLine = (command) => {
    mkdirSync(dirname(settingsPath), { recursive: true });
    writeFileSync(settingsPath, JSON.stringify({ statusLine: { type: "command", command } }));
  };
  const install = (...extra) => {
    const result = run(home, bin, [
      "--harness", "claude", "--lang", "en",
      "--url", "http://127.0.0.1:9", "--api-key", "", "--yes", ...extra,
    ], { OPENVIKING_DOWNLOAD_BASE: "https://tos.example.invalid" });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    return result;
  };

  // The GitHub channel registered a generated directory marketplace. A re-run
  // re-registers the TOS URL marketplace and leaves the directory alone, since
  // another Claude-format CLI may still be registered against it.
  const githubMarketplace = join(home, ".openviking", "marketplaces", "openviking-claude");
  mkdirSync(join(githubMarketplace, ".claude-plugin"), { recursive: true });
  writeFileSync(join(githubMarketplace, ".claude-plugin", "marketplace.json"), "{}");
  writeFileSync(join(fake, "marketplaces.json"), JSON.stringify([{ name: "openviking", path: githubMarketplace }]));

  // A statusline an earlier installer pointed into its clone is repointed on a
  // plain re-run, with no prompt and no bundle download, and the clone it
  // leaves unused is pointed out.
  const clone = join(home, ".openviking", "openviking-repo");
  mkdirSync(clone, { recursive: true });
  setStatusLine(`node "${clone}/examples/claude-code-memory-plugin/scripts/statusline.mjs"`);
  const repointed = install();
  const command = statusLine();
  assert.match(command, /installed_plugins\.json/);
  assert.match(repointed.stdout, /~\/\.claude\/settings\.json \(marketplace auto-update, statusline\)\n/);
  assert.equal(existsSync(join(home, ".openviking", "memory-plugin-marketplace")), false);
  assert.match(repointed.stdout, /No longer used by the installer; you can delete it: .*openviking-repo/);
  assert.ok(existsSync(clone));
  const calls = readFileSync(join(fake, "calls.log"), "utf8").split("\n");
  assert.ok(calls.includes("plugin marketplace remove openviking"), calls.join("\n"));
  assert.ok(calls.includes("plugin marketplace add https://tos.example.invalid/plugins/claude/marketplace.json"), calls.join("\n"));
  assert.ok(existsSync(githubMarketplace));

  // The command runs whatever copy the registry names, so it follows updates.
  const configDir = join(home, "claude-config");
  const runStatusLine = () => spawnSync("sh", ["-c", command], {
    env: { ...process.env, HOME: home, CLAUDE_CONFIG_DIR: configDir },
    input: "{}",
    encoding: "utf8",
  });
  const missing = runStatusLine();
  assert.equal(missing.status, 0, missing.stderr);
  assert.equal(missing.stdout, "");
  for (const version of ["1.0.0", "1.0.1"]) {
    const pluginRoot = join(configDir, "plugins", "cache", "openviking", "openviking-memory", version);
    mkdirSync(join(pluginRoot, "scripts"), { recursive: true });
    writeFileSync(join(pluginRoot, "scripts", "statusline.mjs"), `process.stdout.write("statusline ${version}");\n`);
    writeFileSync(join(configDir, "plugins", "installed_plugins.json"), JSON.stringify({
      version: 2,
      plugins: { "openviking-memory@openviking": [{ scope: "user", installPath: pluginRoot, version }] },
    }));
    const shown = runStatusLine();
    assert.equal(shown.status, 0, shown.stderr);
    assert.equal(shown.stdout, `statusline ${version}`);
  }

  // Someone else's statusline stays unless the user asks for ours, and so does
  // one that only wraps ours.
  setStatusLine("my-statusline");
  const kept = install();
  assert.equal(statusLine(), "my-statusline");
  assert.match(kept.stdout, /~\/\.claude\/settings\.json \(marketplace auto-update\)\n/);
  const wrapped = `sh -c 'node "${clone}/examples/claude-code-memory-plugin/scripts/statusline.mjs"; echo " | main"'`;
  setStatusLine(wrapped);
  install();
  assert.equal(statusLine(), wrapped);
  const chinese = install("--statusline", "--lang", "zh");
  assert.equal(statusLine(), command);
  assert.match(chinese.stdout, /安装来源： OpenViking 发布版\n/);
  assert.match(chinese.stdout, /Statusline 已注册（备份：.*settings\.json\.bak\.[0-9-]+）\n/);
  assert.match(chinese.stdout, /可随时用 export OPENVIKING_STATUSLINE=off 关闭\n/);
});

test("Claude Code without the plugin command is skipped with an upgrade hint", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const log = join(home, "calls.log");
  mkdirSync(bin);
  writeExecutable(join(bin, "claude"), `#!/bin/sh
echo "$*" >> "${log}"
case "$*" in
  --version) echo "1.0.128 (Claude Code)" ;;
  plugin*) exit 1 ;;
esac
exit 0
`);

  const result = run(home, bin, [
    "--harness", "claude", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
  ], { OPENVIKING_DOWNLOAD_BASE: "https://tos.example.invalid" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /Skipping claude: it has no 'plugin' command\. The plugin needs Claude Code 2\.0 or newer/);
  // Neither the review nor the next steps offer commands it cannot run.
  assert.doesNotMatch(result.stdout, /claude plugin/);
  const calls = readFileSync(log, "utf8").split("\n").filter(Boolean);
  assert.deepEqual(calls.filter((line) => !["--version", "plugin --help"].includes(line)), []);
  assert.equal(existsSync(join(home, ".claude", "settings.json")), false);
  assert.equal(existsSync(join(home, ".openviking", "memory-plugin-marketplace")), false);
});

// It records its calls and keeps the one marketplace registration the
// installer reads back; FAKE_CODEX_GIT_FAILS makes adding a git marketplace fail.
// A \`legacy\` file stands for a pre-unification marketplace whose directory is
// gone, which makes Codex fail every \`marketplace list\`.
function writeFakeCodex(bin, dir) {
  writeExecutable(join(bin, "codex"), `#!/bin/sh
echo "$*" >> "${dir}/calls.log"
case "$*" in
  "plugin marketplace list --json")
    [ ! -f "${dir}/legacy" ] || { echo "Error: failed to load marketplace(s)" >&2; exit 1; }
    cat "${dir}/marketplaces.json" 2>/dev/null || echo '{"marketplaces":[]}' ;;
  "plugin marketplace remove openviking-plugins-local") rm -f "${dir}/legacy" ;;
  "plugin marketplace remove "*) rm -f "${dir}/marketplaces.json" ;;
  "plugin marketplace add "*)
    case "$4" in *.git) [ -z "$FAKE_CODEX_GIT_FAILS" ] || { echo "fatal: git marketplace unreachable" >&2; exit 1; } ;; esac
    if [ -f "${dir}/marketplaces.json" ] && ! grep -qF "\"$4\"" "${dir}/marketplaces.json"; then
      echo "Error: marketplace 'openviking' is already added from a different source; remove it before adding this source" >&2
      exit 1
    fi
    printf '{"marketplaces":[{"name":"openviking","marketplaceSource":{"source":"%s"}}]}' "$4" > "${dir}/marketplaces.json" ;;
  "plugin list") echo "openviking-memory@openviking" ;;
esac
exit 0
`);
}

for (const inheritedKey of ["CODEX_HOME", "CODEX_CONFIG_FILE"]) {
  test(`Codex fixtures ignore inherited ${inheritedKey}`, () => {
    const home = mkdtempSync(join(work, "home-"));
    const bin = join(home, "bin");
    const fake = join(home, "fake-codex");
    mkdirSync(bin);
    mkdirSync(fake);
    writeFakeCodex(bin, fake);
    const callerHome = mkdtempSync(join(work, "caller-codex-"));
    const callerConfig = join(callerHome, inheritedKey === "CODEX_HOME" ? "config.toml" : "outside.toml");
    const original = "# Caller configuration must stay untouched.\n";
    writeFileSync(callerConfig, original);
    const saved = { CODEX_HOME: process.env.CODEX_HOME, CODEX_CONFIG_FILE: process.env.CODEX_CONFIG_FILE };
    let result;
    try {
      delete process.env.CODEX_HOME;
      delete process.env.CODEX_CONFIG_FILE;
      process.env[inheritedKey] = inheritedKey === "CODEX_HOME" ? callerHome : callerConfig;
      result = run(home, bin, [
        "--harness", "codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
      ]);
    } finally {
      for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(readFileSync(callerConfig, "utf8"), original);
    assert.match(readFileSync(join(home, ".codex", "config.toml"), "utf8"), /\[plugins\."openviking-memory@openviking"\]\nenabled = true/);
  });
}

// The fake leaves no plugin cache behind, which validation has to tolerate.
test("Codex moves to the git marketplace, falls back to the bundle, and says how it updates", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fake = join(home, "fake-codex");
  mkdirSync(bin);
  mkdirSync(fake);
  writeFakeCodex(bin, fake);
  const install = (extraEnv) => {
    rmSync(join(fake, "calls.log"), { force: true });
    const result = run(home, bin, [
      "--harness", "codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
    ], extraEnv);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    return { stdout: result.stdout, calls: readFileSync(join(fake, "calls.log"), "utf8").split("\n") };
  };

  // A marketplace the GitHub channel registered is re-registered from the download location.
  writeFileSync(join(fake, "marketplaces.json"), JSON.stringify({
    marketplaces: [{ name: "openviking", marketplaceSource: { source: "https://github.com/volcengine/OpenViking.git" } }],
  }));
  const moved = install();
  assert.ok(moved.calls.includes("plugin marketplace remove openviking"), moved.calls.join("\n"));
  assert.ok(moved.calls.includes(`plugin marketplace add file://${tosBase}/plugins/memory-plugins.git`), moved.calls.join("\n"));
  assert.match(moved.stdout, /Codex\n {4}Next: .*\n {4}Updates: automatic; Codex upgrades the marketplace when it starts\n/);
  assert.match(moved.stdout, /Uninstall: codex plugin remove openviking-memory@openviking && codex plugin marketplace remove openviking\n/);
  assert.match(readFileSync(join(home, ".codex", "config.toml"), "utf8"), /\[plugins\."openviking-memory@openviking"\]\nenabled = true/);
  assert.equal(existsSync(join(home, ".openviking", "memory-plugin-marketplace")), false);

  // A git marketplace that cannot be added gives way to the bundle's directory.
  rmSync(join(fake, "marketplaces.json"));
  const fallback = install({ FAKE_CODEX_GIT_FAILS: "1" });
  assert.match(fallback.stdout, /Git marketplace unavailable; falling back to the archive directory/);
  assert.ok(fallback.calls.includes(`plugin marketplace add ${join(home, ".openviking", "memory-plugin-marketplace")}`), fallback.calls.join("\n"));
  assert.match(fallback.stdout, /Codex\n {4}Next: .*\n {4}Updates: re-run this installer\n/);
});

test("Codex drops the pre-unification marketplace and replaces a registration it hid", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fake = join(home, "fake-codex");
  mkdirSync(bin);
  mkdirSync(fake);
  writeFakeCodex(bin, fake);
  mkdirSync(join(home, ".codex"));
  writeFileSync(join(home, ".codex", "config.toml"), '[marketplaces.openviking-plugins-local]\nsource_type = "local"\nsource = "/gone"\n');
  writeFileSync(join(fake, "legacy"), "");
  writeFileSync(join(fake, "marketplaces.json"), JSON.stringify({
    marketplaces: [{ name: "openviking", marketplaceSource: { source: "https://github.com/volcengine/OpenViking.git" } }],
  }));

  const result = run(home, bin, [
    "--harness", "codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
  ]);

  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /Removes the old marketplace openviking-plugins-local/);
  const calls = readFileSync(join(fake, "calls.log"), "utf8").split("\n");
  assert.ok(calls.includes("plugin remove openviking-memory@openviking-plugins-local"), calls.join("\n"));
  assert.ok(calls.includes("plugin marketplace remove openviking-plugins-local"), calls.join("\n"));
  assert.ok(calls.includes("plugin marketplace remove openviking"), calls.join("\n"));
  assert.equal(calls.filter((line) => line === `plugin marketplace add file://${tosBase}/plugins/memory-plugins.git`).length, 1);
  assert.doesNotMatch(result.stdout, /falling back/);
});

test("of several download locations, the first to answer serves the whole install", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fake = join(home, "fake-codex");
  mkdirSync(bin);
  mkdirSync(fake);
  writeFakeCodex(bin, fake);
  const base = join(home, "dl");
  mkdirSync(join(base, "plugins", "claude"), { recursive: true });
  writeFileSync(join(base, "plugins", "claude", "marketplace.json"), "{}");
  const args = ["--harness", "codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"];

  for (const bases of [`http://127.0.0.1:1/dl file://${base}/`, `file://${base} http://127.0.0.1:1/dl`]) {
    rmSync(join(fake, "calls.log"), { force: true });
    rmSync(join(fake, "marketplaces.json"), { force: true });
    const result = run(home, bin, args, { OPENVIKING_DOWNLOAD_BASE: bases });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const calls = readFileSync(join(fake, "calls.log"), "utf8").split("\n");
    assert.ok(calls.includes(`plugin marketplace add file://${base}/plugins/memory-plugins.git`), calls.join("\n"));
  }

  // With none answering, the first one is named in what fails.
  const none = run(home, bin, ["--harness", "cursor", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"], {
    OPENVIKING_DOWNLOAD_BASE: "http://127.0.0.1:1/dl http://127.0.0.1:1/other",
  });
  assert.equal(none.status, 1, `${none.stdout}\n${none.stderr}`);
  assert.match(none.stdout + none.stderr, /http:\/\/127\.0\.0\.1:1\/dl\/releases\/latest\/memory-plugin-marketplace\.zip/);
});

// macOS ships bash 3.2 as /bin/bash, which runs the ERR trap for a failing
// `command <program>` even where the failure is handled.
test("a handled CLI failure neither reports itself nor disarms the error report", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fake = join(home, "fake-codex");
  mkdirSync(bin);
  mkdirSync(fake);
  writeFakeCodex(bin, fake);
  const install = () => spawnSync("/bin/bash", [
    installer, "--harness", "codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
  ], { cwd: home, env: installEnv(home, bin, { CODEX_HOME: join(home, ".codex"), FAKE_CODEX_GIT_FAILS: "1" }), encoding: "utf8" });

  const fellBack = install();
  assert.equal(fellBack.status, 0, `${fellBack.stdout}\n${fellBack.stderr}`);
  assert.doesNotMatch(fellBack.stderr, /stopped unexpectedly|unreachable/);
  const calls = readFileSync(join(fake, "calls.log"), "utf8").split("\n");
  assert.ok(calls.includes(`plugin marketplace add ${join(home, ".openviking", "memory-plugin-marketplace")}`), calls.join("\n"));

  // A real failure later in the same run is still reported.
  const config = join(home, ".codex", "config.toml");
  rmSync(config);
  mkdirSync(config);
  const failed = install();
  assert.equal(failed.status, 1, `${failed.stdout}\n${failed.stderr}`);
  assert.equal(failed.stderr.match(/OpenViking installer stopped unexpectedly/g)?.length, 1, failed.stderr);
  assert.match(failed.stderr, /Command: node /);
});

test("Claude Code and Codex files go where CLAUDE_CONFIG_DIR and CODEX_HOME point", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const fakeClaude = join(home, "fake-claude");
  const fakeCodex = join(home, "fake-codex");
  mkdirSync(bin);
  mkdirSync(fakeClaude);
  mkdirSync(fakeCodex);
  writeFakeClaude(bin, fakeClaude);
  writeFakeCodex(bin, fakeCodex);
  const claudeDir = join(home, "claude-config");
  const codexDir = join(home, "codex-home");
  const cachedProxy = join(codexDir, "plugins", "cache", "openviking", "openviking-memory", "1.0.0", "servers", "mcp-proxy.mjs");
  mkdirSync(dirname(cachedProxy), { recursive: true });
  writeFileSync(cachedProxy, "");

  const result = run(home, bin, [
    "--harness", "claude,codex", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--statusline", "--yes",
  ], { OPENVIKING_DOWNLOAD_BASE: "https://tos.example.invalid", CLAUDE_CONFIG_DIR: claudeDir, CODEX_HOME: codexDir });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  const settings = JSON.parse(readFileSync(join(claudeDir, "settings.json"), "utf8"));
  assert.equal(settings.extraKnownMarketplaces.openviking.autoUpdate, true);
  assert.match(settings.statusLine.command, /installed_plugins\.json/);
  assert.match(readFileSync(join(codexDir, "config.toml"), "utf8"), /\[plugins\."openviking-memory@openviking"\]\nenabled = true/);
  assert.ok(result.stdout.includes(`codex: cached stdio proxy parses (${cachedProxy})`), result.stdout);
  assert.equal(existsSync(join(home, ".claude")), false);
  assert.equal(existsSync(join(home, ".codex")), false);
});

test("retired GitHub-channel options are accepted, announced and ignored", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  mkdirSync(bin);
  const notice = /The GitHub install channel was removed and its options are ignored/;
  const install = (args, extraEnv) => run(home, bin, [
    "--harness", "cursor", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes", ...args,
  ], extraEnv);

  for (const [args, extraEnv] of [
    [["--dist", "github"], {}],
    [["--source", "remote"], {}],
    [[], { OPENVIKING_REPO_REF: "my-branch" }],
  ]) {
    const result = install(args, extraEnv);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(result.stdout, notice);
    assert.match(result.stdout, /Source: OpenViking release\n/);
  }
  const tos = install(["--dist", "tos"]);
  assert.equal(tos.status, 0, `${tos.stdout}\n${tos.stderr}`);
  assert.doesNotMatch(tos.stdout, notice);
  assert.equal(install(["--dist", "gitlab"]).status, 2);
});

test("the installer runs when bash reads it from stdin", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  mkdirSync(bin);
  const piped = (args) => spawnSync("/bin/bash", ["-s", "--", ...args], {
    cwd: home,
    env: installEnv(home, bin, {}),
    input: readFileSync(installer),
    encoding: "utf8",
  });

  const help = piped(["--help"]);
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /^Usage: install\.sh/);
  const installed = piped(["--harness", "cursor", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"]);
  assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "cursor", "scripts", "hook.mjs")));
});

test("a copy that is not a release build runs the release installer", () => {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  mkdirSync(bin);
  const release = join(home, "tos", "memory-plugin-shared", "install.sh");
  mkdirSync(dirname(release), { recursive: true });
  const args = ["--harness", "cursor", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"];
  // A lib/install beside the script would be taken for the installer's own
  // helpers, so the release copy runs alone in a directory it then removes.
  const tmp = join(home, "tmp");
  mkdirSync(join(tmp, "lib", "install"), { recursive: true });
  const reexec = { OPENVIKING_INSTALLER_REEXEC: "", OPENVIKING_DOWNLOAD_BASE: `file://${join(home, "tos")}`, TMPDIR: tmp };

  writeFileSync(release, [
    "#!/usr/bin/env bash",
    "# OpenViking Memory Plugin shared installer (release stand-in)",
    'printf \'%s|\' "$OPENVIKING_INSTALLER_REEXEC" "$@" "$(ls -A "$(dirname "$0")")"',
    "exit 7",
    "",
  ].join("\n"));
  const handed = run(home, bin, args, reexec);
  assert.equal(handed.status, 7, handed.stderr);
  assert.equal(handed.stdout, ["0", "--dist", "tos", ...args, "install.sh", ""].join("|"));
  assert.deepEqual(readdirSync(tmp), ["lib"]);

  // Anything that is not the installer, such as an error page, is not run.
  writeFileSync(release, "<html>503 Service Unavailable</html>\n");
  cpSync(join(tosBase, "releases"), join(home, "tos", "releases"), { recursive: true });
  const kept = run(home, bin, args, reexec);
  assert.equal(kept.status, 0, `${kept.stdout}\n${kept.stderr}`);
  assert.match(kept.stdout, /Could not fetch the release installer; continuing with this copy/);
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "cursor", "scripts", "hook.mjs")));
});

// The release resolver: a local stand-in for the install site answers each
// harness from `answers`, and every request lands in `requests`.
const pinnedZip = join(tosBase, "releases", "v9.9.9", "memory-plugin-marketplace.zip");
mkdirSync(dirname(pinnedZip), { recursive: true });
cpSync(join(tosBase, "releases", "latest", "memory-plugin-marketplace.zip"), pinnedZip);
const pinnedSha256 = createHash("sha256").update(readFileSync(pinnedZip)).digest("hex");
const pinnedGit = `file://${tosBase}/plugins/v9.9.9/memory-plugins.git`;

let answers = {};
const requests = [];
const site = createServer((req, res) => {
  requests.push({ path: req.url, agent: req.headers["user-agent"] });
  const answer = answers[req.url.replace(/^\/install\/v1\/(.*)\.json$/, "$1")] ?? { status: 404, body: {} };
  res.writeHead(answer.status, { "content-type": "application/json" });
  res.end(JSON.stringify(answer.body));
});
await new Promise((ready) => site.listen(0, "127.0.0.1", ready));
test.after(() => site.close());
const siteEnv = { OPENVIKING_INSTALL_SITE: `http://127.0.0.1:${site.address().port}`, OPENVIKING_SKIP_VERSION_CHECK: "" };

function bundleAnswer(overrides = {}) {
  return {
    status: 200,
    body: {
      schema: 1,
      harness: "cursor",
      version: "9.9.9",
      bundle_url: `file://${pinnedZip}`,
      sha256: pinnedSha256,
      source_url: `file://${tosBase}/releases/v9.9.9/openviking-source.zip`,
      source_sha256: pinnedSha256,
      ...overrides,
    },
  };
}

function resolverHome() {
  const home = mkdtempSync(join(work, "home-"));
  const bin = join(home, "bin");
  const log = join(home, "curl.log");
  mkdirSync(bin);
  const realCurl = spawnSync("bash", ["-c", "command -v curl"], { encoding: "utf8" }).stdout.trim();
  writeExecutable(join(bin, "curl"), `#!/bin/sh\necho "curl $*" >> "${log}"\nexec "${realCurl}" "$@"\n`);
  const downloads = () => readFileSync(log, "utf8").split("\n").filter((line) => line.includes("memory-plugin-marketplace.zip"));
  return { home, bin, downloads };
}

const cursorArgs = ["--harness", "cursor", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"];
const latestZip = `file://${tosBase}/releases/latest/memory-plugin-marketplace.zip`;

test("the resolver pins the release each harness installs, asked for under its public name", async () => {
  const { home, bin, downloads } = resolverHome();
  const traeLog = join(home, "trae-cli.log");
  writeExecutable(join(bin, "trae-cli"), `#!/bin/sh
echo "$*" >> "${traeLog}"
case "$*" in
  "plugin marketplace list --json") echo "[]" ;;
  "plugin list") echo "openviking-memory" ;;
esac
exit 0
`);
  answers = {
    cursor: bundleAnswer(),
    "trae-cli": { status: 200, body: { schema: 1, harness: "trae-cli", version: "9.9.9", git_url: pinnedGit } },
  };
  requests.length = 0;

  const result = await runAsync(home, bin, [
    "--harness", "cursor,trae-cli", "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes",
  ], siteEnv);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  assert.deepEqual(requests.map((r) => r.path).sort(), ["/install/v1/cursor.json", "/install/v1/trae-cli.json"]);
  for (const { agent } of requests) assert.match(agent, /^openviking-installer\/dev \((darwin|linux); [^;)]+\)$/);
  assert.equal(downloads().length, 1);
  assert.match(downloads()[0], /releases\/v9\.9\.9\/memory-plugin-marketplace\.zip/);
  assert.ok(readFileSync(traeLog, "utf8").split("\n").includes(`plugin marketplace add ${pinnedGit}`));
  assert.match(result.stdout, /Release: 9\.9\.9/);
  assert.doesNotMatch(result.stdout, /Removes the deprecated TRAE CLI Hooks integration/);
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "cursor", "scripts", "hook.mjs")));
});

test("an answer naming another download location is read from the one in use", async () => {
  const { home, bin, downloads } = resolverHome();
  const other = "http://127.0.0.1:1/dl";
  const env = { ...siteEnv, OPENVIKING_DOWNLOAD_BASE: `file://${tosBase} ${other}/` };

  answers = { cursor: bundleAnswer({ bundle_url: `${other}/releases/v9.9.9/memory-plugin-marketplace.zip`, source_url: `${other}/source.zip` }) };
  const pinned = await runAsync(home, bin, cursorArgs, env);
  assert.equal(pinned.status, 0, `${pinned.stdout}\n${pinned.stderr}`);
  assert.deepEqual(downloads().map((line) => line.split(" ").pop()), [`file://${pinnedZip}`]);

  // The docs site's channel data carries no checksum: its version is shown
  // and the latest bundle is installed.
  answers = { cursor: { status: 200, body: { schema: 1, harness: "cursor", version: "20260929-0123456789", bundle_url: `${other}/releases/latest/memory-plugin-marketplace.zip` } } };
  const latest = await runAsync(home, bin, cursorArgs, env);
  assert.equal(latest.status, 0, `${latest.stdout}\n${latest.stderr}`);
  assert.match(latest.stdout, /Release: 20260929-0123456789/);
  assert.equal(downloads().pop().split(" ").pop(), latestZip);
});

test("an unavailable or untrusted resolver answer falls back to the latest release without a word", async () => {
  for (const answer of [
    { status: 503, body: { error: "unavailable" } },
    bundleAnswer({ bundle_url: "https://elsewhere.example.invalid/memory-plugin-marketplace.zip" }),
    bundleAnswer({ source_url: "https://elsewhere.example.invalid/openviking-source.zip" }),
    bundleAnswer({ schema: 2 }),
  ]) {
    const { home, bin, downloads } = resolverHome();
    answers = { cursor: answer };
    requests.length = 0;
    const result = await runAsync(home, bin, cursorArgs, siteEnv);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(requests.length, 1);
    assert.deepEqual(downloads().map((line) => line.split(" ").pop()), [latestZip]);
    assert.doesNotMatch(`${result.stdout}${result.stderr}`, /install\/v1|elsewhere|Release:/);
  }
});

test("a bundle that does not match the resolver's checksum is not installed", async () => {
  answers = { cursor: bundleAnswer({ sha256: "0".repeat(64) }) };
  const { home, bin } = resolverHome();
  const mismatch = await runAsync(home, bin, cursorArgs, siteEnv);
  assert.equal(mismatch.status, 1, `${mismatch.stdout}\n${mismatch.stderr}`);
  assert.match(mismatch.stderr, /does not match the checksum the release published/);
  assert.match(mismatch.stderr, new RegExp(`got ${pinnedSha256}`));
  assert.equal(existsSync(join(home, ".openviking", "memory-plugin-marketplace")), false);
  assert.equal(existsSync(join(home, ".openviking", "agent-integrations", "cursor")), false);

  // An explicit bundle URL replaces the resolver's, checksum included.
  const overridden = await runAsync(home, bin, cursorArgs, { ...siteEnv, OPENVIKING_MARKETPLACE_ARCHIVE_URL: latestZip });
  assert.equal(overridden.status, 0, `${overridden.stdout}\n${overridden.stderr}`);
  assert.ok(existsSync(join(home, ".openviking", "agent-integrations", "cursor", "scripts", "hook.mjs")));
});

test("OPENVIKING_SKIP_VERSION_CHECK skips the version check", async () => {
  answers = { cursor: bundleAnswer() };
  requests.length = 0;
  const { home, bin, downloads } = resolverHome();
  const result = await runAsync(home, bin, cursorArgs, { ...siteEnv, OPENVIKING_SKIP_VERSION_CHECK: "1" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.deepEqual(requests, []);
  assert.deepEqual(downloads().map((line) => line.split(" ").pop()), [latestZip]);
});
