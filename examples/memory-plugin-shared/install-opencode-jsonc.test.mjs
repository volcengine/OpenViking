/**
 * OpenCode's config is a file people write by hand.
 *
 * The installer adds a plugin and an MCP server to it without reserializing it,
 * because reserializing would eat every comment and every formatting choice the
 * user made. That means walking JSONC the way editors do — over comments, past
 * trailing commas, through single-quoted strings — and splicing the new value
 * into the original text. Each of those is a thing `JSON.parse` refuses and a
 * naive scanner mis-reads, so each gets a case here.
 */

import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { stripJsonc, updateOpencodeConfig } from "./lib/install/jsonc-edit.mjs";
import { expectExit, runHookScript } from "./testing/support.mjs";

const PROXY = "/home/u/.openviking/opencode-mcp-proxy/openviking/servers/mcp-proxy.mjs";
const SPEC = "@openviking/opencode-plugin";

/** What the config parses to once the comments and trailing commas are gone. */
function parse(raw) {
  return JSON.parse(stripJsonc(raw));
}

test("comments survive the edit, wherever they sit", () => {
  const next = updateOpencodeConfig([
    "{",
    "  // keep this user note",
    '  "theme": "system",',
    "  /* keep this block note */",
    '  "mcp": {',
    "    // keep this mcp note",
    '    "other": { "type": "local", "command": ["node", "other.js"] }',
    "  }",
    "}",
    "",
  ].join("\n"), { pluginSpec: SPEC, mcpProxy: PROXY });

  assert.match(next, /\/\/ keep this user note/);
  assert.match(next, /\/\* keep this block note \*\//);
  assert.match(next, /\/\/ keep this mcp note/);
  assert.match(next, /"theme": "system"/);
  assert.match(next, /"other": \{ "type": "local"/);
  assert.deepEqual(parse(next).plugin, [SPEC]);
  assert.equal(parse(next).mcp.openviking.command[1], PROXY);
});

test("a trailing comma is not doubled", () => {
  const next = updateOpencodeConfig([
    "{",
    '  "plugin": [',
    '    "some-other-plugin",',
    "  ],",
    '  "mcp": {',
    '    "other": { "type": "local", "command": ["node", "other.js"] },',
    "  },",
    "}",
    "",
  ].join("\n"), { pluginSpec: SPEC, mcpProxy: PROXY });

  assert.doesNotMatch(next, /,\s*,/);
  assert.deepEqual(parse(next).plugin, ["some-other-plugin", SPEC]);
  assert.deepEqual(Object.keys(parse(next).mcp), ["other", "openviking"]);
});

// A single quote makes the whole file unparseable, so this is the one case the
// editor gets through on the text walk alone: nothing tells it where "mcp" is
// except the scan, and the brace and the `//` inside the string would end the
// object and start a comment for a scanner that does not know it is in a quote.
test("a single-quoted value is stepped over, not scanned into", () => {
  const next = updateOpencodeConfig([
    "{",
    "  'theme': 'dark } // not a comment',",
    '  "mcp": {',
    '    "other": { "type": "local", "command": ["node", "other.js"] }',
    "  }",
    "}",
    "",
  ].join("\n"), { pluginSpec: SPEC, mcpProxy: PROXY });

  assert.match(next, /'dark \} \/\/ not a comment'/);
  assert.match(next, /"openviking": \{/);
  assert.ok(
    next.indexOf('"openviking"') > next.indexOf('"other"'),
    "the server belongs inside the mcp object the scan had to find",
  );
  assert.match(next, /"plugin": \[\n\s+"@openviking\/opencode-plugin"\n\s+\]/);
});

test("the MCP server lands inside an existing mcp object, beside its siblings", () => {
  const next = updateOpencodeConfig([
    "{",
    '  "mcp": {',
    '    "other": {',
    '      "type": "local",',
    '      "command": ["node", "other.js"],',
    '      "environment": { "NESTED": { "deeper": true } }',
    "    }",
    "  }",
    "}",
    "",
  ].join("\n"), { pluginSpec: "", mcpProxy: PROXY });

  const data = parse(next);
  assert.deepEqual(data.mcp.other.environment, { NESTED: { deeper: true } });
  assert.deepEqual(data.mcp.openviking, {
    type: "local",
    command: ["node", PROXY],
    enabled: true,
    timeout: 15000,
  });
  assert.equal(data.openviking, undefined, "the server must not be added at the top level");
});

test("an empty config is filled in, and a second run changes nothing", () => {
  const first = updateOpencodeConfig("", { pluginSpec: SPEC, mcpProxy: PROXY });
  assert.deepEqual(parse(first).plugin, [SPEC]);
  assert.equal(updateOpencodeConfig(first, { pluginSpec: SPEC, mcpProxy: PROXY }), first);
});

test("an npm registration of the plugin is dropped when the file plugin is written", () => {
  const next = updateOpencodeConfig([
    "{",
    "  // keep this user note",
    '  "plugin": ["@openviking/opencode-plugin@0.3.1", "some-other-plugin"]',
    "}",
    "",
  ].join("\n"), { pluginSpec: "", mcpProxy: PROXY });

  assert.match(next, /\/\/ keep this user note/);
  assert.deepEqual(parse(next).plugin, ["some-other-plugin"]);
  assert.equal(parse(next).mcp.openviking.command[1], PROXY);
});

test("a server the user disabled stays disabled", () => {
  const raw = [
    "{",
    '  "mcp": {',
    '    "openviking": { "type": "local", "command": ["node", "old.js"], "enabled": false }',
    "  }",
    "}",
    "",
  ].join("\n");
  assert.equal(updateOpencodeConfig(raw, { pluginSpec: "", mcpProxy: PROXY }), raw);
});

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const installer = join(repoRoot, "examples", "memory-plugin-shared", "install.sh");

function runInstaller(args, options, script = installer) {
  return new Promise((resolvePromise, reject) => {
    execFile("bash", [script, ...args], options, (error, stdout, stderr) => {
      if (error) {
        error.stdout = stdout;
        error.stderr = stderr;
        reject(error);
      } else {
        resolvePromise({ stdout, stderr });
      }
    });
  });
}

// The cases above are the editor; this is the wiring. It is the only thing that
// proves install.sh still finds the module and hands it the right three
// arguments now that the editor no longer lives inside the script.
// Reuse the real package installed for plugin development; no registry calls or
// substitute YAML parser. The npm shim models success, registry failure, and an
// incomplete install that falsely exits zero so the public import must catch it.
const yamlRoot = dirname(createRequire(resolve(repoRoot, "examples/opencode-plugin/package.json")).resolve("yaml/package.json"));
for (const [sourceMode, dependencyResult] of ["dev", "archive"].flatMap((mode) =>
  ["success", "install-failure", "missing-dependency", "broken-skill"].map((result) => [mode, result]))) {
  test(`OpenCode ${sourceMode} clean install: ${dependencyResult}`, async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), "ov-opencode-jsonc-")));
    try {
      const home = join(dir, "home");
      const bin = join(dir, "bin");
      const configDir = join(home, ".config", "opencode");
      await mkdir(bin, { recursive: true });
      await mkdir(configDir, { recursive: true });

      const opencode = join(bin, "opencode");
      await writeFile(opencode, "#!/usr/bin/env sh\nprintf 'opencode 0.0.0-test\\n'\n");
      await chmod(opencode, 0o755);
      const npm = join(bin, "npm");
      await writeFile(npm, `#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
fs.writeFileSync(process.env.NPM_TEST_LOG, JSON.stringify({ cwd: process.cwd(), args: process.argv.slice(2) }));
if (process.env.NPM_TEST_RESULT === "install-failure") { console.error("fixture registry unavailable"); process.exit(1); }
if (["success", "broken-skill"].includes(process.env.NPM_TEST_RESULT)) {
  fs.mkdirSync("node_modules", { recursive: true });
  fs.cpSync(process.env.NPM_TEST_YAML, path.resolve("node_modules/yaml"), { recursive: true });
}
`);
      await chmod(npm, 0o755);

      // An earlier installer registered the npm package; the file plugin
      // replaces it rather than loading beside it.
      const configPath = join(configDir, "opencode.jsonc");
      await writeFile(configPath, `{\n  // keep this user note\n  "theme": "system",\n  "plugin": ["${SPEC}"]\n}\n`);

      // Copy only tracked paths from the working tree: no ignored generated
      // runtime or node_modules may hide a missing installation step.
      const source = join(dir, "source");
      const files = execFileSync("git", ["ls-files", "-z", "--", "examples", "agent-plugins", ".github/scripts"], { cwd: repoRoot, encoding: "utf8" }).split("\0").filter(Boolean);
      for (const file of files) {
        // A working-tree deletion remains in the index until it is staged.
        if (!existsSync(join(repoRoot, file))) continue;
        mkdirSync(dirname(join(source, file)), { recursive: true });
        copyFileSync(join(repoRoot, file), join(source, file));
      }
      await mkdir(join(source, ".git"));
      if (dependencyResult === "broken-skill") {
        await writeFile(join(source, "examples/skills/openviking-memory/SKILL.md"),
          "---\nname: []\ndescription: broken\n---\n# broken\n");
      }
      if (sourceMode === "archive") {
        execFileSync("bash", [join(source, ".github/scripts/stage-memory-plugin-marketplace.sh"), join(dir, "memory-plugin-marketplace")]);
        execFileSync("zip", ["-rq", join(dir, "memory-plugin-marketplace.zip"), "memory-plugin-marketplace"], { cwd: dir });
      }

      const installedRoot = join(configDir, "plugins/openviking");
      const previousConfig = await readFile(configPath, "utf8");
      const wrapper = join(configDir, "plugins/openviking.js");
      if (dependencyResult !== "success") {
        await mkdir(installedRoot, { recursive: true });
        await writeFile(join(installedRoot, "previous.txt"), "keep previous plugin");
        await writeFile(wrapper, "// keep previous wrapper\n");
      }
      const installRun = runInstaller([
        "--harness", "opencode",
        "--source", sourceMode,
        "--lang", "en",
        "--url", "http://127.0.0.1:9",
        "--api-key", "",
        "--yes",
      ], {
        cwd: source,
        env: {
          ...process.env,
          HOME: home,
          NPM_TEST_RESULT: dependencyResult,
          NPM_TEST_LOG: join(dir, "npm.json"),
          NPM_TEST_YAML: yamlRoot,
          OPENVIKING_HOME: join(home, ".openviking"),
          OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${join(dir, "memory-plugin-marketplace.zip")}`,
          OPENVIKING_DOWNLOAD_BASE: "https://downloads.example.invalid",
          OPENVIKING_SKIP_VERSION_CHECK: "1",
          PATH: `${bin}:${process.env.PATH}`,
        },
      }, join(source, "examples/memory-plugin-shared/install.sh"));

      if (dependencyResult === "success") {
        await installRun;
      } else {
        await assert.rejects(installRun, (error) => {
          assert.match(error.stdout + error.stderr, /OpenCode: dependency installation or public entrypoint import failed/);
          assert.doesNotMatch(error.stdout, /OpenCode file plugin installed:/);
          assert.match(error.stdout + error.stderr, dependencyResult === "install-failure"
            ? /fixture registry unavailable/ : dependencyResult === "broken-skill"
              ? /Invalid bundled skill/ : /Cannot find package 'yaml'/);
          return true;
        });
        assert.equal(await readFile(configPath, "utf8"), previousConfig);
        assert.equal(await readFile(wrapper, "utf8"), "// keep previous wrapper\n");
        assert.equal(await readFile(join(installedRoot, "previous.txt"), "utf8"), "keep previous plugin");
        assert.equal(existsSync(`${installedRoot}.tmp`), false);
      }
      const npmCall = JSON.parse(await readFile(join(dir, "npm.json"), "utf8"));
      assert.deepEqual(npmCall.args, ["install", "--omit=dev", "--ignore-scripts", "--package-lock=false", "--no-audit", "--no-fund"]);
      assert.equal(npmCall.cwd, sourceMode === "dev" ? join(source, "examples/opencode-plugin") : `${installedRoot}.tmp`);
      if (dependencyResult !== "success") return;
      assert.ok(existsSync(join(installedRoot, "node_modules/yaml/package.json")));
      const raw = await readFile(configPath, "utf8");
      assert.match(raw, /keep this user note/);
      assert.match(raw, /"theme": "system"/);
      assert.match(parse(raw).mcp.openviking.command[1], /servers\/mcp-proxy\.mjs$/);
      const env = { HOME: home, OPENVIKING_HOME: join(home, ".openviking") };
      assert.deepEqual(parse(raw).plugin, []);
      expectExit(await runHookScript(parse(raw).mcp.openviking.command[1], { cwd: home, env }));
      expectExit(await runHookScript(join(installedRoot, "index.mjs"), { cwd: home, env }));
      execFileSync(process.execPath, ["--input-type=module", "-e", `
        const { default: plugin, OpenVikingPlugin } = await import(${JSON.stringify(`file://${installedRoot}/index.mjs`)});
        if (typeof plugin.setup !== "function" || typeof OpenVikingPlugin !== "function") throw Error("missing entrypoints");
        const { readFile } = await import("node:fs/promises");
        const { loadBundledV2Skills } = await import(${JSON.stringify(`file://${installedRoot}/lib/v2-skills.mjs`)});
        const skills = await loadBundledV2Skills(${JSON.stringify(installedRoot)});
        if (skills.map(s => s.name).sort().join(",") !== "openviking-memory,openviking-rule-search,openviking-skills,ov-experience-memory") throw Error("missing bundled skills");
        for (const skill of skills) {
          if (!(await readFile(skill.path, "utf8")).endsWith(skill.content) || !skill.content.startsWith("# ")) throw Error("invalid skill body");
        }
        const hooks = await plugin.server({ directory: process.cwd() });
        if (Object.keys(hooks).length !== 0) throw Error("disabled V1 should have no hooks");
      `], { cwd: home, env: { ...process.env, ...env, OPENVIKING_MEMORY_ENABLED: "false" } });
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
}
