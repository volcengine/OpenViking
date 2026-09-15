import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { resolveOpenVikingCredentials } from "./lib/credentials.mjs";

async function tempJson(prefix, value) {
  const dir = await mkdtemp(join(tmpdir(), prefix));
  const path = join(dir, "ovcli.conf");
  await writeFile(path, JSON.stringify(value, null, 2) + "\n");
  return { dir, path };
}

test("credential env wins over ovcli config by default", async () => {
  const { dir, path } = await tempJson("ov-creds-cli-", {
    url: "https://ov.example.com",
    api_key: "cli-key",
    account: "default",
    user: "zeus",
    actor_peer_id: "peer-a",
  });
  try {
    const creds = resolveOpenVikingCredentials({
      OPENVIKING_CLI_CONFIG_FILE: path,
      OPENVIKING_URL: "https://stale.example.com",
      OPENVIKING_MCP_URL: "https://stale.example.com/mcp",
      OPENVIKING_API_KEY: "stale-key",
      OPENVIKING_ACCOUNT: "stale-account",
      OPENVIKING_USER: "stale-user",
      OPENVIKING_PEER_ID: "stale-peer",
    });

    assert.equal(creds.credentialSource, "env");
    assert.equal(creds.baseUrl, "https://stale.example.com");
    assert.equal(creds.mcpUrl, "https://stale.example.com/mcp");
    assert.equal(creds.apiKey, "stale-key");
    assert.equal(creds.account, "stale-account");
    assert.equal(creds.user, "stale-user");
    assert.equal(creds.peerId, "stale-peer");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("env source can be forced explicitly", async () => {
  const { dir, path } = await tempJson("ov-creds-env-", {
    url: "https://ov.example.com",
    api_key: "cli-key",
    user: "zeus",
  });
  try {
    const creds = resolveOpenVikingCredentials({
      OPENVIKING_CREDENTIAL_SOURCE: "env",
      OPENVIKING_CLI_CONFIG_FILE: path,
      OPENVIKING_URL: "https://env.example.com",
      OPENVIKING_MCP_URL: "https://env.example.com/custom-mcp",
      OPENVIKING_API_KEY: "env-key",
      OPENVIKING_ACCOUNT: "env-account",
      OPENVIKING_USER: "env-user",
      OPENVIKING_PEER_ID: "env-peer",
    });

    assert.equal(creds.credentialSource, "env");
    assert.equal(creds.baseUrl, "https://env.example.com");
    assert.equal(creds.mcpUrl, "https://env.example.com/custom-mcp");
    assert.equal(creds.apiKey, "env-key");
    assert.equal(creds.account, "env-account");
    assert.equal(creds.user, "env-user");
    assert.equal(creds.peerId, "env-peer");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("ovcli source can be forced explicitly without inheriting env key", async () => {
  const { dir, path } = await tempJson("ov-creds-noauth-", {
    url: "http://127.0.0.1:1933",
  });
  try {
    const creds = resolveOpenVikingCredentials({
      OPENVIKING_CLI_CONFIG_FILE: path,
      OPENVIKING_CREDENTIAL_SOURCE: "ovcli",
      OPENVIKING_API_KEY: "stale-key",
    });

    assert.equal(creds.credentialSource, "ovcli");
    assert.equal(creds.baseUrl, "http://127.0.0.1:1933");
    assert.equal(creds.apiKey, "");
    assert.equal(creds.hasApiKey, false);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("credentialPath names the file that supplied the api_key", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-creds-path-"));
  const cliPath = join(dir, "ovcli.conf");
  const ovPath = join(dir, "ov.conf");
  await writeFile(cliPath, JSON.stringify({ url: "http://127.0.0.1:1933", api_key: "cli-key" }));
  await writeFile(ovPath, JSON.stringify({ server: { root_api_key: "root-key" } }));
  const env = { OPENVIKING_CLI_CONFIG_FILE: cliPath, OPENVIKING_CONFIG_FILE: ovPath };
  try {
    assert.equal(resolveOpenVikingCredentials(env).credentialPath, cliPath);

    // env beats both files, so no file is named.
    assert.equal(
      resolveOpenVikingCredentials({ ...env, OPENVIKING_API_KEY: "env-key" }).credentialPath,
      "",
    );

    // A tuning-only ovcli.conf carries no credentials, so the chain lands on ov.conf.
    await writeFile(cliPath, JSON.stringify({ plugin: { recallCompress: "off" } }));
    const creds = resolveOpenVikingCredentials(env);
    assert.equal(creds.apiKey, "root-key");
    assert.equal(creds.credentialPath, ovPath);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("each harness reads its own ov.conf section, not codex's", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-creds-harness-"));
  const ovPath = join(dir, "ov.conf");
  await writeFile(ovPath, JSON.stringify({
    server: { root_api_key: "root-key" },
    codex: { apiKey: "sk-codex", accountId: "acct-codex", userId: "user-codex", peerId: "peer-codex" },
    opencode: { apiKey: "sk-opencode", accountId: "acct-opencode", userId: "user-opencode", peerId: "peer-opencode" },
    trae_cn: { apiKey: "sk-trae-cn" },
  }));
  const env = {
    OPENVIKING_CONFIG_FILE: ovPath,
    OPENVIKING_CLI_CONFIG_FILE: join(dir, "absent-ovcli.conf"),
  };
  try {
    const codex = resolveOpenVikingCredentials(env);
    assert.equal(codex.apiKey, "sk-codex");
    assert.equal(codex.account, "acct-codex");
    assert.equal(codex.user, "user-codex");
    assert.equal(codex.peerId, "peer-codex");

    const opencode = resolveOpenVikingCredentials(env, "opencode");
    assert.equal(opencode.apiKey, "sk-opencode");
    assert.equal(opencode.account, "acct-opencode");
    assert.equal(opencode.user, "user-opencode");
    assert.equal(opencode.peerId, "peer-opencode");

    // Either spelling of a harness name reaches the snake_case section.
    assert.equal(resolveOpenVikingCredentials(env, "trae-cn").apiKey, "sk-trae-cn");

    // A harness with no section of its own inherits nothing from codex's; the
    // chain carries on to server.root_api_key as it always did.
    const cursor = resolveOpenVikingCredentials(env, "cursor");
    assert.equal(cursor.apiKey, "root-key");
    assert.equal(cursor.account, "");
    assert.equal(cursor.user, "");
    assert.equal(cursor.peerId, "");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("ovcli.conf still outranks the harness section, which outranks the root key", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-creds-order-"));
  const ovPath = join(dir, "ov.conf");
  const cliPath = join(dir, "ovcli.conf");
  await writeFile(ovPath, JSON.stringify({
    server: { root_api_key: "root-key" },
    opencode: { apiKey: "sk-opencode", peerId: "peer-opencode" },
  }));
  await writeFile(cliPath, JSON.stringify({ url: "http://127.0.0.1:1933", actor_peer_id: "cli-peer" }));
  const env = { OPENVIKING_CONFIG_FILE: ovPath, OPENVIKING_CLI_CONFIG_FILE: cliPath };
  try {
    // ovcli.conf carries credential fields, so this is the pinned-file mode:
    // ov.conf is not consulted at all and the harness section stays out.
    const pinned = resolveOpenVikingCredentials(env, "opencode");
    assert.equal(pinned.credentialSource, "ovcli");
    assert.equal(pinned.apiKey, "");
    assert.equal(pinned.peerId, "cli-peer");

    // With ovcli.conf holding tuning only, the harness section supplies the key
    // and still sits ahead of server.root_api_key.
    await writeFile(cliPath, JSON.stringify({ plugin: { recallCompress: "off" } }));
    const layered = resolveOpenVikingCredentials(env, "opencode");
    assert.equal(layered.apiKey, "sk-opencode");
    assert.equal(layered.credentialPath, ovPath);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
