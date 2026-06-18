import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { resolveOpenVikingCredentials, syncMcpConfig } from "./ov-credentials.mjs";

async function tempJson(prefix, value) {
  const dir = await mkdtemp(join(tmpdir(), prefix));
  const path = join(dir, "ovcli.conf");
  await writeFile(path, JSON.stringify(value, null, 2) + "\n");
  return { dir, path };
}

test("active ovcli config wins over stale credential env by default", async () => {
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

    assert.equal(creds.credentialSource, "ovcli");
    assert.equal(creds.baseUrl, "https://ov.example.com");
    assert.equal(creds.mcpUrl, "https://ov.example.com/mcp");
    assert.equal(creds.apiKey, "cli-key");
    assert.equal(creds.account, "default");
    assert.equal(creds.user, "zeus");
    assert.equal(creds.peerId, "peer-a");
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

test("ovcli config without api_key does not inherit stale env key", async () => {
  const { dir, path } = await tempJson("ov-creds-noauth-", {
    url: "http://127.0.0.1:1933",
  });
  try {
    const creds = resolveOpenVikingCredentials({
      OPENVIKING_CLI_CONFIG_FILE: path,
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

test("syncMcpConfig with peerId maps actor-peer header", async () => {
  const { dir, path } = await tempJson("ov-creds-mcp-peer-", {
    url: "https://ov.example.com",
    api_key: "cli-key",
    actor_peer_id: "peer-a",
  });
  const mcpPath = join(dir, ".mcp.json");
  await writeFile(mcpPath, JSON.stringify({
    mcpServers: {
      "openviking-memory": {
        url: "__OPENVIKING_MCP_URL__",
        bearer_token_env_var: "STALE_KEY",
        env_http_headers: {},
      },
    },
  }, null, 2) + "\n");

  try {
    syncMcpConfig(mcpPath, { OPENVIKING_CLI_CONFIG_FILE: path });
    const rendered = JSON.parse(await readFile(mcpPath, "utf-8"));
    const server = rendered.mcpServers["openviking-memory"];

    assert.equal(server.url, "https://ov.example.com/mcp");
    assert.equal(server.bearer_token_env_var, "OPENVIKING_API_KEY");
    assert.deepEqual(server.env_http_headers, {
      "X-OpenViking-Account": "OPENVIKING_ACCOUNT",
      "X-OpenViking-User": "OPENVIKING_USER",
      "X-OpenViking-Actor-Peer": "OPENVIKING_PEER_ID",
    });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("syncMcpConfig without peerId omits actor-peer header (symmetric to bearer)", async () => {
  const { dir, path } = await tempJson("ov-creds-mcp-nopeer-", {
    url: "https://ov.example.com",
    api_key: "cli-key",
  });
  const mcpPath = join(dir, ".mcp.json");
  await writeFile(mcpPath, JSON.stringify({
    mcpServers: {
      "openviking-memory": {
        url: "__OPENVIKING_MCP_URL__",
        bearer_token_env_var: "STALE_KEY",
        env_http_headers: {},
      },
    },
  }, null, 2) + "\n");

  try {
    syncMcpConfig(mcpPath, { OPENVIKING_CLI_CONFIG_FILE: path });
    const rendered = JSON.parse(await readFile(mcpPath, "utf-8"));
    const server = rendered.mcpServers["openviking-memory"];

    assert.equal(server.url, "https://ov.example.com/mcp");
    assert.equal(server.bearer_token_env_var, "OPENVIKING_API_KEY");
    assert.deepEqual(server.env_http_headers, {
      "X-OpenViking-Account": "OPENVIKING_ACCOUNT",
      "X-OpenViking-User": "OPENVIKING_USER",
    });
    assert.equal(
      Object.prototype.hasOwnProperty.call(server.env_http_headers, "X-OpenViking-Actor-Peer"),
      false,
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("syncMcpConfig drops stale actor-peer header when peer is unset", async () => {
  const { dir, path } = await tempJson("ov-creds-mcp-drop-peer-", {
    url: "https://ov.example.com",
    api_key: "cli-key",
  });
  const mcpPath = join(dir, ".mcp.json");
  await writeFile(mcpPath, JSON.stringify({
    mcpServers: {
      "openviking-memory": {
        url: "__OPENVIKING_MCP_URL__",
        bearer_token_env_var: "STALE_KEY",
        env_http_headers: {
          "X-OpenViking-Account": "OPENVIKING_ACCOUNT",
          "X-OpenViking-User": "OPENVIKING_USER",
          "X-OpenViking-Actor-Peer": "OPENVIKING_PEER_ID",
        },
      },
    },
  }, null, 2) + "\n");

  try {
    const changed = syncMcpConfig(mcpPath, { OPENVIKING_CLI_CONFIG_FILE: path });
    assert.equal(changed, true);
    const rendered = JSON.parse(await readFile(mcpPath, "utf-8"));
    const server = rendered.mcpServers["openviking-memory"];

    assert.equal(
      Object.prototype.hasOwnProperty.call(server.env_http_headers, "X-OpenViking-Actor-Peer"),
      false,
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
