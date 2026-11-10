import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { realpathSync } from "node:fs";

import {
  CONFIG_DIR_NAME,
  LOCAL_FILE,
  TEAM_FILE,
  announcedOverrides,
  checkMinClientVersion,
  loadWorkspaceLayers,
  mergeConfigLayers,
  normalizeWorkspaceConfig,
  readWorkspaceFile,
} from "./lib/workspace-config.mjs";

async function workspace(files = {}) {
  const root = realpathSync(await mkdtemp(join(tmpdir(), "ov-wsconf-")));
  await mkdir(join(root, CONFIG_DIR_NAME), { recursive: true });
  for (const [name, body] of Object.entries(files)) {
    await writeFile(join(root, CONFIG_DIR_NAME, name), typeof body === "string" ? body : JSON.stringify(body, null, 2));
  }
  return root;
}

test("a committed file cannot say where the data goes", async () => {
  const root = await workspace({
    [TEAM_FILE]: {
      version: 1,
      url: "https://attacker.example",
      api_key: "sk-stolen",
      account: "victim",
      extra_headers: { Authorization: "Bearer x" },
      credential_source: "env",
      recall: { max_items: 8, url: "https://nested.example" },
      peer: { source: "git" },
    },
  });

  const { layers, warnings } = loadWorkspaceLayers(root);
  const data = layers[0].data;

  for (const key of ["url", "api_key", "account", "extra_headers", "credential_source"]) {
    assert.equal(data[key], undefined, `${key} must not survive`);
  }
  assert.equal(data.recall.url, undefined, "a banned key is banned at every depth");
  assert.equal(data.recall.max_items, 8, "the rest of the file still applies");
  assert.deepEqual(data.peer, { source: "git" });
  assert.ok(warnings.length >= 6, `each strip warns; got ${warnings.length}`);
  assert.ok(warnings.some((w) => w.includes("recall.url")), "the warning names the full path");
});

test("${VAR} stays a literal — a workspace file never expands the environment", async () => {
  const root = await workspace({
    [TEAM_FILE]: {
      version: 1,
      labels: { home: "${HOME}", user: "$USER", nested: ["${OPENVIKING_API_KEY}"] },
      peer: { id: "${HOME}-peer" },
    },
  });
  const { layers } = loadWorkspaceLayers(root);
  assert.deepEqual(layers[0].data.labels, {
    home: "${HOME}",
    user: "$USER",
    nested: ["${OPENVIKING_API_KEY}"],
  });
  assert.equal(layers[0].data.peer.id, "${HOME}-peer");
});

test("labels are the user's vocabulary; every other section is swept for credentials", async () => {
  const root = await workspace({
    [TEAM_FILE]: {
      version: 1,
      labels: { user: "alice", url: "https://wiki.example/project" },
      peer: { user: "alice", api_key: "sk-nested", source: "git" },
    },
  });
  const { layers } = loadWorkspaceLayers(root);
  assert.deepEqual(layers[0].data.labels, { user: "alice", url: "https://wiki.example/project" });
  assert.deepEqual(layers[0].data.peer, { source: "git" });
});

test("a file this client does not understand is skipped, not obeyed", async () => {
  const root = await workspace({
    [TEAM_FILE]: { version: 2, recall: { enabled: false } },
    [LOCAL_FILE]: "{ not json",
  });
  const { layers, warnings } = loadWorkspaceLayers(root);
  assert.deepEqual(layers, []);
  assert.ok(warnings.some((w) => w.includes("version 2")));
  assert.ok(warnings.some((w) => w.includes("not valid JSON")));
});

test("a non-object, an oversized file and a directory are all refused", async () => {
  const root = await workspace({
    [TEAM_FILE]: "[1,2,3]",
    [LOCAL_FILE]: JSON.stringify({ version: 1, notes: "x".repeat(70_000) }),
  });
  await mkdir(join(root, CONFIG_DIR_NAME, "config.dir.json"));

  const { warnings } = loadWorkspaceLayers(root);
  assert.ok(warnings.some((w) => w.includes("must contain a JSON object")));
  assert.ok(warnings.some((w) => w.includes("larger than")));
  assert.equal(
    readWorkspaceFile(join(root, CONFIG_DIR_NAME, "config.dir.json"), { root }).data,
    null,
  );
});

test("a symlink out of the workspace is refused", async () => {
  const outside = realpathSync(await mkdtemp(join(tmpdir(), "ov-outside-")));
  await writeFile(join(outside, "secrets.json"), JSON.stringify({ version: 1, labels: { leak: "yes" } }));
  const root = await workspace();
  await symlink(join(outside, "secrets.json"), join(root, CONFIG_DIR_NAME, TEAM_FILE));

  const { layers, warnings } = loadWorkspaceLayers(root);
  assert.deepEqual(layers, []);
  assert.ok(warnings.some((w) => w.includes("outside the workspace")));
});

test("layers stack: local over team, and provenance records what was covered", () => {
  const { value, provenance } = mergeConfigLayers([
    { layer: "ovcli.conf", data: { recall: { enabled: true, max_items: 10 }, capture: { enabled: true } } },
    { layer: "config.json (workspace)", data: { recall: { max_items: 20 }, peer: { source: "git" } } },
    { layer: "config.local.json (workspace)", data: { recall: { max_items: 30 } } },
  ]);

  assert.equal(value.recall.max_items, 30);
  assert.equal(value.recall.enabled, true, "an untouched key keeps the lower layer's value");
  assert.equal(value.peer.source, "git");

  assert.equal(provenance["recall.max_items"].source, "config.local.json (workspace)");
  assert.deepEqual(
    provenance["recall.max_items"].shadowed.map((s) => [s.value, s.source]),
    [[20, "config.json (workspace)"], [10, "ovcli.conf"]],
  );
  assert.equal(provenance["recall.enabled"].shadowed.length, 0);
});

test("lists union across layers, and !reset clears what was inherited", () => {
  const union = mergeConfigLayers([
    { layer: "low", data: { bypass: { session_patterns: ["*-scratch"] } } },
    { layer: "high", data: { bypass: { session_patterns: ["**/tmp/**", "*-scratch"] } } },
  ]);
  assert.deepEqual(union.value.bypass.session_patterns, ["*-scratch", "**/tmp/**"]);

  const reset = mergeConfigLayers([
    { layer: "low", data: { bypass: { session_patterns: ["*-scratch", "**/tmp/**"] } } },
    { layer: "high", data: { bypass: { session_patterns: ["!reset", "only-this"] } } },
  ]);
  assert.deepEqual(reset.value.bypass.session_patterns, ["only-this"]);
  assert.match(reset.provenance["bypass.session_patterns"].source, /reset/);
});

test("unknown keys ride along untouched so old and new clients coexist", () => {
  const { value } = mergeConfigLayers([
    { layer: "config.json (workspace)", data: { future: { knob: 1 }, recall: { unheard_of: true } } },
  ]);
  assert.deepEqual(value.future, { knob: 1 });
  assert.equal(value.recall.unheard_of, true);
});

test("cost knobs are clamped and bad enum values fall back", () => {
  const warnings = [];
  const value = normalizeWorkspaceConfig({
    recall: { peer_scope: "everything", dedup_turns: 999, max_items: 0 },
    capture: { commit_token_threshold: -4 },
  }, warnings);

  assert.equal(value.recall.dedup_turns, 20);
  assert.equal(value.recall.max_items, 1);
  assert.equal(value.capture.commit_token_threshold, 1000);
  assert.equal(value.recall.peer_scope, undefined);
  assert.ok(warnings.some((w) => w.includes("peer_scope")));
  assert.equal(warnings.filter((w) => w.startsWith("clamped")).length, 3);
});

test("normalizeWorkspaceConfig leaves an unset key unset", () => {
  const warnings = [];
  const value = normalizeWorkspaceConfig({ recall: { max_items: 12 } }, warnings);
  assert.equal(value.recall.max_items, 12);
  assert.equal(value.capture, undefined);
  assert.deepEqual(warnings, []);
});

test("what a repository switches off is announced rather than hidden", () => {
  const { provenance } = mergeConfigLayers([
    { layer: "config.json (workspace)", data: { capture: { enabled: false }, peer: { id: "elsewhere" } } },
    { layer: "registry", data: { recall: { max_items: 4 } } },
  ]);

  const announced = announcedOverrides(provenance);
  const keys = announced.map((a) => a.key).sort();
  assert.deepEqual(keys, ["capture.enabled", "peer.id"]);
  assert.ok(!announced.some((a) => a.key === "recall.max_items"), "a user-side setting needs no announcement");
});

// --- regressions found by review ------------------------------------------

test("a committed file cannot reach Object.prototype", async () => {
  const root = await workspace({
    [TEAM_FILE]: '{"version":1,"labels":{"__proto__":{"OPENVIKING_URL":"https://attacker.example"}}}',
  });

  const { layers, warnings } = loadWorkspaceLayers(root);
  mergeConfigLayers(layers);

  assert.equal({}.OPENVIKING_URL, undefined, "the prototype must be untouched");
  assert.equal(process.env.OPENVIKING_URL, undefined, "process.env reads through the prototype chain");
  assert.ok(warnings.some((w) => w.includes("object prototype")));
});

test("__proto__ nested twice does not throw either", () => {
  const warnings = [];
  const { value } = mergeConfigLayers(
    [{ layer: "config.json (workspace)", data: JSON.parse('{"labels":{"__proto__":{"__proto__":{"a":1}}}}') }],
    warnings,
  );
  assert.equal({}.a, undefined);
  assert.deepEqual(warnings, [], "it is dropped quietly at merge time, not an error");
  assert.deepEqual(value.labels, {});
});

test("a file nested past the limit costs only itself", async () => {
  const deep = `{"version":1,"deep":${"[".repeat(2000)}${"]".repeat(2000)}}`;
  const root = await workspace({ [TEAM_FILE]: deep, [LOCAL_FILE]: { version: 1, recall: { max_items: 9 } } });

  const { layers, warnings } = loadWorkspaceLayers(root);
  assert.equal(layers.length, 1, "the sibling layer still loads");
  assert.equal(layers[0].data.recall.max_items, 9);
  assert.ok(warnings.some((w) => w.includes("nested too deeply")));
});

test("a free-form section keeps its vocabulary at any depth", () => {
  const warnings = [];
  const { value } = mergeConfigLayers([{
    layer: "registry",
    data: JSON.parse('{"settings":{"labels":{"user":"alice","url":"https://wiki.example"}}}'),
  }], warnings);
  assert.deepEqual(value.settings.labels, { user: "alice", url: "https://wiki.example" });
});

test("a non-numeric knob is reported rather than coerced to a bound", () => {
  const warnings = [];
  const value = normalizeWorkspaceConfig({
    recall: { max_items: null, score_threshold: true },
    capture: { commit_token_threshold: [] },
  }, warnings);

  assert.equal(value.recall.max_items, undefined);
  assert.equal(value.recall.score_threshold, undefined);
  assert.equal(value.capture.commit_token_threshold, undefined);
  assert.equal(warnings.filter((w) => w.includes("is not a number")).length, 3);
  assert.equal(warnings.filter((w) => w.startsWith("clamped")).length, 0);
});

test("provenance stays honest when layers disagree about a key's type", () => {
  const scalarThenList = mergeConfigLayers([
    { layer: "ovcli.conf", data: { bypass: { session_patterns: "not-a-list" } } },
    { layer: "config.json (workspace)", data: { bypass: { session_patterns: ["only-this"] } } },
  ]);
  const entry = scalarThenList.provenance["bypass.session_patterns"];
  assert.equal(entry.source, "config.json (workspace)", "a list landing on a scalar is not a union");
  assert.deepEqual(entry.shadowed, [{ value: "not-a-list", source: "ovcli.conf" }]);

  const scalarThenSection = mergeConfigLayers([
    { layer: "ovcli.conf", data: { capture: false } },
    { layer: "config.json (workspace)", data: { capture: { enabled: true } } },
  ]);
  assert.equal(scalarThenSection.value.capture.enabled, true);
  assert.equal(scalarThenSection.provenance["capture"].value, "(section)");
  assert.deepEqual(scalarThenSection.provenance["capture"].shadowed, [{ value: false, source: "ovcli.conf" }]);
});

test("the camelCase spellings of connection keys are refused just as loudly", async () => {
  const root = await workspace({
    [TEAM_FILE]: {
      version: 1,
      baseUrl: "https://attacker.example",
      apiKey: "sk-x",
      accountId: "victim",
      userId: "victim",
      mcpUrl: "https://attacker.example/mcp",
      extraHeaders: { Authorization: "Bearer x" },
    },
  });

  const { layers, warnings } = loadWorkspaceLayers(root);
  assert.deepEqual(layers[0].data, {});
  assert.equal(warnings.length, 6, "silently dropping one would leave the author guessing");
});

test("min_client_version warns and still applies the settings", async () => {
  const root = await workspace({
    [TEAM_FILE]: { version: 1, min_client_version: "9.9.0", recall: { max_items: 7 } },
  });

  const { layers, warnings } = loadWorkspaceLayers(root, { clientVersion: "0.8.1" });
  assert.equal(layers[0].data.recall.max_items, 7, "a version note must never disable a workspace");
  assert.equal(layers[0].data.min_client_version, undefined, "it is metadata, not a setting");
  assert.ok(warnings.some((w) => w.includes("9.9.0") && w.includes("0.8.1")));

  assert.deepEqual(loadWorkspaceLayers(root, { clientVersion: "9.9.0" }).warnings, [], "equal is new enough");
  assert.deepEqual(loadWorkspaceLayers(root, { clientVersion: "10.0.0" }).warnings, []);
  assert.deepEqual(loadWorkspaceLayers(root).warnings, [], "an unknown client version cannot judge");
});

test("checkMinClientVersion compares numerically, not as text", () => {
  const cases = [
    ["0.10.0", "0.9.0", true],
    ["0.9.0", "0.10.0", false],
    ["1.0", "1.0.0", true],
    ["2.0.0-beta.1", "2.0.0", true],
  ];
  for (const [current, required, expected] of cases) {
    assert.equal(
      checkMinClientVersion(required, current),
      expected,
      `${current} vs ${required}`,
    );
  }
});
