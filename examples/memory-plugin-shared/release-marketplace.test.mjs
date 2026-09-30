import assert from "node:assert/strict";
import { execFile, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const installer = join(ROOT, "examples", "memory-plugin-shared", "install.sh");
const stageScript = join(ROOT, ".github", "scripts", "stage-memory-plugin-marketplace.sh");
const archiveCheck = join(ROOT, ".github", "scripts", "check-marketplace-archive.mjs");
const claudeMarketplaceScript = join(ROOT, ".github", "scripts", "generate-claude-marketplace-json.sh");
const stampScript = join(ROOT, ".github", "scripts", "stamp-installer-version.sh");
const publishGitScript = join(ROOT, ".github", "scripts", "publish-dumb-git-repo.sh");
const zipScript = join(ROOT, ".github", "scripts", "reproducible-zip.sh");

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    ...options,
  });
}

function stageMarketplaceZip(tmp) {
  const stage = join(tmp, "memory-plugin-marketplace");
  const staged = run("bash", [stageScript, stage]);
  assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);
  const zip = join(tmp, "memory-plugin-marketplace.zip");
  const zipped = run("zip", ["-rq", zip, "memory-plugin-marketplace"], { cwd: tmp });
  assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);
  return { stage, zip };
}

// A stand-in for the claude CLI: it records each invocation and keeps the one
// marketplace registration the installer reads back through `--json`.
function writeFakeClaude(bin) {
  writeFileSync(join(bin, "claude"), `#!/bin/sh
echo "$*" >> "$FAKE_CLAUDE_DIR/calls.log"
case "$*" in
  --version) echo "$FAKE_CLAUDE_VERSION (Claude Code)" ;;
  "plugin marketplace list --json") cat "$FAKE_CLAUDE_DIR/marketplaces.json" 2>/dev/null || echo "[]" ;;
  "plugin marketplace remove "*) rm -f "$FAKE_CLAUDE_DIR/marketplaces.json" ;;
  "plugin marketplace add "*)
    case "$4" in https://*) [ -z "$FAKE_CLAUDE_URL_FAILS" ] || exit 1 ;; esac
    printf '[{"name":"openviking","path":"%s"}]' "$4" > "$FAKE_CLAUDE_DIR/marketplaces.json" ;;
  "plugin install "*)
    case "$(cat "$FAKE_CLAUDE_DIR/marketplaces.json" 2>/dev/null)" in *https://*) [ -z "$FAKE_CLAUDE_INSTALL_FAILS" ] || exit 1 ;; esac ;;
esac
exit 0
`, { mode: 0o755 });
}

test("Claude URL marketplace lists the release's plugin zip as an archive source", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-claude-marketplace-"));
  try {
    const { stage } = stageMarketplaceZip(tmp);
    const pluginZip = join(tmp, "openviking-memory-claude.zip");
    const zipped = run("zip", ["-rq", pluginZip, "claude-code-memory-plugin"], { cwd: stage });
    assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);
    const out = join(tmp, "marketplace.json");
    const generated = run("bash", [claudeMarketplaceScript, "v9.9.9", "https://tos.example.invalid/", pluginZip, stage, out]);
    assert.equal(generated.status, 0, `${generated.stdout}\n${generated.stderr}`);

    const manifest = JSON.parse(readFileSync(out, "utf8"));
    const pluginJson = JSON.parse(readFileSync(join(stage, "claude-code-memory-plugin", ".claude-plugin", "plugin.json"), "utf8"));
    assert.equal(manifest.name, "openviking");
    assert.equal(manifest.plugins.length, 1);
    const [entry] = manifest.plugins;
    assert.equal(entry.name, "openviking-memory");
    // Claude Code only updates an installed plugin when this string changes.
    assert.equal(entry.version, pluginJson.version);
    assert.deepEqual(entry.source, {
      source: "archive",
      url: "https://tos.example.invalid/releases/v9.9.9/openviking-memory-claude.zip",
      sha256: createHash("sha256").update(readFileSync(pluginZip)).digest("hex"),
    });

    // Claude Code strips one wrapping directory, so the manifest must sit
    // exactly one level down.
    const listed = run("unzip", ["-Z1", pluginZip]);
    assert.equal(listed.status, 0, listed.stderr);
    assert.ok(listed.stdout.split("\n").includes("claude-code-memory-plugin/.claude-plugin/plugin.json"));
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("TOS installs register Claude Code's URL marketplace when the CLI supports archive sources", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-claude-tos-"));
  try {
    const { zip } = stageMarketplaceZip(tmp);
    const bin = join(tmp, "bin");
    const fake = join(tmp, "fake-claude");
    mkdirSync(bin);
    mkdirSync(fake);
    writeFakeClaude(bin);
    const home = join(tmp, "home");
    mkdirSync(join(home, ".claude"), { recursive: true });
    const marketplaceUrl = "https://tos.example.invalid/plugins/claude/marketplace.json";
    const settingsPath = join(home, ".claude", "settings.json");
    const readSettings = () => JSON.parse(readFileSync(settingsPath, "utf8"));
    const calls = () => readFileSync(join(fake, "calls.log"), "utf8").split("\n");
    const install = (version, extraEnv = {}) => {
      rmSync(join(fake, "calls.log"), { force: true });
      const result = run("bash", [
        installer, "--harness", "claude", "--dist", "tos", "--lang", "en",
        "--url", "http://127.0.0.1:9", "--api-key", "", "--no-statusline", "--yes",
      ], {
        env: {
          ...process.env,
          HOME: home,
          PATH: `${bin}:${process.env.PATH}`,
          OPENVIKING_HOME: join(home, ".openviking"),
          OPENVIKING_TOS_BASE: "https://tos.example.invalid",
          OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${zip}`,
          OPENVIKING_SKIP_VERSION_CHECK: "1",
          FAKE_CLAUDE_DIR: fake,
          FAKE_CLAUDE_VERSION: version,
          ...extraEnv,
        },
      });
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      return result;
    };

    // An install from the old archive channel is re-registered from the URL.
    const archiveDir = join(home, ".openviking", "memory-plugin-marketplace");
    writeFileSync(join(fake, "marketplaces.json"), JSON.stringify([{ name: "openviking", path: archiveDir }]));
    install("2.1.224");
    let log = calls();
    assert.ok(log.includes("plugin uninstall openviking-memory@openviking"), log.join("\n"));
    assert.ok(log.includes("plugin marketplace remove openviking"), log.join("\n"));
    assert.ok(log.includes(`plugin marketplace add ${marketplaceUrl}`), log.join("\n"));
    assert.ok(log.includes("plugin install openviking-memory@openviking"), log.join("\n"));
    assert.deepEqual(readSettings().extraKnownMarketplaces.openviking, {
      source: { source: "url", url: marketplaceUrl },
      autoUpdate: true,
    });

    // A re-run refreshes the same registration and leaves a user's choice to
    // turn auto-update off alone.
    const settings = readSettings();
    settings.extraKnownMarketplaces.openviking.autoUpdate = false;
    writeFileSync(settingsPath, JSON.stringify(settings));
    install("2.1.284");
    log = calls();
    assert.ok(log.includes("plugin marketplace update openviking"), log.join("\n"));
    assert.equal(log.some((line) => line.startsWith("plugin marketplace add")), false, log.join("\n"));
    assert.equal(readSettings().extraKnownMarketplaces.openviking.autoUpdate, false);

    // Unreachable URL marketplace: fall back to the unpacked archive.
    const failed = install("2.1.284", { FAKE_CLAUDE_URL_FAILS: "1", OPENVIKING_TOS_BASE: "https://other.example.invalid" });
    log = calls();
    assert.ok(log.includes(`plugin marketplace add ${archiveDir}`), log.join("\n"));
    assert.match(failed.stdout + failed.stderr, /falling back to the archive directory/);

    // So does a URL marketplace whose plugin cannot be installed, which
    // otherwise leaves no plugin at all.
    const uninstallable = install("2.1.284", { FAKE_CLAUDE_INSTALL_FAILS: "1" });
    log = calls();
    assert.equal(log.filter((line) => line === "plugin install openviking-memory@openviking").length, 2, log.join("\n"));
    assert.ok(log.indexOf(`plugin marketplace add ${archiveDir}`) > log.indexOf(`plugin marketplace add ${marketplaceUrl}`), log.join("\n"));
    assert.match(uninstallable.stdout, /Claude Code\n {4}Next: .*\n {4}Updates: re-run this installer\n/);

    // Claude Code before the archive source keeps the local directory.
    rmSync(join(fake, "marketplaces.json"), { force: true });
    rmSync(settingsPath);
    const legacy = install("2.1.223");
    log = calls();
    assert.ok(log.includes(`plugin marketplace add ${archiveDir}`), log.join("\n"));
    assert.equal(log.some((line) => line.includes("https://")), false, log.join("\n"));
    assert.equal(existsSync(settingsPath) && readSettings().extraKnownMarketplaces !== undefined, false);
    assert.match(legacy.stdout, /Claude Code\n {4}Next: .*\n {4}Updates: re-run this installer\n/);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("a Claude-format wrapper sharing Claude Code's config keeps the URL marketplace", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-claude-wrapper-"));
  try {
    const bin = join(tmp, "bin");
    const fake = join(tmp, "fake-claude");
    const home = join(tmp, "home");
    mkdirSync(bin);
    mkdirSync(fake);
    mkdirSync(home);
    writeFakeClaude(bin);
    writeFileSync(join(bin, "claude-wrap"), '#!/bin/sh\nexec claude "$@"\n', { mode: 0o755 });
    const marketplaceUrl = "https://tos.example.invalid/plugins/claude/marketplace.json";

    const result = run("bash", [
      installer, "--harness", "claude", "--claude-bin", "claude,claude-wrap", "--dist", "tos", "--lang", "en",
      "--url", "http://127.0.0.1:9", "--api-key", "", "--no-statusline", "--yes",
    ], {
      env: {
        ...process.env,
        HOME: home,
        PATH: `${bin}:${process.env.PATH}`,
        OPENVIKING_HOME: join(home, ".openviking"),
        OPENVIKING_TOS_BASE: "https://tos.example.invalid",
        OPENVIKING_SKIP_VERSION_CHECK: "1",
        FAKE_CLAUDE_DIR: fake,
        FAKE_CLAUDE_VERSION: "2.1.284",
      },
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    const log = readFileSync(join(fake, "calls.log"), "utf8").split("\n");
    assert.deepEqual(log.filter((line) => /^plugin (uninstall|marketplace (add|remove))/.test(line)), [
      `plugin marketplace add ${marketplaceUrl}`,
    ]);
    assert.equal(JSON.parse(readFileSync(join(fake, "marketplaces.json"), "utf8"))[0].path, marketplaceUrl);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("release marketplace archive supports ZCode and pi TOS installs", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-zcode-release-"));
  try {
    const stage = join(tmp, "memory-plugin-marketplace");
    const staged = run("bash", [stageScript, stage]);
    assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);

    const bundled = readdirSync(stage, { recursive: true, encoding: "utf8" }).filter((entry) =>
      entry.split(sep).includes("node_modules"),
    );
    assert.deepEqual(bundled.slice(0, 3), [], "marketplace archive carries development dependencies");

    const zipped = run("zip", ["-rq", join(tmp, "memory-plugin-marketplace.zip"), "memory-plugin-marketplace"], {
      cwd: tmp,
    });
    assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);

    const home = join(tmp, "home");
    mkdirSync(home, { recursive: true });
    const installed = run("bash", [
      installer,
      "--harness", "zcode",
      "--dist", "tos",
      "--source", "archive",
      "--lang", "en",
      "--url", "http://127.0.0.1:9",
      "--api-key", "",
      "--yes",
    ], {
      env: {
        ...process.env,
        HOME: home,
        OPENVIKING_HOME: join(home, ".openviking"),
        OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${join(tmp, "memory-plugin-marketplace.zip")}`,
        OPENVIKING_SKIP_VERSION_CHECK: "1",
      },
    });
    assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);

    const integrationRoot = join(home, ".openviking", "agent-integrations", "zcode");
    assert.ok(existsSync(join(integrationRoot, "plugin.json")));
    assert.equal(existsSync(join(integrationRoot, ".claude-plugin")), false);
    assert.ok(existsSync(join(integrationRoot, "scripts", "hook.mjs")));
    assert.ok(existsSync(join(integrationRoot, "hosts", "zcode.mjs")));
    assert.ok(existsSync(join(integrationRoot, "hosts", "zcode-capture.mjs")));
    // An installation is for one client: the other hosts' configuration
    // directories are not copied with it.
    assert.equal(existsSync(join(integrationRoot, "hosts", "zcode", "hooks.json")), true);
    assert.equal(existsSync(join(integrationRoot, "hosts", "cursor")), false);
    // ZCode imports the runtime the installer assembles beside it, the way
    // cursor and trae do, rather than a copy committed into its own tree.
    const sharedRoot = join(home, ".openviking", "agent-integrations", "memory-plugin-shared", "lib");
    assert.ok(existsSync(join(sharedRoot, "async-writer.mjs")));
    assert.ok(existsSync(join(sharedRoot, "capture-utils.mjs")));
    assert.ok(existsSync(join(sharedRoot, "mcp-proxy-config.mjs")));
    assert.equal(existsSync(join(integrationRoot, "scripts", "shared")), false);

    const config = JSON.parse(readFileSync(join(home, ".zcode", "cli", "config.json"), "utf8"));
    assert.equal(config.hooks.enabled, true);
    assert.deepEqual(Object.keys(config.hooks.events), [
      "SessionStart",
      "UserPromptSubmit",
      "PreToolUse",
      "Stop",
    ]);
    assert.ok(config.mcp.servers.openviking);

    // The hook commands point across the plugin boundary at the runtime the
    // installer assembles from the archive, so an entry the manifest forgot to
    // carry is a hooks.json naming a script that is not there.
    const commands = JSON.stringify(config.hooks.events)
      .split(/"/u)
      .filter((part) => part.includes("# openviking-memory"));
    assert.ok(commands.length > 0, "no OpenViking hook commands were installed");
    for (const command of commands) {
      const script = /'([^']*\.mjs)'/u.exec(command)?.[1];
      assert.ok(script, `${command} names no script`);
      assert.ok(existsSync(script), `${script} is missing after install`);
    }

    const bin = join(tmp, "bin");
    mkdirSync(bin);
    writeFileSync(join(bin, "kimi"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    const kimiInstalled = run("bash", [
      installer,
      "--harness", "kimicode",
      "--dist", "tos",
      "--source", "archive",
      "--lang", "en",
      "--url", "http://127.0.0.1:9",
      "--api-key", "",
      "--yes",
    ], {
      env: {
        ...process.env,
        HOME: home,
        PATH: `${bin}:${process.env.PATH}`,
        OPENVIKING_HOME: join(home, ".openviking"),
        OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${join(tmp, "memory-plugin-marketplace.zip")}`,
        OPENVIKING_SKIP_VERSION_CHECK: "1",
      },
    });
    assert.equal(kimiInstalled.status, 0, kimiInstalled.stdout + kimiInstalled.stderr);
    const kimiRoot = join(home, ".kimi-code", "plugins", "managed", "openviking-memory");
    assert.ok(existsSync(join(kimiRoot, "kimi.plugin.json")));
    assert.ok(existsSync(join(kimiRoot, "agent-integrations", "kimicode", "scripts", "hook.mjs")));
    assert.ok(existsSync(join(kimiRoot, "agent-integrations", "memory-plugin-shared", "lib", "agent-hook-runtime.mjs")));

    writeFileSync(join(bin, "pi"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    const piArgs = [installer, "--harness", "pi", "--dist", "tos", "--source", "archive",
      "--lang", "en", "--url", "http://127.0.0.1:9", "--api-key", "", "--yes"];
    const piEnv = { ...process.env, HOME: home, PATH: bin + ":" + process.env.PATH,
      OPENVIKING_HOME: join(home, ".openviking"),
      OPENVIKING_MARKETPLACE_ARCHIVE_URL: "file://" + join(tmp, "memory-plugin-marketplace.zip"), OPENVIKING_SKIP_VERSION_CHECK: "1" };
    const piInstalled = run("bash", piArgs, { env: piEnv });
    assert.equal(piInstalled.status, 0, piInstalled.stdout + piInstalled.stderr);
    const piRoot = join(home, ".pi", "agent", "extensions", "openviking");
    const imported = run("node", ["--input-type=module", "-e", 'await import("./lib/mcp-bridge.mjs"); await import("./tools.ts")'], { cwd: piRoot });
    assert.equal(imported.status, 0, imported.stdout + imported.stderr);
    assert.ok(existsSync(join(piRoot, "package-lock.json")));
    assert.equal(existsSync(join(piRoot, "shared", "mcp-proxy-core.mjs")), false);

    // Both an npm failure and a false-success npm must leave the old install usable.
    writeFileSync(join(piRoot, "installed-before-upgrade"), "keep");
    for (const exitCode of [1, 0]) {
      writeFileSync(join(bin, "npm"), "#!/bin/sh\nexit " + exitCode + "\n", { mode: 0o755 });
      const failed = run("bash", piArgs, { env: piEnv });
      assert.notEqual(failed.status, 0, failed.stdout + failed.stderr);
      assert.equal(readFileSync(join(piRoot, "installed-before-upgrade"), "utf8"), "keep");
      assert.equal(existsSync(piRoot + ".tmp"), false);
      assert.match(failed.stdout + failed.stderr, /existing extension was kept/);
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

// The archive's contents are derived from the plugins' manifests and the shared
// sync, so nothing here restates them. What this pins is that the derivation is
// wired up at all: an archive missing a copy only the generator produces has to
// fail the stage, not ship.
test("staging rejects an archive missing a generated shared copy", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-marketplace-check-"));
  try {
    const stage = join(tmp, "memory-plugin-marketplace");
    const staged = run("bash", [stageScript, stage]);
    assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);
    const stagedDirs = readdirSync(stage, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);

    const generated = [
      join("opencode-plugin", "lib", "shared", "plugin-config.mjs"),
      join("pi-coding-agent-extension", "shared", "plugin-config.mjs"),
    ];
    for (const file of generated) {
      assert.ok(existsSync(join(stage, file)), `${file} is not in the staged tree`);
    }

    const piPackage = JSON.parse(readFileSync(join(stage, "pi-coding-agent-extension", "package.json")));
    const lockPath = join(stage, "pi-coding-agent-extension", "package-lock.json");
    const lock = readFileSync(lockPath);
    assert.deepEqual(JSON.parse(lock).packages[""].dependencies, piPackage.dependencies);
    rmSync(lockPath);
    const missingLock = run("node", [archiveCheck, stage, ...stagedDirs]);
    assert.equal(missingLock.status, 1);
    assert.match(missingLock.stderr, /pi-coding-agent-extension\/package-lock.json/);
    writeFileSync(lockPath, lock);

    rmSync(join(stage, generated[0]));
    const rechecked = run("node", [archiveCheck, stage, ...stagedDirs]);
    assert.equal(rechecked.status, 1, `${rechecked.stdout}\n${rechecked.stderr}`);
    assert.match(rechecked.stderr, /opencode-plugin\/lib\/shared\/plugin-config\.mjs/);

    const restaged = run("bash", [stageScript, stage]);
    assert.equal(restaged.status, 0, `${restaged.stdout}\n${restaged.stderr}`);
    rmSync(join(stage, "agent-hook-plugin", "plugin.json"));
    const missingManifest = run("node", [archiveCheck, stage, ...stagedDirs]);
    assert.equal(missingManifest.status, 1, `${missingManifest.stdout}\n${missingManifest.stderr}`);
    assert.match(missingManifest.stderr, /agent-hook-plugin\/plugin\.json/);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("published installer copies carry the release version and nothing else changes", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-installer-stamp-"));
  try {
    const original = readFileSync(installer, "utf8");
    const out = join(tmp, "install.sh");
    const stamped = run("bash", [stampScript, installer, "0.9.1", out]);
    assert.equal(stamped.status, 0, `${stamped.stdout}\n${stamped.stderr}`);
    assert.equal(readFileSync(installer, "utf8"), original);

    const before = original.split("\n");
    const after = readFileSync(out, "utf8").split("\n");
    const changed = after.flatMap((line, index) => (line === before[index] ? [] : [line]));
    assert.equal(after.length, before.length);
    assert.deepEqual(changed, ['INSTALLER_VERSION="0.9.1"']);
    // The bootstrap rejects a download without these, e.g. an HTML error page.
    assert.match(after[0], /^#!.*bash/);
    assert.ok(after.slice(0, 5).some((line) => line.includes("OpenViking Memory Plugin shared installer")));
    const parsed = run("bash", ["-n", out]);
    assert.equal(parsed.status, 0, parsed.stderr);

    for (const [name, body] of [
      ["missing", original.replace('INSTALLER_VERSION="dev"\n', "")],
      ["duplicated", original.replace('INSTALLER_VERSION="dev"\n', 'INSTALLER_VERSION="dev"\nINSTALLER_VERSION="dev"\n')],
    ]) {
      const src = join(tmp, `${name}.sh`);
      writeFileSync(src, body);
      const rejected = run("bash", [stampScript, src, "0.9.1", join(tmp, `${name}-out.sh`)]);
      assert.equal(rejected.status, 1, `${name}: ${rejected.stdout}\n${rejected.stderr}`);
      assert.match(rejected.stderr, /expected one INSTALLER_VERSION="dev" line/);
      assert.equal(existsSync(join(tmp, `${name}-out.sh`)), false);
    }

    for (const version of ["1.0/rc", '1.0"$(id)"']) {
      const unsafe = run("bash", [stampScript, installer, version, join(tmp, "unsafe.sh")]);
      assert.equal(unsafe.status, 1, `${version}: ${unsafe.stderr}`);
      assert.equal(existsSync(join(tmp, "unsafe.sh")), false);
    }
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

// A re-run of a release rebuilds its zips and overwrites the versioned keys,
// while the manifests pinning their sha256 may stay as they were.
test("rebuilding a release zip from the same commit gives the same bytes", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-reproducible-zip-"));
  try {
    const files = [join("plugin", "plugin.json"), join("plugin", "scripts", "hook.mjs"), join("plugin", "README.md")];
    const build = (name, order, mtime) => {
      const parent = join(tmp, name);
      for (const file of order) {
        mkdirSync(dirname(join(parent, file)), { recursive: true });
        writeFileSync(join(parent, file), `${file}\n`);
        utimesSync(join(parent, file), mtime, mtime);
      }
      const out = join(tmp, `${name}.zip`);
      const zipped = run("bash", [zipScript, out, parent, "plugin"]);
      assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);
      return out;
    };
    const sha256 = (file) => createHash("sha256").update(readFileSync(file)).digest("hex");

    const first = build("first", files, new Date("2020-01-01T00:00:00Z"));
    const second = build("second", [...files].reverse(), new Date());
    assert.equal(sha256(first), sha256(second));
    const listed = run("unzip", ["-Z1", first]);
    assert.equal(listed.status, 0, listed.stderr);
    assert.deepEqual(listed.stdout.trim().split("\n"), [
      "plugin/",
      "plugin/README.md",
      "plugin/plugin.json",
      "plugin/scripts/",
      "plugin/scripts/hook.mjs",
    ]);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

// A stand-in for `aws s3 cp|sync` against one directory per bucket. It logs
// each call and applies it the way the real command would for these arguments.
const FAKE_AWS = `#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const [, op, ...rest] = process.argv.slice(2);
const args = [];
const flags = [];
for (let i = 0; i < rest.length; i += 1) {
  if (!rest[i].startsWith("--")) args.push(rest[i]);
  else if (["--endpoint-url", "--cache-control"].includes(rest[i])) flags.push(rest[i], rest[++i]);
  else flags.push(rest[i]);
}
fs.appendFileSync(process.env.FAKE_AWS_LOG, JSON.stringify({ op, args, flags }) + "\\n");
const local = (url) => (url.startsWith("s3://") ? path.join(process.env.FAKE_S3, url.slice(5)) : url);
const [src, dest] = args.map(local);
// Git writes pack files read-only, so replace rather than overwrite.
const copy = (from, to) => {
  if (fs.statSync(from).isDirectory()) {
    for (const entry of fs.readdirSync(from)) copy(path.join(from, entry), path.join(to, entry));
    return;
  }
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.rmSync(to, { force: true });
  fs.copyFileSync(from, to);
};
copy(src, dest);
if (op === "sync" && flags.includes("--delete")) {
  for (const entry of fs.readdirSync(dest, { recursive: true })) {
    const target = path.join(dest, entry);
    if (fs.statSync(target).isFile() && !fs.existsSync(path.join(src, entry))) fs.rmSync(target);
  }
}
`;

test("the Codex git marketplace is replaced packs first, refs next, old objects last", async () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-dumb-git-"));
  const gitEnv = { ...process.env, GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_NOSYSTEM: "1", NO_PROXY: "*", no_proxy: "*" };
  const git = (args, cwd) => {
    const result = run("git", args, { cwd, env: gitEnv });
    assert.equal(result.status, 0, `git ${args.join(" ")}\n${result.stdout}\n${result.stderr}`);
  };
  // Built the way the release workflow builds it.
  const buildRepo = (release) => {
    const src = join(tmp, `${release}-src`);
    mkdirSync(src);
    writeFileSync(join(src, "release.txt"), release);
    git(["init", "-q", "-b", "main"], src);
    git(["add", "-A"], src);
    git(["-c", "user.email=release@example.invalid", "-c", "user.name=Release", "commit", "-qm", release], src);
    const bare = join(tmp, `${release}.git`);
    git(["clone", "-q", "--bare", src, bare], tmp);
    git(["repack", "-adq"], bare);
    git(["update-server-info"], bare);
    return bare;
  };

  const bucket = join(tmp, "s3");
  const bin = join(tmp, "bin");
  mkdirSync(bin);
  writeFileSync(join(bin, "aws"), FAKE_AWS, { mode: 0o755 });
  const log = join(tmp, "aws.log");
  const dest = "s3://bucket/plugins/memory-plugins.git";
  const publish = (repo) => {
    rmSync(log, { force: true });
    const published = run("bash", [publishGitScript, repo, `${dest}/`, "--endpoint-url", "https://tos.example.invalid"], {
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, FAKE_S3: bucket, FAKE_AWS_LOG: log },
    });
    assert.equal(published.status, 0, `${published.stdout}\n${published.stderr}`);
    return readFileSync(log, "utf8").trim().split("\n").map((line) => JSON.parse(line));
  };

  const server = createServer((req, res) => {
    const file = join(bucket, decodeURIComponent(req.url.split("?")[0]));
    if (!existsSync(file)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": "application/octet-stream" }).end(readFileSync(file));
  });
  await new Promise((listening) => server.listen(0, "127.0.0.1", listening));
  try {
    publish(buildRepo("v1"));
    const v2 = buildRepo("v2");
    const calls = publish(v2);

    const repoPath = (url) => url.slice(dest.length).replace(/^\//, "");
    assert.deepEqual(
      calls.map(({ op, args }) => [op, repoPath(args[1])]),
      [["cp", "objects/pack"], ["cp", "objects/info/packs"], ["cp", "packed-refs"], ["cp", "info/refs"], ["cp", "HEAD"], ["sync", ""]],
    );
    assert.ok(calls.at(-1).flags.includes("--delete"));
    for (const { flags } of calls) {
      assert.equal(flags[flags.indexOf("--endpoint-url") + 1], "https://tos.example.invalid");
      assert.match(flags[flags.indexOf("--cache-control") + 1], /no-store/);
    }

    // The previous release's pack is gone and the new one is what clients get.
    const published = join(bucket, "bucket", "plugins", "memory-plugins.git");
    assert.deepEqual(readdirSync(join(published, "objects", "pack")).sort(), readdirSync(join(v2, "objects", "pack")).sort());
    const clone = join(tmp, "clone");
    const { port } = server.address();
    await promisify(execFile)("git", ["clone", "-q", `http://127.0.0.1:${port}/bucket/plugins/memory-plugins.git`, clone], { env: gitEnv });
    assert.equal(readFileSync(join(clone, "release.txt"), "utf8"), "v2");
  } finally {
    server.close();
    rmSync(tmp, { recursive: true, force: true });
  }
});
