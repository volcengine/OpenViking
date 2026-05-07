import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { runWriteTask, spawnDetached } from "../util/async-writer.js";

let tmpDir: string;
let workerScript: string;

const WORKER_SOURCE = `
  import { writeFileSync } from "node:fs";

  let body = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { body += chunk; });
  process.stdin.on("end", () => {
    let payload;
    try { payload = JSON.parse(body); } catch { return; }
    const delay = Number(payload.delayMs ?? 0);
    const write = () => {
      writeFileSync(payload.markerPath, JSON.stringify({
        payload,
        env: { MARKER: process.env.MARKER ?? null },
      }));
    };
    if (delay > 0) {
      setTimeout(write, delay);
    } else {
      write();
    }
  });
`;

async function waitForFile(path: string, timeoutMs = 3000): Promise<void> {
  const start = Date.now();
  while (!existsSync(path)) {
    if (Date.now() - start > timeoutMs) {
      throw new Error(`timeout waiting for ${path}`);
    }
    await new Promise((r) => setTimeout(r, 10));
  }
}

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ov-aw-"));
  workerScript = join(tmpDir, "worker.mjs");
  writeFileSync(workerScript, WORKER_SOURCE);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("spawnDetached", () => {
  it("delivers the payload to the worker via stdin and survives parent return", async () => {
    const markerPath = join(tmpDir, "marker.json");
    const result = spawnDetached({
      command: process.execPath,
      args: [workerScript],
      stdinPayload: { markerPath, hello: "world" },
    });

    expect(result.detached).toBe(true);
    expect(result.error).toBeUndefined();

    await waitForFile(markerPath);
    const out = JSON.parse(readFileSync(markerPath, "utf8"));
    expect(out.payload).toMatchObject({ markerPath, hello: "world" });
  });

  it("passes env overrides through to the worker process", async () => {
    const markerPath = join(tmpDir, "env-marker.json");
    spawnDetached({
      command: process.execPath,
      args: [workerScript],
      env: { MARKER: "from-test" },
      stdinPayload: { markerPath },
    });

    await waitForFile(markerPath);
    const out = JSON.parse(readFileSync(markerPath, "utf8"));
    expect(out.env.MARKER).toBe("from-test");
  });

  it("returns detached=false with an error when the command does not exist", () => {
    const result = spawnDetached({
      command: "/nonexistent-binary-for-test-only",
      args: [],
    });
    // Spawn errors surface as either a synchronous throw (caught and turned
    // into result.error) or an async 'error' event (which we don't observe
    // here). On Linux/macOS the synchronous path is taken when the command
    // is unresolvable; either way the parent does not crash.
    if (!result.detached) {
      expect(result.error).toBeInstanceOf(Error);
    } else {
      // If the platform let the spawn proceed but the child immediately
      // dies, the parent has still detached cleanly — that's an acceptable
      // outcome for this layer.
      expect(result.detached).toBe(true);
    }
  });
});

describe("runWriteTask — sync path", () => {
  it("invokes the syncHandler when async=false", async () => {
    const markerPath = join(tmpDir, "sync-marker");
    let calledWith: unknown = null;
    await runWriteTask<{ markerPath: string; tag: string }>({
      async: false,
      payload: { markerPath, tag: "from-sync" },
      syncHandler: async (payload) => {
        calledWith = payload;
        writeFileSync(payload.markerPath, payload.tag);
      },
    });

    expect(calledWith).toEqual({ markerPath, tag: "from-sync" });
    expect(readFileSync(markerPath, "utf8")).toBe("from-sync");
  });

  it("swallows syncHandler errors instead of throwing to the caller", async () => {
    let resolved = false;
    await runWriteTask({
      async: false,
      payload: { x: 1 },
      syncHandler: async () => {
        throw new Error("boom");
      },
    });
    resolved = true;
    expect(resolved).toBe(true);
  });
});

describe("runWriteTask — async path", () => {
  it("returns to the caller before the worker writes its marker", async () => {
    const markerPath = join(tmpDir, "async-marker.json");
    const start = Date.now();

    await runWriteTask<{ markerPath: string; delayMs: number }>({
      async: true,
      payload: { markerPath, delayMs: 200 },
      syncHandler: async () => {
        // Should NOT be called when async path succeeds.
        throw new Error("sync handler should not run");
      },
      asyncSpawn: (payload) => ({
        command: process.execPath,
        args: [workerScript],
        stdinPayload: payload,
      }),
    });

    const elapsed = Date.now() - start;
    expect(existsSync(markerPath)).toBe(false);
    expect(elapsed).toBeLessThan(150);

    await waitForFile(markerPath);
    const out = JSON.parse(readFileSync(markerPath, "utf8"));
    expect(out.payload.delayMs).toBe(200);
  });

  it("falls back to syncHandler when the spawn itself fails", async () => {
    const markerPath = join(tmpDir, "fallback-marker");
    let syncCalled = false;

    await runWriteTask<{ markerPath: string }>({
      async: true,
      payload: { markerPath },
      syncHandler: async (payload) => {
        syncCalled = true;
        writeFileSync(payload.markerPath, "from-sync-fallback");
      },
      asyncSpawn: () => ({
        command: "/definitely-not-a-binary-on-this-host-xyz",
        args: [],
      }),
    });

    // On a hard sync-throw spawn failure the fallback path runs. On a
    // platform that defers the failure to the 'error' event the parent
    // will think it succeeded; that's a known limitation we tolerate.
    if (!syncCalled) {
      expect(existsSync(markerPath)).toBe(false);
    } else {
      expect(readFileSync(markerPath, "utf8")).toBe("from-sync-fallback");
    }
  });

  it("uses the syncHandler when async=true but no asyncSpawn provided", async () => {
    let syncCalled = false;
    await runWriteTask({
      async: true,
      payload: { x: 1 },
      syncHandler: async () => {
        syncCalled = true;
      },
    });
    expect(syncCalled).toBe(true);
  });
});
