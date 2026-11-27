import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HARNESS_KEYS, KNOBS } from "../agent-hook-plugin/lib/config-schema.mjs";
import { buildContextSearchBody } from "../agent-hook-plugin/lib/recall-core.mjs";
import { buildPluginConfig } from "../agent-hook-plugin/lib/plugin-config.mjs";
import { loadAgentHookConfig } from "../agent-hook-plugin/lib/agent-hook-runtime.mjs";
import claudeCode from "../agent-hook-plugin/hosts/claude-code/host.mjs";
import { loadConfig as loadCodex } from "../codex-memory-plugin/scripts/config.mjs";
import { loadConfig as loadOpencode } from "../opencode-plugin/lib/config.mjs";
import { resolveConfig as loadDsh } from "../dsh-memory-plugin/config.mjs";
import { loadConfig as loadPi } from "../pi-coding-agent-extension/config.ts";

const LOADERS = {
  claude_code: (cwd) => loadAgentHookConfig("claude-code", cwd, claudeCode.config),
  codex: loadCodex,
  opencode: (cwd) => loadOpencode("", cwd),
  dsh: (cwd) => loadDsh({}, process.env, cwd),
  pi: loadPi,
  ...Object.fromEntries(
    ["cursor", "trae", "trae-cn", "zcode", "kimicode", "codebuddy", "qoder", "antigravity"]
      .map((host) => [host.replace("-", "_"), (cwd) => loadAgentHookConfig(host, cwd)]),
  ),
};

const SEND_ONLY_WHEN_CONFIGURED = KNOBS.filter((knob) => knob.sendOnlyWhenConfigured);

const CONNECTION = {
  url: "http://127.0.0.1:1933",
  api_key: "sk-cli",
  account: "acct",
  user: "usr",
  actor_peer_id: "cli-peer",
};

/**
 * Run `fn` against a throwaway ~/.openviking pair plus two directories: a
 * workspace root (a bare `.git` is what makes it one, and `workspace` is
 * written to `.openviking/config.json`) and one that is neither.
 */
function withFixture({ ov = { server: {} }, cli = CONNECTION, workspace = null }, fn) {
  const dir = mkdtempSync(join(tmpdir(), "ov-plugin-config-"));
  const workspaceDir = join(dir, "workspace");
  const otherDir = join(dir, "other");
  const saved = Object.fromEntries(
    Object.keys(process.env).filter((key) => key.startsWith("OPENVIKING_") || key === "OV_DEBUG_LOG")
      .map((key) => [key, process.env[key]]),
  );
  try {
    for (const key of Object.keys(saved)) delete process.env[key];
    writeFileSync(join(dir, "ov.conf"), JSON.stringify(ov));
    writeFileSync(join(dir, "ovcli.conf"), JSON.stringify(cli));
    mkdirSync(join(workspaceDir, ".git"), { recursive: true });
    mkdirSync(join(workspaceDir, ".openviking"), { recursive: true });
    mkdirSync(otherDir, { recursive: true });
    if (workspace) {
      writeFileSync(join(workspaceDir, ".openviking", "config.json"), JSON.stringify(workspace));
    }
    process.env.OPENVIKING_CONFIG_FILE = join(dir, "ov.conf");
    process.env.OPENVIKING_CLI_CONFIG_FILE = join(dir, "ovcli.conf");
    // Keeps the identity cache and the workspace registry out of the real home.
    process.env.OPENVIKING_HOME = join(dir, "home");
    return fn({ workspaceDir, otherDir });
  } finally {
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("OPENVIKING_") || key === "OV_DEBUG_LOG") delete process.env[key];
    }
    Object.assign(process.env, saved);
    rmSync(dir, { recursive: true, force: true });
  }
}

test("each shared loader reads the configuration for its own host and workspace", () => {
  assert.deepEqual(
    Object.keys(LOADERS).sort(),
    Object.values(HARNESS_KEYS).filter((key) => key !== "openclaw").sort(),
  );
  const plugin = Object.fromEntries(
    Object.keys(LOADERS).map((key, index) => [key, { recallLimit: index + 2 }]),
  );
  withFixture({
    cli: { ...CONNECTION, plugin },
    workspace: { version: 1, capture: { enabled: false }, recall: { max_items: 20 } },
  }, ({ workspaceDir, otherDir }) => {
    const original = process.cwd();
    try {
      process.chdir(workspaceDir);
      for (const [host, load] of Object.entries(LOADERS)) {
        const outside = load(otherDir);
        assert.equal(outside.recallLimit, plugin[host].recallLimit, host);
        assert.equal(outside.autoCapture, true, host);
        assert.equal(outside.baseUrl, CONNECTION.url, host);
        assert.equal(outside.apiKey, CONNECTION.api_key, host);
        assert.equal(load(workspaceDir).recallLimit, 20, host);
        assert.equal(load(workspaceDir).autoCapture, false, host);
        assert.equal(load(undefined).recallLimit, 20, host);
        assert.equal(load("").recallLimit, 20, host);
      }
    } finally {
      process.chdir(original);
    }
  });
});

test("the shared settings default WM flags off, and both config and env can enable them", () => {
  for (const enabled of [undefined, false, true]) {
    withFixture({
      cli: { ...CONNECTION, plugin: { resumeArchiveInject: enabled, takeoverEnabled: enabled } },
    }, ({ otherDir }) => {
      const load = () => buildPluginConfig("codex", { cwd: otherDir });
      const configured = load();
      assert.equal(configured.resumeArchiveInject, enabled ?? false);
      assert.equal(configured.takeoverEnabled, enabled ?? false);
      process.env.OPENVIKING_RESUME_ARCHIVE_INJECT = "1";
      process.env.OPENVIKING_TAKEOVER = "1";
      const overridden = load();
      assert.equal(overridden.resumeArchiveInject, true);
      assert.equal(overridden.takeoverEnabled, true);
    });
  }
});

test("the shared loader distinguishes defaults from knobs the user configured", () => {
  const plugin = {
    recallLimit: 3,
    recallMaxTokens: 900,
    recallQueryExpansion: "off",
    recallCompressMaxBullets: 4,
  };
  for (const configured of [false, true]) {
    withFixture({ cli: { ...CONNECTION, plugin: configured ? plugin : {} } }, ({ otherDir }) => {
      const loaded = buildPluginConfig("codex", { cwd: otherDir });
      for (const knob of SEND_ONLY_WHEN_CONFIGURED) {
        assert.equal(loaded[`${knob.name}Configured`], configured, knob.name);
        if (!configured) assert.equal(loaded[knob.name], knob.default, knob.name);
      }
    });
  }
});

test("the shared loader resolves a knob through every config layer", () => {
  const ov = { server: {}, codex: { recallLimit: 2 } };
  withFixture({ ov }, ({ otherDir }) => {
    assert.equal(buildPluginConfig("codex", { cwd: otherDir }).recallLimit, 2);
  });
  withFixture({ ov, cli: { ...CONNECTION, plugin: { recallLimit: 5 } } }, ({ otherDir }) => {
    assert.equal(buildPluginConfig("codex", { cwd: otherDir }).recallLimit, 5);
  });
  withFixture({
    ov,
    cli: { ...CONNECTION, plugin: { recallLimit: 5, codex: { recallLimit: 6 } } },
    workspace: { version: 1, recall: { max_items: 8 } },
  }, ({ workspaceDir, otherDir }) => {
    const limitAt = (cwd) => buildPluginConfig("codex", { cwd }).recallLimit;
    assert.equal(limitAt(otherDir), 6);
    assert.equal(limitAt(workspaceDir), 8);
    process.env.OPENVIKING_RECALL_LIMIT = "9";
    assert.equal(limitAt(workspaceDir), 9);
  });
});

// The version reaches the workspace layers as well as the User-Agent, which is
// what lets a `min_client_version` be compared against anything.
test("the assembler reports the client version it stamps on the User-Agent", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-plugin-config-version-"));
  try {
    const cfg = buildPluginConfig("codex", {
      cwd: dir,
      version: "1.2.3",
      env: { OPENVIKING_HOME: join(dir, "home") },
    });
    assert.equal(cfg.clientVersion, "1.2.3");
    assert.equal(cfg.userAgent, "openviking-memory-codex/1.2.3");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// An older install pointed OPENVIKING_CONFIG_FILE at ovcli.conf, and the
// credential chain still honours that. The knobs have to come out of the same
// file, or a `plugin` section supplies credentials and no behaviour.
test("the plugin section is read from the file the credential chain accepted", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-plugin-config-compat-"));
  const file = join(dir, "ovcli.conf");
  try {
    writeFileSync(file, JSON.stringify({
      url: "http://legacy:1933",
      api_key: "sk-legacy",
      plugin: { recallLimit: 3, codex: { recallLimit: 4 } },
    }));
    const cfg = buildPluginConfig("codex", {
      cwd: dir,
      env: { OPENVIKING_CONFIG_FILE: file, OPENVIKING_HOME: join(dir, "home") },
    });

    assert.equal(cfg.apiKeySource, "ovcli");
    assert.equal(cfg.cliPath, file);
    assert.equal(cfg.recallLimit, 4);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// The chain the doctors and both reference tables describe, end to end.
test("ovcli.conf's plugin section outranks ov.conf, under ovcli.conf's own key", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-plugin-config-key-"));
  const env = {
    OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "ovcli.conf"),
    OPENVIKING_HOME: join(dir, "home"),
  };
  try {
    writeFileSync(join(dir, "ov.conf"), JSON.stringify({
      server: { root_api_key: "sk-root" },
      codex: { apiKey: "sk-ov-section", accountId: "acct-ov", userId: "usr-ov" },
    }));
    const write = (cli) => writeFileSync(join(dir, "ovcli.conf"), JSON.stringify(cli));
    const build = () => buildPluginConfig("codex", { cwd: dir, env });
    const connection = (cfg) => ({ apiKey: cfg.apiKey, account: cfg.account, user: cfg.user });

    write({
      plugin: {
        codex: { apiKey: "sk-plugin-codex", accountId: "acct-plugin-codex", userId: "usr-plugin-codex" },
        apiKey: "sk-plugin",
        accountId: "acct-plugin",
        userId: "usr-plugin",
      },
    });
    assert.deepEqual(connection(build()), {
      apiKey: "sk-plugin-codex",
      account: "acct-plugin-codex",
      user: "usr-plugin-codex",
    });

    write({ plugin: { apiKey: "sk-plugin", accountId: "acct-plugin", userId: "usr-plugin" } });
    assert.deepEqual(connection(build()), {
      apiKey: "sk-plugin",
      account: "acct-plugin",
      user: "usr-plugin",
    });

    write({
      url: "http://127.0.0.1:1933",
      api_key: "sk-cli",
      account: "acct-cli",
      user: "usr-cli",
      plugin: { apiKey: "sk-plugin", accountId: "acct-plugin", userId: "usr-plugin" },
    });
    assert.deepEqual(connection(build()), {
      apiKey: "sk-cli",
      account: "acct-cli",
      user: "usr-cli",
    });

    write({ plugin: {} });
    assert.deepEqual(connection(build()), {
      apiKey: "sk-ov-section",
      account: "acct-ov",
      user: "usr-ov",
    });
    assert.equal(build().apiKeySource, "ov");

    assert.deepEqual(
      connection(buildPluginConfig("codex", {
        cwd: dir,
        env: {
          ...env,
          OPENVIKING_API_KEY: "sk-env",
          OPENVIKING_ACCOUNT: "acct-env",
          OPENVIKING_USER: "usr-env",
        },
      })),
      { apiKey: "sk-env", account: "acct-env", user: "usr-env" },
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the skill catalog is on by default and its budget clamps to the declared range", () => {
  withFixture({}, ({ otherDir }) => {
    const defaults = buildPluginConfig("codex", { cwd: otherDir });
    assert.equal(defaults.skillCatalog, true);
    assert.equal(defaults.skillCatalogTokenBudget, 1200);

    process.env.OPENVIKING_SKILL_CATALOG = "false";
    process.env.OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET = "999999";
    const overridden = buildPluginConfig("codex", { cwd: otherDir });
    assert.equal(overridden.skillCatalog, false);
    assert.equal(overridden.skillCatalogTokenBudget, 20000);
  });
});

test("shared compression settings reach the HTTP request", () => {
  withFixture({}, ({ otherDir }) => {
    const load = () => buildPluginConfig("codex", { cwd: otherDir });
    for (const [mode, expected] of [["server", true], ["auto", "auto"], ["off", undefined]]) {
      process.env.OPENVIKING_RECALL_COMPRESS = mode;
      assert.equal(buildContextSearchBody(load(), { localCompressorAvailable: false }).rewrite, expected);
    }
  });
});
