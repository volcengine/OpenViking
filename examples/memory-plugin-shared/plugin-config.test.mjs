import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { HARNESS_KEYS, KNOBS } from "./lib/config-schema.mjs";
import { buildPluginConfig } from "./lib/plugin-config.mjs";
import { loadAgentHookConfig } from "./lib/agent-hook-runtime.mjs";
import { loadConfig as loadClaudeCode } from "../claude-code-memory-plugin/scripts/config.mjs";
import { loadConfig as loadCodex } from "../codex-memory-plugin/scripts/config.mjs";
import { loadConfig as loadOpencode } from "../opencode-plugin/lib/config.mjs";
import { resolveConfig as loadDsh } from "../dsh-memory-plugin/config.mjs";
import { loadConfig as loadPi } from "../pi-coding-agent-extension/config.ts";

/**
 * Every harness's loader, and the fields it is allowed to answer differently
 * from `buildPluginConfig`. That list is the whole of what stays local: a
 * loader that starts reinterpreting anything else fails this file.
 */
const LOADERS = {
  claude_code: {
    harness: "claude-code",
    load: (cwd) => loadClaudeCode(cwd),
    options: { manifestUrl: new URL("../claude-code-memory-plugin/.claude-plugin/plugin.json", import.meta.url), logFile: "cc-hooks.log", rootKeyFallback: true },
    owns: ["configPath", "credentialPath"],
  },
  codex: {
    harness: "codex",
    load: (cwd) => loadCodex(cwd),
    options: { manifestUrl: new URL("../codex-memory-plugin/.codex-plugin/plugin.json", import.meta.url), logFile: "codex-hooks.log" },
    owns: ["recallCompress"],
  },
  opencode: {
    harness: "opencode",
    load: (cwd) => loadOpencode("", cwd),
    options: { manifestUrl: new URL("../opencode-plugin/package.json", import.meta.url), logFile: "opencode-plugin.log", deriveEffectivePeer: true },
    // This plugin reads the repo-context switch as a section, not a flag.
    owns: ["repoContext"],
  },
  dsh: {
    harness: "dsh",
    load: (cwd) => loadDsh({}, process.env, cwd),
    options: { version: "0.4.0", deriveEffectivePeer: true },
    owns: ["peerId"],
  },
  pi: {
    harness: "pi",
    load: (cwd) => loadPi(cwd),
    options: { version: "0.3.0", deriveEffectivePeer: true },
    owns: ["peerId"],
  },
  cursor: { harness: "cursor", load: (cwd) => loadAgentHookConfig("cursor", cwd), options: { logFile: "cursor-hooks.log" }, owns: [] },
  trae: { harness: "trae", load: (cwd) => loadAgentHookConfig("trae", cwd), options: { logFile: "trae-hooks.log" }, owns: [] },
  trae_cn: { harness: "trae-cn", load: (cwd) => loadAgentHookConfig("trae-cn", cwd), options: { logFile: "trae-cn-hooks.log" }, owns: [] },
  zcode: { harness: "zcode", load: (cwd) => loadAgentHookConfig("zcode", cwd), options: { logFile: "zcode-hooks.log" }, owns: [] },
};

// openclaw declares its own settings in TypeScript and still resolves them
// itself; it joins this table when it moves onto the shared loader.
const WITHOUT_A_SHARED_LOADER = new Set(["openclaw"]);

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

/**
 * The files every loader is checked against. The first names every connection
 * field plus all four knobs the request omits unless configured, so a harness
 * that drops one shows up; the second puts a workspace file over them.
 */
const FIXTURES = [
  {
    ov: { server: { host: "127.0.0.1", port: 1933, root_api_key: "sk-root" } },
    cli: {
      ...CONNECTION,
      plugin: {
        recallLimit: 3,
        recallMaxTokens: 900,
        recallQueryExpansion: "off",
        recallCompressMaxBullets: 4,
      },
    },
  },
  {
    ov: { server: { root_api_key: "sk-root" } },
    cli: { ...CONNECTION, plugin: { recallLimit: 5 } },
    workspace: {
      version: 1,
      peer: { id: "team-a" },
      capture: { enabled: false },
      recall: { max_items: 8 },
    },
  },
];

test("the table covers every harness that has a shared loader", () => {
  const covered = new Set(Object.keys(LOADERS));
  for (const key of Object.values(HARNESS_KEYS)) {
    if (WITHOUT_A_SHARED_LOADER.has(key)) continue;
    assert.ok(covered.delete(key), `${key} has no entry in this file`);
  }
  assert.deepEqual([...covered], [], "an entry names a harness the schema does not");
});

for (const [key, entry] of Object.entries(LOADERS)) {
  test(`${key} answers what buildPluginConfig answers`, () => {
    for (const files of FIXTURES) {
      withFixture(files, ({ workspaceDir, otherDir }) => {
        for (const cwd of [workspaceDir, otherDir]) {
          const built = buildPluginConfig(entry.harness, { cwd, ...entry.options });
          const loaded = entry.load(cwd);

          const differing = Object.keys(built)
            .filter((field) => !entry.owns.includes(field))
            .filter((field) => JSON.stringify(built[field]) !== JSON.stringify(loaded[field]))
            .sort();
          assert.deepEqual(differing, [], `a loader answers a shared field itself (${cwd})`);
        }
      });
    }
  });

  test(`${key} tells a configured knob from a default`, () => {
    withFixture(FIXTURES[0], ({ otherDir }) => {
      const loaded = entry.load(otherDir);
      for (const knob of SEND_ONLY_WHEN_CONFIGURED) {
        assert.equal(loaded[`${knob.name}Configured`], true, `${knob.name} on ${key}`);
      }
    });

    // Several fields reach the server only when configured, so a default that
    // reported as configured would send this plugin's opinion as the user's.
    withFixture({}, ({ otherDir }) => {
      const loaded = entry.load(otherDir);
      for (const knob of SEND_ONLY_WHEN_CONFIGURED) {
        assert.equal(loaded[`${knob.name}Configured`], false, `${knob.name} on ${key}`);
        assert.equal(loaded[knob.name], knob.default, `${knob.name} on ${key}`);
      }
    });
  });

  test(`${key} resolves a knob through every layer`, () => {
    // Bottom up, dropping one rung at a time: a layer only wins because the one
    // above it is absent, which asserting on the winner alone cannot tell apart.
    const ov = { server: {}, [key]: { recallLimit: 2 } };
    withFixture({ ov }, ({ otherDir }) => {
      assert.equal(entry.load(otherDir).recallLimit, 2, "ov.conf's harness block outranks the default");
    });
    withFixture({ ov, cli: { ...CONNECTION, plugin: { recallLimit: 5 } } }, ({ otherDir }) => {
      assert.equal(entry.load(otherDir).recallLimit, 5, "ovcli.conf's shared plugin block outranks ov.conf");
    });
    withFixture({
      ov,
      cli: { ...CONNECTION, plugin: { recallLimit: 5, [key]: { recallLimit: 6 } } },
      workspace: { version: 1, recall: { max_items: 8 } },
    }, ({ workspaceDir, otherDir }) => {
      assert.equal(entry.load(otherDir).recallLimit, 6, "the per-harness block covers the shared one, which covers ov.conf");
      assert.equal(entry.load(workspaceDir).recallLimit, 8, "the workspace file outranks ovcli.conf");
      process.env.OPENVIKING_RECALL_LIMIT = "9";
      assert.equal(entry.load(workspaceDir).recallLimit, 9, "the environment outranks every file");
    });
  });

  test(`${key} reads the workspace layer of the cwd it is handed, not the process's`, () => {
    withFixture({
      workspace: { version: 1, capture: { enabled: false }, recall: { max_items: 8 } },
    }, ({ workspaceDir, otherDir }) => {
      const inside = entry.load(workspaceDir);
      assert.equal(inside.autoCapture, false);
      assert.equal(inside.recallLimit, 8);

      // Same process, another directory: the workspace file does not follow.
      const outside = entry.load(otherDir);
      assert.equal(outside.autoCapture, true);
      assert.equal(outside.recallLimit, 10);

      // A hook loads its config before the payload names a directory, so an
      // absent cwd has to mean this process's own.
      const origin = process.cwd();
      try {
        process.chdir(workspaceDir);
        assert.equal(entry.load(undefined).recallLimit, 8);
        assert.equal(entry.load("").recallLimit, 8);
      } finally {
        process.chdir(origin);
      }
    });
  });
}

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
