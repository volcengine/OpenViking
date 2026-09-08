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
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { stripJsonc, updateOpencodeConfig } from "./lib/install/jsonc-edit.mjs";

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

function runInstaller(args, options) {
  return new Promise((resolvePromise, reject) => {
    execFile("bash", [installer, ...args], options, (error, stdout, stderr) => {
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
test("the OpenCode installer runs the editor against the real config", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-opencode-jsonc-"));
  try {
    const home = join(dir, "home");
    const bin = join(dir, "bin");
    const configDir = join(home, ".config", "opencode");
    await mkdir(bin, { recursive: true });
    await mkdir(configDir, { recursive: true });

    const opencode = join(bin, "opencode");
    await writeFile(opencode, "#!/usr/bin/env sh\nprintf 'opencode 0.0.0-test\\n'\n");
    await chmod(opencode, 0o755);

    const configPath = join(configDir, "opencode.jsonc");
    await writeFile(configPath, '{\n  // keep this user note\n  "theme": "system"\n}\n');

    await runInstaller([
      "--harness", "opencode",
      "--source", "dev",
      "--dist", "github",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], {
      cwd: repoRoot,
      env: {
        ...process.env,
        HOME: home,
        OPENVIKING_HOME: join(home, ".openviking"),
        PATH: `${bin}:${process.env.PATH}`,
      },
    });

    // A dev-source install registers the plugin as a directory, not as the npm
    // package, so only the MCP fallback reaches the config here.
    const raw = await readFile(configPath, "utf8");
    assert.match(raw, /keep this user note/);
    assert.match(raw, /"theme": "system"/);
    assert.match(parse(raw).mcp.openviking.command[1], /servers\/mcp-proxy\.mjs$/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
