/**
 * The manifest contract, as this host reads it.
 *
 * `kimi.plugin.json` is the only thing the host looks at, and every rule here
 * has a specific failure behind it: an unknown field is ignored silently, a
 * `matcher: "*"` is a valid string but an invalid regex and so never matches,
 * an MCP server written as a path is not loaded at all, and a timeout shorter
 * than the hook's own budget means the host kills it mid-write.
 */

import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { BUDGETS } from "../../scripts/hook-stage.mjs";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

async function manifest() {
  return JSON.parse(await readFile(join(ROOT, "kimi.plugin.json"), "utf8"));
}

const EXPECTED_HOOKS = {
  SessionStart: "./scripts/session-start.mjs",
  UserPromptSubmit: "./scripts/user-prompt.mjs",
  PreToolUse: "./scripts/uri-guard.mjs",
  Stop: "./scripts/stop.mjs",
  PreCompact: "./scripts/pre-compact.mjs",
  SessionEnd: "./scripts/session-end.mjs",
};

test("the manifest declares the identity and version the package expects", async () => {
  const json = await manifest();
  assert.equal(json.name, "openviking-memory");
  // The host requires this shape; a name with a space or slash is refused.
  assert.match(json.name, /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:@[A-Za-z0-9_-]+)?$/);
  assert.equal(json.version, "0.6.0");
  assert.ok(json.description);
  assert.equal(json.license, "Apache-2.0");
  assert.ok(json.interface?.displayName);

  const pkg = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  assert.equal(pkg.name, "@openviking/kimi-code-memory-plugin");
  // The host reports the manifest version; npm reports the package's. They must
  // not disagree, or a user is told they run something they are not.
  assert.equal(pkg.version, json.version);
  assert.match(pkg.engines.node, /22\.19/);
});

test("all six hooks are inline objects with only the fields this host reads", async () => {
  const json = await manifest();
  assert.ok(Array.isArray(json.hooks), "hooks must be an inline array");

  for (const [event, command] of Object.entries(EXPECTED_HOOKS)) {
    const matches = json.hooks.filter((hook) => hook.event === event);
    assert.equal(matches.length, 1, `${event} must be registered exactly once`);
    const hook = matches[0];
    assert.equal(hook.command, `node ${command}`);
    for (const key of Object.keys(hook)) {
      assert.ok(["event", "matcher", "command", "timeout"].includes(key), `${event} has unsupported field '${key}'`);
    }
    assert.equal(typeof hook.timeout, "number");
    assert.ok(hook.timeout >= 1 && hook.timeout <= 600, `${event} timeout out of range`);
    // A matcher is a JavaScript regex here, not a glob; `*` never matches.
    assert.equal(hook.matcher, undefined, `${event} must not carry a matcher`);
    assert.equal(hook.cwd, undefined, "this host does not accept a per-hook cwd");
    assert.equal(hook.env, undefined, "this host does not accept a per-hook env");
  }
  assert.equal(json.hooks.length, Object.keys(EXPECTED_HOOKS).length);
});

test("every hook's internal budget is smaller than the host's timeout", async () => {
  const json = await manifest();
  const budgetFor = {
    SessionStart: BUDGETS["session-start"],
    UserPromptSubmit: BUDGETS["user-prompt"],
    PreToolUse: BUDGETS["uri-guard"],
    Stop: BUDGETS.stop,
    PreCompact: BUDGETS["pre-compact"],
    SessionEnd: BUDGETS["session-end"],
  };
  for (const hook of json.hooks) {
    const budget = budgetFor[hook.event];
    assert.ok(budget, `${hook.event} has no internal budget`);
    assert.ok(
      budget < hook.timeout * 1000,
      `${hook.event}: the ${budget}ms budget must be under the host's ${hook.timeout}s timeout`,
    );
  }
});

test("each hook command points at a file that exists", async () => {
  const json = await manifest();
  for (const hook of json.hooks) {
    const match = /^node (\.\/[^ ]+)$/.exec(hook.command);
    assert.ok(match, `unexpected command form: ${hook.command}`);
    const info = await stat(join(ROOT, match[1]));
    assert.ok(info.isFile(), `${match[1]} does not exist`);
  }
});

test("the MCP server is an inline stdio entry started from the plugin root", async () => {
  const json = await manifest();
  assert.equal(typeof json.mcpServers, "object");
  const server = json.mcpServers.openviking;
  assert.ok(server, "the openviking server is what the skills and the model call");
  assert.equal(server.transport, "stdio");
  assert.equal(server.command, "node");
  assert.deepEqual(server.args, ["./servers/mcp-proxy.mjs"]);
  // The host starts the proxy in the plugin root; a cwd here would be ignored.
  assert.equal(server.cwd, undefined);
  const info = await stat(join(ROOT, "servers", "mcp-proxy.mjs"));
  assert.ok(info.isFile());
});

test("the skills directory is a relative path inside the plugin", async () => {
  const json = await manifest();
  assert.equal(json.skills, "./skills/");
  const info = await stat(join(ROOT, "skills"));
  assert.ok(info.isDirectory());
  for (const skill of ["openviking-memory", "openviking-skills", "openviking-rule-search", "ov-experience-memory"]) {
    const text = await readFile(join(ROOT, "skills", skill, "SKILL.md"), "utf8");
    // Frontmatter has to start the file: a generated-from banner ahead of it
    // would break the loader.
    assert.match(text, /^---\n/, `${skill} must begin with YAML frontmatter`);
    const front = text.slice(4, text.indexOf("\n---", 4));
    assert.match(front, new RegExp(`^name:\\s*${skill}$`, "m"), `${skill} frontmatter name`);
    assert.match(front, /^description:/m, `${skill} frontmatter description`);
  }
});

test("no deprecated or unsupported extension point is declared", async () => {
  const json = await manifest();
  for (const key of ["tools", "apps", "inject", "configFile", "config_file", "bootstrap", "systemPrompt", "systemPromptPath"]) {
    assert.equal(json[key], undefined, `${key} is not a usable runtime extension point on this host`);
  }
  assert.equal(json.sessionStart, undefined);
});

test("the publishing fields the catalog schema requires are present and consistent", async () => {
  const json = await manifest();
  const iface = json.interface ?? {};

  // The catalog refuses an entry without an icon, and it must be a public URL:
  // a relative path or a data: URI is rejected at publish time.
  assert.equal(typeof iface.iconUrl, "string");
  assert.match(iface.iconUrl, /^https?:\/\/\S+$/, "iconUrl must be an absolute HTTP(S) URL");

  // A locale declaration without the directory would ship a manifest that
  // promises a translation the archive does not carry.
  assert.ok(["en-US", "zh-CN"].includes(iface.defaultLocale), "defaultLocale must be en-US or zh-CN");
  assert.equal(iface.locales, "./locales/");
  const locales = await stat(join(ROOT, "locales"));
  assert.ok(locales.isDirectory(), "the declared locales directory must exist");

  const other = iface.defaultLocale === "en-US" ? "zh-CN" : "en-US";
  const translated = JSON.parse(await readFile(join(ROOT, "locales", `${other}.json`), "utf8"));
  // A partial translation leaves the catalog showing English half way through a
  // Chinese page, so every text field the base interface declares must appear.
  for (const field of ["displayName", "shortDescription", "longDescription", "developerName"]) {
    assert.equal(typeof translated[field], "string", `${other}.json must translate ${field}`);
    assert.ok(translated[field].length > 0, `${other}.json ${field} must not be empty`);
  }

  // The plugin runs local hooks and a stdio MCP server, so it cannot be hosted.
  assert.equal(iface.hostKind, "local");
  assert.ok(
    ["PRODUCTIVITY", "FINANCE", "DEVELOPER", "LIFESTYLE_HEALTH", "SYSTEM"].includes(iface.category),
    "category must be one the catalog knows",
  );
});

test("the catalog fields do not drift from the manifest they describe", async () => {
  const json = await manifest();
  const iface = json.interface;
  // The card tagline is capped at 20 characters by the catalog.
  assert.ok(iface.shortDescription.length <= 20, "shortDescription is a card tagline of at most 20 characters");
  assert.ok(iface.longDescription.length <= 500, "longDescription is capped at 500 characters");
  // Every override has to name something the plugin actually declares, or the
  // catalog shows a display name for a tool the user never gets.
  for (const name of Object.keys(iface.mcpOverrides ?? {})) {
    assert.ok(json.mcpServers?.[name], `mcpOverrides names "${name}", which is not a declared server`);
  }
  for (const name of Object.keys(iface.skillOverrides ?? {})) {
    const info = await stat(join(ROOT, "skills", name)).catch(() => undefined);
    assert.ok(info?.isDirectory(), `skillOverrides names "${name}", which is not a shipped skill`);
  }
});
