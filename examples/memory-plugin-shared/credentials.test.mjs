import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { KNOB_BY_NAME } from "./lib/config-schema.mjs";
import { CONNECTION_ENV_VARS, CREDENTIAL_ENV_VARS, resolveConnection } from "./lib/credentials.mjs";

/**
 * Run `fn` against an ovcli.conf / ov.conf pair in a throwaway directory. A
 * file given as `null` is not written, so the chain sees it as absent.
 */
async function withFiles({ ovcli = null, ov = null }, fn) {
  const dir = await mkdtemp(join(tmpdir(), "ov-creds-"));
  const cliPath = join(dir, "ovcli.conf");
  const ovPath = join(dir, "ov.conf");
  if (ovcli) await writeFile(cliPath, JSON.stringify(ovcli));
  if (ov) await writeFile(ovPath, JSON.stringify(ov));
  const env = { OPENVIKING_CLI_CONFIG_FILE: cliPath, OPENVIKING_CONFIG_FILE: ovPath };
  const write = async (files) => {
    if (files.ovcli) await writeFile(cliPath, JSON.stringify(files.ovcli));
    if (files.ov) await writeFile(ovPath, JSON.stringify(files.ov));
  };
  try {
    return await fn({ env, cliPath, ovPath, write });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

const identity = (c) => ({ apiKey: c.apiKey, account: c.account, user: c.user });

test("credential env wins over ovcli config by default", async () => {
  await withFiles({
    ovcli: { url: "https://ov.example.com", api_key: "cli-key", account: "default", user: "zeus", actor_peer_id: "peer-a" },
  }, async ({ env }) => {
    const creds = resolveConnection("codex", {
      env: {
        ...env,
        OPENVIKING_URL: "https://stale.example.com",
        OPENVIKING_MCP_URL: "https://stale.example.com/mcp",
        OPENVIKING_API_KEY: "stale-key",
        OPENVIKING_ACCOUNT: "stale-account",
        OPENVIKING_USER: "stale-user",
        OPENVIKING_PEER_ID: "stale-peer",
      },
    });

    assert.equal(creds.credentialSource, "env");
    assert.equal(creds.baseUrl, "https://stale.example.com");
    assert.equal(creds.mcpUrl, "https://stale.example.com/mcp");
    assert.deepEqual(identity(creds), { apiKey: "stale-key", account: "stale-account", user: "stale-user" });
    assert.equal(creds.peerId, "stale-peer");
  });
});

test("env source can be forced explicitly", async () => {
  await withFiles({ ovcli: { url: "https://ov.example.com", api_key: "cli-key", user: "zeus" } }, async ({ env }) => {
    const creds = resolveConnection("codex", {
      env: {
        ...env,
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_URL: "https://env.example.com",
        OPENVIKING_MCP_URL: "https://env.example.com/custom-mcp",
        OPENVIKING_API_KEY: "env-key",
        OPENVIKING_ACCOUNT: "env-account",
        OPENVIKING_USER: "env-user",
        OPENVIKING_PEER_ID: "env-peer",
      },
    });

    assert.equal(creds.credentialSource, "env");
    assert.equal(creds.baseUrl, "https://env.example.com");
    assert.equal(creds.mcpUrl, "https://env.example.com/custom-mcp");
    assert.deepEqual(identity(creds), { apiKey: "env-key", account: "env-account", user: "env-user" });
    assert.equal(creds.peerId, "env-peer");
  });
});

test("a forced env source reads no file, so an empty variable stays empty", async () => {
  await withFiles({
    ovcli: { url: "https://cli.example.com", api_key: "cli-key", account: "acct-cli", actor_peer_id: "cli-peer", plugin: { userId: "usr-plugin", authMode: "trusted" } },
    ov: { server: { url: "https://ov.example.com", root_api_key: "root-key", auth_mode: "trusted" }, codex: { apiKey: "sk-codex" } },
  }, async ({ env }) => {
    const bare = resolveConnection("codex", { env: { ...env, OPENVIKING_CREDENTIAL_SOURCE: "env" }, rootKeyFallback: true });
    assert.equal(bare.credentialSource, "env");
    assert.equal(bare.baseUrl, "http://127.0.0.1:1933");
    assert.deepEqual(identity(bare), { apiKey: "", account: "", user: "" });
    assert.equal(bare.apiKeySource, "none");
    assert.equal(bare.peerId, "");
    assert.equal(bare.authMode, "api_key");

    const named = resolveConnection("codex", {
      env: { ...env, OPENVIKING_CREDENTIALS_SOURCE: "environment", OPENVIKING_URL: "https://env.example.com", OPENVIKING_USER: "usr-env" },
    });
    assert.equal(named.mcpUrl, "https://env.example.com/mcp");
    assert.deepEqual(identity(named), { apiKey: "", account: "", user: "usr-env" });
    assert.equal(named.authMode, "trusted");
  });
});

test("ovcli source can be forced explicitly without inheriting env key", async () => {
  await withFiles({ ovcli: { url: "http://127.0.0.1:1933" } }, async ({ env }) => {
    const creds = resolveConnection("codex", {
      env: { ...env, OPENVIKING_CREDENTIAL_SOURCE: "ovcli", OPENVIKING_API_KEY: "stale-key" },
    });

    assert.equal(creds.credentialSource, "ovcli");
    assert.equal(creds.baseUrl, "http://127.0.0.1:1933");
    assert.equal(creds.apiKey, "");
    assert.equal(creds.hasApiKey, false);
  });
});

test("only a variable that names the connection takes the chain off ovcli.conf", async () => {
  await withFiles({ ovcli: { url: "https://ov.example.com", api_key: "cli-key" } }, async ({ env }) => {
    const tuned = resolveConnection("codex", {
      env: { ...env, OPENVIKING_AUTH_MODE: "api_key", OPENVIKING_TIMEOUT_MS: "5000" },
    });
    assert.equal(tuned.credentialSource, "ovcli");
    assert.equal(tuned.apiKey, "cli-key");

    for (const name of CREDENTIAL_ENV_VARS) {
      const value = name.endsWith("URL") ? "https://env.example.com" : "from-env";
      assert.equal(
        resolveConnection("codex", { env: { ...env, [name]: value } }).credentialSource,
        "env",
        `${name} names part of the connection`,
      );
    }
  });
});

test("the key's source names the layer and the file behind it", async () => {
  await withFiles({
    ovcli: { url: "http://127.0.0.1:1933", api_key: "cli-key" },
    ov: { server: { root_api_key: "root-key" } },
  }, async ({ env, cliPath, ovPath, write }) => {
    const source = (c) => [c.apiKeySource, c.credentialPath];
    assert.deepEqual(source(resolveConnection("codex", { env })), ["ovcli", cliPath]);

    // env beats both files, so no file is named; a host beats env.
    assert.deepEqual(source(resolveConnection("codex", { env: { ...env, OPENVIKING_API_KEY: "env-key" } })), ["env", ""]);
    assert.deepEqual(source(resolveConnection("codex", { env, hostInput: { apiKey: "host-key" } })), ["host", ""]);

    // A tuning-only ovcli.conf carries no credentials, so the chain lands on ov.conf.
    await write({ ovcli: { plugin: { recallCompress: "off" } } });
    const creds = resolveConnection("codex", { env });
    assert.equal(creds.apiKey, "root-key");
    assert.deepEqual(source(creds), ["ov", ovPath]);

    await write({ ovcli: { url: "http://127.0.0.1:1933" } });
    assert.deepEqual(source(resolveConnection("codex", { env })), ["none", ""]);
  });
});

test("each harness reads its own ov.conf section, not codex's", async () => {
  await withFiles({
    ov: {
      server: { root_api_key: "root-key" },
      codex: { apiKey: "sk-codex", accountId: "acct-codex", userId: "user-codex", peerId: "peer-codex" },
      opencode: { apiKey: "sk-opencode", accountId: "acct-opencode", userId: "user-opencode", peerId: "peer-opencode" },
      trae_cn: { apiKey: "sk-trae-cn" },
    },
  }, async ({ env }) => {
    const codex = resolveConnection("codex", { env });
    assert.deepEqual(identity(codex), { apiKey: "sk-codex", account: "acct-codex", user: "user-codex" });
    assert.equal(codex.peerId, "peer-codex");

    const opencode = resolveConnection("opencode", { env });
    assert.deepEqual(identity(opencode), { apiKey: "sk-opencode", account: "acct-opencode", user: "user-opencode" });
    assert.equal(opencode.peerId, "peer-opencode");

    // Either spelling of a harness name reaches the snake_case section.
    assert.equal(resolveConnection("trae-cn", { env }).apiKey, "sk-trae-cn");

    // A harness with no section of its own inherits nothing from codex's; the
    // chain carries on to server.root_api_key as it always did.
    const cursor = resolveConnection("cursor", { env });
    assert.deepEqual(identity(cursor), { apiKey: "root-key", account: "", user: "" });
    assert.equal(cursor.peerId, "");
  });
});

test("ovcli.conf's plugin keys rank under its own fields and over ov.conf", async () => {
  await withFiles({
    ovcli: {
      plugin: {
        apiKey: "sk-plugin",
        accountId: "acct-plugin",
        userId: "usr-plugin",
        claude_code: { apiKey: "sk-snake", accountId: "acct-snake" },
        "claude-code": { apiKey: "sk-hyphen" },
      },
    },
    ov: { server: { root_api_key: "root-key" }, claude_code: { apiKey: "sk-ov", accountId: "acct-ov", userId: "usr-ov" } },
  }, async ({ env, write }) => {
    // Shared keys, then plugin.claude_code over them, then the hyphenated
    // spelling over that — the merge every knob in that section follows.
    assert.deepEqual(identity(resolveConnection("claude-code", { env })), {
      apiKey: "sk-hyphen",
      account: "acct-snake",
      user: "usr-plugin",
    });

    // A number is a value, as the knob schema coerces it.
    await write({ ovcli: { plugin: { accountId: 12345 } } });
    assert.equal(resolveConnection("codex", { env }).account, "12345");

    // An empty scoped key blanks the shared one and the chain carries on.
    await write({ ovcli: { plugin: { apiKey: "sk-plugin", claude_code: { apiKey: "" } } } });
    assert.equal(resolveConnection("claude-code", { env }).apiKey, "sk-ov");

    await write({ ovcli: { url: "http://127.0.0.1:1933", api_key: "sk-cli", plugin: { apiKey: "sk-plugin", userId: "usr-plugin" } } });
    const pinned = resolveConnection("claude-code", { env });
    assert.equal(pinned.credentialSource, "ovcli");
    assert.equal(pinned.apiKey, "sk-cli");
    assert.equal(pinned.user, "usr-plugin");
  });
});

test("pinned, the key falls back to ov.conf's harness section but the identity does not", async () => {
  await withFiles({
    ovcli: { url: "http://127.0.0.1:1933", actor_peer_id: "cli-peer" },
    ov: {
      server: { root_api_key: "root-key" },
      opencode: { apiKey: "sk-opencode", accountId: "acct-opencode", userId: "usr-opencode", peerId: "peer-opencode" },
    },
  }, async ({ env, ovPath, write }) => {
    const pinned = resolveConnection("opencode", { env });
    assert.equal(pinned.credentialSource, "ovcli");
    assert.deepEqual(identity(pinned), { apiKey: "sk-opencode", account: "", user: "" });
    assert.equal(pinned.peerId, "cli-peer");
    assert.equal(pinned.credentialPath, ovPath);

    // With ovcli.conf holding tuning only, the whole harness section applies
    // and still sits ahead of server.root_api_key.
    await write({ ovcli: { plugin: { recallCompress: "off" } } });
    const layered = resolveConnection("opencode", { env });
    assert.deepEqual(identity(layered), { apiKey: "sk-opencode", account: "acct-opencode", user: "usr-opencode" });
    assert.equal(layered.peerId, "peer-opencode");
  });
});

test("the root key ends every unpinned chain, and a pinned one only on request", async () => {
  await withFiles({ ov: { server: { root_api_key: "root-key" } } }, async ({ env, write }) => {
    const key = (rootKeyFallback) => resolveConnection("codex", { env, rootKeyFallback }).apiKey;
    assert.equal(key(false), "root-key");
    assert.equal(key(true), "root-key");

    await write({ ovcli: { url: "http://127.0.0.1:1933" } });
    assert.equal(key(false), "");
    assert.equal(key(true), "root-key");
  });
});

test("the auth mode walks host, env, plugin, harness section, server, then the identity", async () => {
  const ovcli = {
    api_key: "sk-cli",
    account: "acct",
    plugin: { auth_mode: "trusted", codex: { authMode: "api_key" } },
  };
  const ov = { server: { auth_mode: "trusted" }, codex: { authMode: "trusted" } };
  await withFiles({ ovcli, ov }, async ({ env, write }) => {
    const mode = (options = {}) => {
      const c = resolveConnection("codex", { env, ...options });
      assert.equal(c.sendIdentityHeaders, c.authMode === "trusted");
      return c.authMode;
    };
    assert.equal(mode(), "api_key", "plugin.codex outranks the shared plugin key");
    assert.equal(mode({ env: { ...env, OPENVIKING_AUTH_MODE: "TRUSTED" } }), "trusted");
    assert.equal(
      mode({ env: { ...env, OPENVIKING_AUTH_MODE: "trusted" }, hostInput: { authMode: "api_key" } }),
      "api_key",
      "a host's own answer outranks the environment",
    );

    await write({ ovcli: { ...ovcli, plugin: { auth_mode: "api_key" } } });
    assert.equal(mode(), "api_key", "the snake_case spelling counts");

    await write({ ovcli: { ...ovcli, plugin: {} }, ov: { ...ov, codex: { auth_mode: "api_key" } } });
    assert.equal(mode(), "api_key", "ov.conf's harness section ranks over server.auth_mode");

    await write({ ov: { server: { auth_mode: "api_key" } } });
    assert.equal(mode(), "api_key");

    await write({ ov: { server: { auth_mode: "bogus" } } });
    assert.equal(mode(), "trusted", "an identity means the deployment expects one");

    await write({ ovcli: { api_key: "sk-cli" } });
    assert.equal(mode(), "api_key");
    assert.equal(mode({ hostInput: { user: "host-user" } }), "trusted", "a host's identity counts too");
  });
});

test("a host's endpoint outranks every URL, including an explicit MCP URL", async () => {
  await withFiles({ ovcli: { url: "https://cli.example.com/" } }, async ({ env }) => {
    assert.equal(resolveConnection("dsh", { env }).mcpUrl, "https://cli.example.com/mcp");

    const withMcpUrl = { ...env, OPENVIKING_MCP_URL: "https://env.example.com/custom" };
    assert.equal(resolveConnection("dsh", { env: withMcpUrl }).mcpUrl, "https://env.example.com/custom");

    const hosted = resolveConnection("dsh", { env: withMcpUrl, hostInput: { baseUrl: "https://host.example.com/" } });
    assert.equal(hosted.baseUrl, "https://host.example.com");
    assert.equal(hosted.mcpUrl, "https://host.example.com/mcp");
  });
});

// credentials.mjs reads these knobs out of the plugin section itself, so the
// portable bundle does not have to ship the schema; every spelling the schema
// accepts has to land.
test("every spelling of a connection knob in the schema reaches the chain", async () => {
  const values = { apiKey: ["apiKey", "sk-plugin"], accountId: ["account", "acct-plugin"], userId: ["user", "usr-plugin"], authMode: ["authMode", "trusted"] };
  for (const [name, [field, value]] of Object.entries(values)) {
    const knob = KNOB_BY_NAME.get(name);
    assert.equal(knob.capability, "connection");
    if (knob.env) assert.ok(CONNECTION_ENV_VARS.includes(knob.env), `${knob.env} is a connection variable`);
    for (const spelling of [name, ...(knob.aliases || [])]) {
      await withFiles({ ovcli: { plugin: { codex: { [spelling]: value } } }, ov: { server: { auth_mode: "api_key" } } }, async ({ env }) => {
        assert.equal(resolveConnection("codex", { env })[field], value, `plugin.codex.${spelling}`);
      });
    }
  }
});
