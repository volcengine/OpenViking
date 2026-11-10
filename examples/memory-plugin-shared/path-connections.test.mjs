import assert from "node:assert/strict";
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildProxyConnection, resolveConnection } from "./lib/credentials.mjs";
import { buildPluginConfig } from "./lib/plugin-config.mjs";
import { credentialSources } from "./lib/doctor-core.mjs";
import { createPendingQueue, enqueue, listPending } from "./lib/pending-queue.mjs";
import {
  expectExit, runHookScript, scrubOpenVikingEnv, withMockOpenViking,
  withPendingDir, writeCredentialFiles, writeJson,
} from "./testing/support.mjs";
import { rm } from "node:fs/promises";

async function fixture(run) {
  const files = await writeCredentialFiles("ov-path-connections-", { ovcli: {} });
  const work = join(files.dir, "work");
  const personal = join(files.dir, "personal");
  mkdirSync(work);
  mkdirSync(personal);
  const config = {
    url: "https://personal.invalid", api_key: "personal-key", account: "personal", user: "me",
    connections: [{ path: work, url: "https://work.invalid", api_key: "work-key", account: "work" }],
  };
  const write = () => writeFileSync(files.cliPath, JSON.stringify(config));
  write();
  try { await run({ ...files, work, personal, config, write }); }
  finally { await rm(files.dir, { recursive: true, force: true }); }
}

test("longest directory match overrides only supplied connection fields", async () => {
  await fixture(({ env, work, personal, config, write }) => {
    config.connections.push({ path: join(work, "client"), account: "client" });
    write();
    const get = (cwd) => resolveConnection("codex", { env, cwd });
    const nested = get(join(work, "client", "repo"));
    assert.equal(nested.account, "client");
    // Missing fields inherit from the top level, not another matching entry.
    assert.equal(nested.apiKey, "personal-key");
    assert.equal(nested.baseUrl, "https://personal.invalid");
    assert.equal(nested.user, "me");
    assert.equal(get(work).apiKey, "work-key");
    assert.equal(get(join(work, "repo")).account, "work");
    assert.equal(get(`${work}-other`).account, "personal");
    assert.equal(get(personal).account, "personal");
  });
});

test("home paths, path normalization, and Windows case are supported", async () => {
  await fixture(({ env, config, write }) => {
    config.connections = [{ path: "~/work/../work/", account: "work" }];
    write();
    const cwd = join(homedir(), "work", "repo");
    assert.equal(resolveConnection("codex", { env, cwd }).account, "work");
    if (process.platform === "win32") {
      assert.equal(resolveConnection("codex", { env, cwd: cwd.toUpperCase() }).account, "work");
    }
  });
});

test("environment and host overrides keep their precedence over path connections", async () => {
  await fixture(({ env, work }) => {
    const overrides = { ...env, OPENVIKING_API_KEY: "env-key", OPENVIKING_ACCOUNT: "env-account" };
    const get = (options = {}) => resolveConnection("codex", { cwd: work, env: overrides, ...options });
    assert.equal(get().apiKey, "env-key");
    assert.equal(get().account, "env-account");
    assert.equal(get().baseUrl, "https://work.invalid");
    assert.equal(get({ hostInput: { apiKey: "host-key", account: "host-account" } }).apiKey, "host-key");
    assert.equal(get({ hostInput: { account: "host-account" } }).account, "host-account");
    assert.equal(get({ env: { ...overrides, OPENVIKING_CREDENTIAL_SOURCE: "cli" } }).apiKey, "work-key");
    const environmentOnly = get({ env: { ...overrides, OPENVIKING_CREDENTIAL_SOURCE: "env" } });
    assert.equal(environmentOnly.apiKey, "env-key");
    assert.equal(environmentOnly.baseUrl, "http://127.0.0.1:1933");
    assert.equal(environmentOnly.connectionPath, "");
  });
});

test("each session pins its first selection across hooks, cwd changes and new entries", async () => {
  await fixture(({ env, work, personal, config, write }) => {
    const get = (cwd, sessionId, harness = "codex") => buildPluginConfig(harness, { env, cwd, sessionId });
    assert.equal(get(work, "work-session").account, "work");
    assert.equal(get(personal, "work-session").account, "work");
    assert.equal(get(personal, "personal-session").account, "personal");
    assert.equal(get(work, "personal-session").account, "personal");
    assert.equal(get(personal, "work-session", "claude-code").account, "personal");
    config.connections.push({ path: join(work, "repo"), account: "new-client" });
    config.connections[0].api_key = "rotated-work-key";
    write();
    assert.equal(get(join(work, "repo"), "work-session").account, "work");
    assert.equal(get(personal, "work-session").apiKey, "rotated-work-key");
    assert.equal(get(join(work, "repo"), "new-session").account, "new-client");
    const stateDir = join(env.OPENVIKING_HOME, "connection-state");
    for (const filename of readdirSync(stateDir)) {
      const raw = readFileSync(join(stateDir, filename), "utf8");
      assert.doesNotMatch(raw, /personal-key|work-key|rotated-work-key/);
    }
  });
});

test("removing a selected entry fails instead of sending the session to the default tenant", async () => {
  await fixture(({ env, work, personal, config, write }) => {
    resolveConnection("codex", { env, cwd: work, sessionId: "pinned" });
    config.connections = [{ path: personal, account: "other" }];
    write();
    assert.throws(() => resolveConnection("codex", { env, cwd: personal, sessionId: "pinned" }), /removed/);
    config.connections = [];
    write();
    assert.throws(() => resolveConnection("codex", { env, cwd: personal, sessionId: "pinned" }), /removed/);
  });
});

test("editing a pinned route cannot switch an active session's server or identity", async () => {
  await fixture(({ env, work, config, write }) => {
    resolveConnection("codex", { env, cwd: work, sessionId: "pinned" });
    config.connections[0].account = "different-account";
    write();
    assert.throws(() => resolveConnection("codex", { env, cwd: work, sessionId: "pinned" }), /identity changed/);
    assert.equal(resolveConnection("codex", { env, cwd: work, sessionId: "new" }).account, "different-account");
  });
});

test("relative or malformed entries are rejected without exposing credential values", async () => {
  await fixture(({ env, work, config, write }) => {
    for (const entry of [{ path: "relative", api_key: "private-value" }, { path: work, api_key: 42 }, null]) {
      config.connections = [entry];
      write();
      assert.throws(() => resolveConnection("codex", { env, cwd: work }), (error) => {
        assert.doesNotMatch(error.message, /private-value/);
        return /connections/.test(error.message);
      });
    }
    config.connections = [{ path: work }, { path: join(work, ".") }];
    write();
    assert.throws(() => resolveConnection("codex", { env, cwd: work }), /unique/);
  });
});

test("workspace files cannot supply connection entries, and a proxy uses its supplied launch directory", async () => {
  await fixture(({ env, work, personal }) => {
    mkdirSync(join(work, ".openviking"));
    writeFileSync(join(work, ".openviking", "config.json"), JSON.stringify({
      version: 1, connections: [{ path: work, api_key: "repo-key", account: "repo-account" }],
    }));
    assert.equal(buildPluginConfig("codex", { env, cwd: work }).account, "work");
    assert.equal(buildProxyConnection("agent-plugins", { env, cwd: work }).apiKey, "work-key");
    assert.equal(buildProxyConnection("agent-plugins", { env, cwd: personal }).apiKey, "personal-key");
  });
});

test("doctor identifies the selected entry without exposing its key", async () => {
  await fixture(({ env, work, config, cliPath }) => {
    const cfg = buildPluginConfig("codex", { env, cwd: work });
    const sources = credentialSources(cfg, { ok: true, path: cliPath, data: config }, { ok: false, path: "" });
    assert.match(sources.apiKey, /connections\[/);
    assert.match(sources.account, /connections\[/);
    assert.doesNotMatch(JSON.stringify(sources), /work-key|personal-key/);
  });
});

test("offline queues are isolated between connections and from unscoped legacy writes", async () => {
  await fixture(async ({ env, work, personal }) => {
    await withPendingDir(async () => {
      const queue = (cwd) => createPendingQueue(buildPluginConfig("codex", { env, cwd }).connectionScope);
      const workQueue = queue(work);
      const personalQueue = queue(personal);
      await Promise.all([
        workQueue.enqueue("addMessage", "same-session", { content: "work" }),
        personalQueue.enqueue("addMessage", "same-session", { content: "personal" }),
        enqueue("addMessage", "legacy-session", { content: "legacy" }),
      ]);
      const sent = [];
      const result = await workQueue.replayPending(async (_path, init) => {
        sent.push(JSON.parse(init.body).content);
        return { ok: true };
      }, () => {});
      assert.equal(result.replayed, 1);
      assert.deepEqual(sent, ["work"]);
      assert.equal((await personalQueue.listPending()).length, 1);
      assert.equal((await listPending()).length, 1);
    });
  });
});

test("real hook processes use the input cwd and retain that connection on later hooks", async (t) => {
  const restore = scrubOpenVikingEnv();
  try {
    await fixture(async ({ env, dir, work, personal, config, write }) => {
      await withMockOpenViking((_req, res) => writeJson(res, { status: "ok", result: {} }), async (baseUrl, requests) => {
        config.url = baseUrl;
        config.connections[0].url = baseUrl;
        config.plugin = { workspacePeer: false, profileTokenBudget: 0, skillCatalog: false, recallCompress: "off" };
        write();
        const childEnv = {
          ...env, HOME: dir, USERPROFILE: dir, OPENVIKING_WRITE_PATH_ASYNC: "0",
          OPENVIKING_PENDING_DIR: join(dir, "pending"), OPENVIKING_STATE_DIR: join(dir, "state"),
          OPENVIKING_HOOK_STATE_DIR: join(dir, "hook-state"),
        };
        const cases = [
          ["claude-code", "../claude-code-memory-plugin/scripts/pre-compact.mjs", []],
          ["codex", "../codex-memory-plugin/scripts/pre-compact-capture.mjs", []],
          ["cursor", "../agent-hook-plugin/scripts/hook.mjs", ["sessionStart", "cursor"]],
        ];
        for (const [harness, path, argv] of cases) {
          await t.test(harness, async () => {
            const script = fileURLToPath(new URL(path, import.meta.url));
            for (const cwd of [work, personal]) {
              requests.length = 0;
              const result = expectExit(await runHookScript(script, {
                cwd: personal,
                argv: harness === "cursor" && cwd === personal ? ["beforeSubmitPrompt", "cursor"] : argv,
                env: childEnv,
                input: { cwd, prompt: "Recall the project conventions", session_id: `${harness}-pinned`, conversation_id: `${harness}-pinned`, source: "startup" },
              }));
              assert.ok(requests.length > 0, result.stderr);
              assert.ok(requests.every((req) => req.headers.authorization === "Bearer work-key"));
              assert.ok(requests.every((req) => req.headers["x-openviking-account"] === "work"));
            }
          });
        }
      });
    });
  } finally { restore(); }
});
