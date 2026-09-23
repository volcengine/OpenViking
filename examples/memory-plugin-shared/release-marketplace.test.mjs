import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const installer = join(ROOT, "examples", "memory-plugin-shared", "install.sh");
const stageScript = join(ROOT, ".github", "scripts", "stage-memory-plugin-marketplace.sh");
const archiveCheck = join(ROOT, ".github", "scripts", "check-marketplace-archive.mjs");

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    ...options,
  });
}

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
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], {
      env: {
        ...process.env,
        HOME: home,
        OPENVIKING_HOME: join(home, ".openviking"),
        OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${join(tmp, "memory-plugin-marketplace.zip")}`,
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
    writeFileSync(join(bin, "pi"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
    const piArgs = [installer, "--harness", "pi", "--dist", "tos", "--source", "archive",
      "--lang", "en", "--url", "http://127.0.0.1:1933", "--api-key", "", "--yes"];
    const piEnv = { ...process.env, HOME: home, PATH: bin + ":" + process.env.PATH,
      OPENVIKING_HOME: join(home, ".openviking"),
      OPENVIKING_MARKETPLACE_ARCHIVE_URL: "file://" + join(tmp, "memory-plugin-marketplace.zip") };
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
