/**
 * Conformance checks for the OpenViking Agent Plugins 1.0 package.
 *
 * Zero-dependency, runs with `node --test agent-plugins/plugin.test.mjs`.
 * Validates the manifests against the Agent Plugins 1.0 spec
 * (https://agent-plugins.org/specification), skill frontmatter against the
 * Agent Skills spec, and that every referenced/vendored .mjs file exists and
 * parses.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PLUGIN_ROOT = dirname(fileURLToPath(import.meta.url));

const SPEC_VERSION = "1.0.0";
const PLUGIN_SCHEMA_URL = `https://agent-plugins.org/schemas/${SPEC_VERSION}/plugin.schema.json`;
const MCP_SCHEMA_URL = `https://agent-plugins.org/schemas/${SPEC_VERSION}/mcp.schema.json`;

// plugin.json root is closed: only fields documented by the 1.0 spec.
const PLUGIN_ALLOWED_KEYS = new Set([
  "$schema",
  "name",
  "version",
  "description",
  "author",
  "homepage",
  "repository",
  "license",
  "keywords",
  "extensions",
]);
const AUTHOR_ALLOWED_KEYS = new Set(["name", "email", "url"]);

// name: 1-64 chars, lowercase alphanumeric plus hyphens and periods, no
// consecutive hyphens or periods, starts and ends alphanumeric.
const NAME_RE = /^[a-z0-9](?:[a-z0-9]|[-.](?=[a-z0-9])){0,63}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

function loadJson(relPath) {
  return JSON.parse(readFileSync(join(PLUGIN_ROOT, relPath), "utf-8"));
}

function schemaSpecVersion(schemaUrl) {
  const m = /^https:\/\/agent-plugins\.org\/schemas\/(\d+\.\d+\.\d+)\//.exec(String(schemaUrl));
  return m ? m[1] : null;
}

function listMjsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listMjsFiles(full));
    else if (entry.endsWith(".mjs")) out.push(full);
  }
  return out;
}

test("plugin.json conforms to Agent Plugins 1.0", () => {
  const manifest = loadJson("plugin.json");

  assert.equal(manifest.$schema, PLUGIN_SCHEMA_URL);

  assert.equal(typeof manifest.name, "string");
  assert.ok(manifest.name.length >= 1 && manifest.name.length <= 64, "name must be 1-64 chars");
  assert.match(manifest.name, NAME_RE, "name must be lowercase alphanumeric/hyphens/periods without consecutive separators");
  assert.ok(!/--|\.\./.test(manifest.name), "name must not contain consecutive hyphens or periods");

  for (const key of Object.keys(manifest)) {
    assert.ok(PLUGIN_ALLOWED_KEYS.has(key), `plugin.json root field not in the 1.0 spec: ${key}`);
  }

  if (manifest.version !== undefined) {
    assert.match(manifest.version, SEMVER_RE, "version must be semver");
  }
  if (manifest.author !== undefined) {
    assert.equal(typeof manifest.author, "object");
    for (const key of Object.keys(manifest.author)) {
      assert.ok(AUTHOR_ALLOWED_KEYS.has(key), `author field not in the 1.0 spec: ${key}`);
    }
    assert.equal(typeof manifest.author.name, "string");
  }
  if (manifest.keywords !== undefined) {
    assert.ok(Array.isArray(manifest.keywords));
    for (const kw of manifest.keywords) assert.equal(typeof kw, "string");
  }
  assert.equal(typeof manifest.description, "string");
  assert.equal(typeof manifest.license, "string");
});

test("mcp.json conforms and its spec version matches plugin.json", () => {
  const manifest = loadJson("plugin.json");
  const mcp = loadJson("mcp.json");

  assert.equal(mcp.$schema, MCP_SCHEMA_URL);
  assert.equal(
    schemaSpecVersion(mcp.$schema),
    schemaSpecVersion(manifest.$schema),
    "mcp.json $schema spec version must match plugin.json's",
  );

  for (const key of Object.keys(mcp)) {
    assert.ok(key === "$schema" || key === "mcpServers", `mcp.json root field not in the 1.0 spec: ${key}`);
  }
  assert.equal(typeof mcp.mcpServers, "object");
  assert.ok(Object.keys(mcp.mcpServers).length > 0, "at least one MCP server expected");
});

test("mcp.json server entries are valid and reference files inside the plugin", () => {
  const mcp = loadJson("mcp.json");

  for (const [name, server] of Object.entries(mcp.mcpServers)) {
    if (server.type === "streamable-http") {
      assert.equal(typeof server.url, "string");
      for (const header of Object.keys(server.headers || {})) {
        assert.ok(
          !/authorization|api[-_]?key|token|secret|cookie/i.test(header),
          `server ${name}: headers must not carry credentials (${header})`,
        );
      }
      continue;
    }

    assert.equal(server.type, "stdio", `server ${name}: type must be stdio or streamable-http`);
    assert.equal(typeof server.command, "string");
    // command is a single executable token: no shell strings, and placeholders
    // like ${PLUGIN_ROOT} are only expanded in args/env/cwd, never in command.
    assert.ok(!/\s/.test(server.command), `server ${name}: command must be a single token`);
    assert.ok(!server.command.includes("${"), `server ${name}: placeholders are not expanded in command`);
    if (server.command.startsWith("./")) {
      assert.ok(existsSync(join(PLUGIN_ROOT, server.command)), `server ${name}: plugin-relative command missing`);
    }

    for (const arg of server.args || []) {
      if (!String(arg).includes("${PLUGIN_ROOT}")) continue;
      const expanded = resolve(String(arg).replaceAll("${PLUGIN_ROOT}", PLUGIN_ROOT));
      assert.ok(
        expanded === PLUGIN_ROOT || expanded.startsWith(PLUGIN_ROOT + sep),
        `server ${name}: arg escapes the plugin root: ${arg}`,
      );
      assert.ok(existsSync(expanded), `server ${name}: referenced file missing: ${arg}`);
    }
  }
});

test("every skills/* child ships a SKILL.md with name + description frontmatter", () => {
  const skillsDir = join(PLUGIN_ROOT, "skills");
  const children = readdirSync(skillsDir).filter((entry) =>
    statSync(join(skillsDir, entry)).isDirectory(),
  );
  assert.ok(children.length > 0, "at least one skill expected");

  for (const child of children) {
    const skillPath = join(skillsDir, child, "SKILL.md");
    assert.ok(existsSync(skillPath), `missing ${skillPath}`);
    const raw = readFileSync(skillPath, "utf-8");

    const fm = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(raw);
    assert.ok(fm, `${child}/SKILL.md must start with YAML frontmatter`);

    const fields = {};
    for (const line of fm[1].split(/\r?\n/)) {
      const kv = /^([A-Za-z][\w-]*):\s*(.*)$/.exec(line);
      if (kv) fields[kv[1]] = kv[2].trim();
    }
    assert.ok(fields.name, `${child}/SKILL.md frontmatter missing name`);
    assert.ok(fields.description, `${child}/SKILL.md frontmatter missing description`);
    assert.equal(fields.name, child, `${child}/SKILL.md name should match its directory`);
    assert.match(fields.name, NAME_RE);
  }
});

test("relative markdown links inside skills resolve to real files", () => {
  const skillsDir = join(PLUGIN_ROOT, "skills");
  const children = readdirSync(skillsDir).filter((entry) =>
    statSync(join(skillsDir, entry)).isDirectory(),
  );
  for (const child of children) {
    const skillPath = join(skillsDir, child, "SKILL.md");
    const raw = readFileSync(skillPath, "utf-8");
    const links = [...raw.matchAll(/\]\((?!https?:|#)([^)]+)\)/g)].map((m) => m[1]);
    for (const rel of links) {
      assert.ok(
        existsSync(join(skillsDir, child, rel)),
        `${child}/SKILL.md links to missing file: ${rel}`,
      );
    }
  }
});

test("all .mjs files in the plugin pass node --check", () => {
  const files = listMjsFiles(PLUGIN_ROOT);
  assert.ok(files.length > 0, "expected vendored .mjs files");
  for (const file of files) {
    const result = spawnSync(process.execPath, ["--check", file], { encoding: "utf-8" });
    assert.equal(result.status, 0, `node --check failed for ${file}: ${result.stderr}`);
  }
});

test("vendored proxy chain resolves: mcp-proxy.mjs imports exist", () => {
  const proxy = readFileSync(join(PLUGIN_ROOT, "servers", "mcp-proxy.mjs"), "utf-8");
  const imports = [...proxy.matchAll(/from\s+"(\.[^"]+)"/g)].map((m) => m[1]);
  assert.ok(imports.length > 0);
  for (const rel of imports) {
    assert.ok(
      existsSync(join(PLUGIN_ROOT, "servers", rel)),
      `mcp-proxy.mjs import missing: ${rel}`,
    );
  }
});
