import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { loadConfig } from "../scripts/config.mjs";

function withEnv(nextEnv, fn) {
  const previous = { ...process.env };
  process.env = { ...previous, ...nextEnv };
  try {
    return fn();
  } finally {
    process.env = previous;
  }
}

function writeJson(filePath, value) {
  mkdirSync(dirname(filePath), { recursive: true });
  writeFileSync(filePath, JSON.stringify(value, null, 2));
}

function withTempDir(fn) {
  const dir = mkdtempSync(join(tmpdir(), "openviking-cc-config-"));
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("local mode reads apiKey and port fallback from ov.conf", () => {
  withTempDir((dir) => {
    const clientConfigPath = join(dir, "client-config.json");
    const serverConfigPath = join(dir, "ov.conf");

    writeJson(clientConfigPath, {
      mode: "local",
      agentId: "claude-code",
      recallLimit: 9,
    });
    writeJson(serverConfigPath, {
      server: {
        port: 2048,
        root_api_key: "local-root-key",
      },
    });

    const cfg = withEnv(
      {
        OPENVIKING_CC_CONFIG_FILE: clientConfigPath,
        OPENVIKING_CONFIG_FILE: serverConfigPath,
      },
      () => loadConfig(),
    );

    assert.equal(cfg.mode, "local");
    assert.equal(cfg.baseUrl, "http://127.0.0.1:2048");
    assert.equal(cfg.apiKey, "local-root-key");
    assert.equal(cfg.recallLimit, 9);
    assert.equal(cfg.configPath, clientConfigPath);
    assert.equal(cfg.serverConfigPath, serverConfigPath);
  });
});

test("remote mode uses client config baseUrl and apiKey", () => {
  withTempDir((dir) => {
    const clientConfigPath = join(dir, "client-config.json");
    const serverConfigPath = join(dir, "ov.conf");

    writeJson(clientConfigPath, {
      mode: "remote",
      baseUrl: "https://memory.example.com/api///",
      apiKey: "remote-key",
      timeoutMs: 2500,
    });
    writeJson(serverConfigPath, {
      server: {
        port: 9999,
        root_api_key: "should-not-be-used",
      },
    });

    const cfg = withEnv(
      {
        OPENVIKING_CC_CONFIG_FILE: clientConfigPath,
        OPENVIKING_CONFIG_FILE: serverConfigPath,
      },
      () => loadConfig(),
    );

    assert.equal(cfg.mode, "remote");
    assert.equal(cfg.baseUrl, "https://memory.example.com/api");
    assert.equal(cfg.apiKey, "remote-key");
    assert.equal(cfg.timeoutMs, 2500);
  });
});

test("local mode falls back to default port when ov.conf is absent", () => {
  withTempDir((dir) => {
    const clientConfigPath = join(dir, "client-config.json");
    const missingServerConfigPath = join(dir, "missing-ov.conf");

    writeJson(clientConfigPath, {
      mode: "local",
    });

    const cfg = withEnv(
      {
        OPENVIKING_CC_CONFIG_FILE: clientConfigPath,
        OPENVIKING_CONFIG_FILE: missingServerConfigPath,
      },
      () => loadConfig(),
    );

    assert.equal(cfg.mode, "local");
    assert.equal(cfg.baseUrl, "http://127.0.0.1:1933");
    assert.equal(cfg.apiKey, "");
    assert.equal(cfg.serverConfigError, null);
  });
});

test("string values support ${ENV_VAR} expansion", () => {
  withTempDir((dir) => {
    const clientConfigPath = join(dir, "client-config.json");

    writeJson(clientConfigPath, {
      mode: "remote",
      baseUrl: "${OV_TEST_BASE_URL}/",
      apiKey: "${OV_TEST_API_KEY}",
      debugLogPath: "${OV_TEST_LOG_DIR}/cc-hooks.log",
    });

    const cfg = withEnv(
      {
        OPENVIKING_CC_CONFIG_FILE: clientConfigPath,
        OV_TEST_BASE_URL: "https://remote.example.com",
        OV_TEST_API_KEY: "env-api-key",
        OV_TEST_LOG_DIR: join(dir, "logs"),
      },
      () => loadConfig(),
    );

    assert.equal(cfg.baseUrl, "https://remote.example.com");
    assert.equal(cfg.apiKey, "env-api-key");
    assert.equal(cfg.debugLogPath, join(dir, "logs", "cc-hooks.log"));
  });
});

test("remote mode requires baseUrl in client config", () => {
  withTempDir((dir) => {
    const clientConfigPath = join(dir, "client-config.json");

    writeJson(clientConfigPath, {
      mode: "remote",
      apiKey: "remote-key",
    });

    const result = spawnSync(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        'import { loadConfig } from "./scripts/config.mjs"; loadConfig();',
      ],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          OPENVIKING_CC_CONFIG_FILE: clientConfigPath,
        },
        encoding: "utf8",
      },
    );

    assert.equal(result.status, 1);
    assert.match(result.stderr, /baseUrl is required when mode is "remote"/);
  });
});
