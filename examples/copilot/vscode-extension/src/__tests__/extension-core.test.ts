import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildActivationHandle,
  runDeactivate,
  type ActivationHandle,
  type FlushableQueue,
} from "../extension-core";

const ENV_KEYS = [
  "OPENVIKING_MEMORY_ENABLED",
  "OPENVIKING_URL",
  "OPENVIKING_API_KEY",
  "OPENVIKING_ACCOUNT",
  "OPENVIKING_USER",
  "OPENVIKING_AGENT_ID",
  "OPENVIKING_DEBUG",
  "OPENVIKING_CONFIG_FILE",
  "OPENVIKING_CLI_CONFIG_FILE",
] as const;

let savedEnv: Record<string, string | undefined>;

function clearEnv(): void {
  for (const k of ENV_KEYS) delete process.env[k];
}

function isolateConfigFiles(): void {
  // Point the loader at paths that won't exist so `isPluginEnabled()`
  // is decided purely by env vars in tests, regardless of whatever
  // real config files the developer has under `~/.openviking/`.
  process.env["OPENVIKING_CONFIG_FILE"] = "/tmp/__ov_test_nonexistent_ov.conf";
  process.env["OPENVIKING_CLI_CONFIG_FILE"] = "/tmp/__ov_test_nonexistent_ovcli.conf";
}

beforeEach(() => {
  savedEnv = {};
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
  clearEnv();
  isolateConfigFiles();
});

afterEach(() => {
  clearEnv();
  for (const [k, v] of Object.entries(savedEnv)) {
    if (v !== undefined) process.env[k] = v;
  }
});

const STUB_FETCH = (() => Promise.resolve(new Response("{}", { status: 200 }))) as unknown as typeof fetch;

describe("buildActivationHandle — gating", () => {
  it("returns null when the plugin is disabled (no env, no config files)", () => {
    const handle = buildActivationHandle({ fetchImpl: STUB_FETCH });
    expect(handle).toBeNull();
  });

  it("returns null when explicitly disabled via enabledOverride", () => {
    const handle = buildActivationHandle({ enabledOverride: false, fetchImpl: STUB_FETCH });
    expect(handle).toBeNull();
  });

  it("activates when OPENVIKING_MEMORY_ENABLED=1 is set", () => {
    process.env["OPENVIKING_MEMORY_ENABLED"] = "1";
    const handle = buildActivationHandle({ fetchImpl: STUB_FETCH });
    expect(handle).not.toBeNull();
    expect(handle!.cfg.agentId).toBe("copilot-vscode");
  });

  it("activates when enabledOverride=true is set, even without env / file", () => {
    const handle = buildActivationHandle({ enabledOverride: true, fetchImpl: STUB_FETCH });
    expect(handle).not.toBeNull();
  });
});

describe("buildActivationHandle — config flow", () => {
  it("hostOverrides take precedence over env and built-in defaults", () => {
    process.env["OPENVIKING_URL"] = "http://from-env.example";
    const handle = buildActivationHandle({
      enabledOverride: true,
      fetchImpl: STUB_FETCH,
      hostOverrides: { baseUrl: "http://from-host.example" },
    });
    expect(handle!.cfg.baseUrl).toBe("http://from-host.example");
  });

  it("env vars take precedence over built-in defaults when no hostOverrides", () => {
    process.env["OPENVIKING_URL"] = "http://from-env.example";
    process.env["OPENVIKING_API_KEY"] = "env-key";
    const handle = buildActivationHandle({ enabledOverride: true, fetchImpl: STUB_FETCH });
    expect(handle!.cfg.baseUrl).toBe("http://from-env.example");
    expect(handle!.cfg.apiKey).toBe("env-key");
  });

  it("agent id defaults to 'copilot-vscode'", () => {
    const handle = buildActivationHandle({ enabledOverride: true, fetchImpl: STUB_FETCH });
    expect(handle!.cfg.agentId).toBe("copilot-vscode");
  });

  it("exposes a logger and a client wired with the resolved cfg", () => {
    const handle = buildActivationHandle({
      enabledOverride: true,
      fetchImpl: STUB_FETCH,
      hostOverrides: { apiKey: "host-key" },
    });
    expect(handle!.client).toBeDefined();
    expect(handle!.logger).toBeDefined();
    expect(handle!.cfg.apiKey).toBe("host-key");
  });
});

describe("registerCommitQueue + runDeactivate", () => {
  function makeHandle(): ActivationHandle {
    const handle = buildActivationHandle({ enabledOverride: true, fetchImpl: STUB_FETCH });
    if (!handle) throw new Error("expected activation");
    return handle;
  }

  it("registerCommitQueue is idempotent (same instance counted once)", () => {
    const handle = makeHandle();
    const q: FlushableQueue = { flush: vi.fn(async () => {}) };
    handle.registerCommitQueue(q);
    handle.registerCommitQueue(q);
    expect(handle.registeredCount()).toBe(1);
  });

  it("runDeactivate flushes every registered queue", async () => {
    const handle = makeHandle();
    const q1: FlushableQueue = { flush: vi.fn(async () => {}) };
    const q2: FlushableQueue = { flush: vi.fn(async () => {}) };
    handle.registerCommitQueue(q1);
    handle.registerCommitQueue(q2);

    await runDeactivate(handle);

    expect(q1.flush).toHaveBeenCalledTimes(1);
    expect(q2.flush).toHaveBeenCalledTimes(1);
  });

  it("runDeactivate is a no-op for a null handle (disabled plugin)", async () => {
    await expect(runDeactivate(null)).resolves.toBeUndefined();
  });

  it("a thrown flush() does NOT prevent other queues from flushing", async () => {
    const handle = makeHandle();
    const failing: FlushableQueue = { flush: vi.fn(async () => { throw new Error("boom"); }) };
    const ok: FlushableQueue = { flush: vi.fn(async () => {}) };
    handle.registerCommitQueue(failing);
    handle.registerCommitQueue(ok);

    await expect(runDeactivate(handle)).resolves.toBeUndefined();
    expect(failing.flush).toHaveBeenCalled();
    expect(ok.flush).toHaveBeenCalled();
  });

  it("runDeactivate with no registered queues completes cleanly", async () => {
    const handle = makeHandle();
    await expect(runDeactivate(handle)).resolves.toBeUndefined();
  });
});
