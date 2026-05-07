import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createDebugLogger, __test__ } from "../debug/logger.js";

let tmpDir: string;
let logPath: string;

function readLines(): Array<Record<string, unknown>> {
  if (!existsSync(logPath)) return [];
  return readFileSync(logPath, "utf8")
    .split("\n")
    .filter((l) => l.length > 0)
    .map((l) => JSON.parse(l) as Record<string, unknown>);
}

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ov-logger-"));
  logPath = join(tmpDir, "nested", "copilot-vscode.log");
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("createDebugLogger — disabled mode", () => {
  it("is a no-op when cfg.debug is false (no file created)", () => {
    const log = createDebugLogger({ debug: false, debugLogPath: logPath });
    log.log("anything", { foo: 1 });
    expect(existsSync(logPath)).toBe(false);
  });

  it("creates the parent directory when first writing", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    log.log("started");
    expect(existsSync(logPath)).toBe(true);
  });
});

describe("createDebugLogger — line shape", () => {
  it("writes one JSON object per line with ts/scope/event and merged fields", () => {
    const log = createDebugLogger(
      { debug: true, debugLogPath: logPath },
      { now: () => "2026-05-07T20:30:00.000Z", scope: "recall" },
    );
    log.log("hit", { query: "auth migration", count: 3 });
    log.log("done", { ms: 42 });

    const lines = readLines();
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatchObject({
      ts: "2026-05-07T20:30:00.000Z",
      scope: "recall",
      event: "hit",
      query: "auth migration",
      count: 3,
    });
    expect(lines[1]).toMatchObject({
      ts: "2026-05-07T20:30:00.000Z",
      scope: "recall",
      event: "done",
      ms: 42,
    });
  });

  it("child() produces a logger with a different scope sharing the same file", () => {
    const root = createDebugLogger(
      { debug: true, debugLogPath: logPath },
      { now: () => "2026-05-07T00:00:00.000Z", scope: "shared" },
    );
    const recall = root.child("recall");
    const capture = root.child("capture");

    root.log("boot");
    recall.log("hit");
    capture.log("commit");

    const lines = readLines();
    expect(lines.map((l) => [l["scope"], l["event"]])).toEqual([
      ["shared", "boot"],
      ["recall", "hit"],
      ["capture", "commit"],
    ]);
  });

  it("replaces circular references with '[CIRCULAR]' instead of crashing", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    const circular: Record<string, unknown> = { name: "loop" };
    circular["self"] = circular;
    log.log("oops", { circular });

    const lines = readLines();
    expect(lines).toHaveLength(1);
    const entry = lines[0]!;
    expect(entry["event"]).toBe("oops");
    const c = entry["circular"] as Record<string, unknown>;
    expect(c["name"]).toBe("loop");
    expect(c["self"]).toBe("[CIRCULAR]");
  });

  it("falls back to a flat error line when JSON.stringify still throws (e.g. BigInt)", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    log.log("oops", { value: 1n });

    const lines = readLines();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ event: "oops", error: "unserialisable_fields" });
  });
});

describe("createDebugLogger — secret redaction", () => {
  it("redacts top-level secret-like keys", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    log.log("auth", {
      apiKey: "sk-real-key",
      api_key: "sk-snake-case",
      bearer: "tok-abc",
      token: "raw-token",
      secret: "shh",
      password: "hunter2",
      authorization: "Bearer xyz",
      ok: "kept",
    });

    const lines = readLines();
    const entry = lines[0]!;
    expect(entry["apiKey"]).toBe("[REDACTED]");
    expect(entry["api_key"]).toBe("[REDACTED]");
    expect(entry["bearer"]).toBe("[REDACTED]");
    expect(entry["token"]).toBe("[REDACTED]");
    expect(entry["secret"]).toBe("[REDACTED]");
    expect(entry["password"]).toBe("[REDACTED]");
    expect(entry["authorization"]).toBe("[REDACTED]");
    expect(entry["ok"]).toBe("kept");
  });

  it("redacts nested secret-like keys (e.g. headers.authorization)", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    log.log("request", {
      headers: { authorization: "Bearer xyz", "x-trace": "abc" },
      body: { nested: { token: "deep" } },
    });

    const lines = readLines();
    const entry = lines[0]!;
    expect((entry["headers"] as Record<string, unknown>)["authorization"]).toBe("[REDACTED]");
    expect((entry["headers"] as Record<string, unknown>)["x-trace"]).toBe("abc");
    expect(((entry["body"] as Record<string, unknown>)["nested"] as Record<string, unknown>)["token"]).toBe("[REDACTED]");
  });

  it("redacts secret-like keys inside arrays of objects", () => {
    const log = createDebugLogger({ debug: true, debugLogPath: logPath });
    log.log("batch", {
      items: [
        { name: "first", token: "t1" },
        { name: "second", apiKey: "t2" },
      ],
    });

    const lines = readLines();
    const items = (lines[0]!["items"] as Array<Record<string, unknown>>);
    expect(items[0]).toMatchObject({ name: "first", token: "[REDACTED]" });
    expect(items[1]).toMatchObject({ name: "second", apiKey: "[REDACTED]" });
  });

  it("redactValue is case-insensitive on the key match", () => {
    expect(__test__.redactValue("API_KEY", "secret")).toBe("[REDACTED]");
    expect(__test__.redactValue("ApiKey", "secret")).toBe("[REDACTED]");
    expect(__test__.redactValue("Authorization", "Bearer x")).toBe("[REDACTED]");
    expect(__test__.redactValue("ok", "kept")).toBe("kept");
  });
});

describe("createDebugLogger — rotation", () => {
  it("rotates the file to <path>.1 when the size cap would be exceeded", () => {
    const log = createDebugLogger(
      { debug: true, debugLogPath: logPath },
      { maxBytes: 200, now: () => "2026-05-07T00:00:00.000Z", scope: "rot" },
    );

    // Each line is comfortably > 50 bytes (`ts`+`scope`+`event`+padding).
    for (let i = 0; i < 20; i++) {
      log.log(`tick-${i}`, { i, padding: "x".repeat(40) });
    }

    expect(existsSync(`${logPath}.1`)).toBe(true);
    // Live file is non-empty (we kept writing after rotation)
    expect(existsSync(logPath)).toBe(true);
    expect(readFileSync(logPath, "utf8").length).toBeGreaterThan(0);
  });

  it("overwrites a pre-existing <path>.1 backup on rotation", () => {
    // Pre-seed a stale .1 backup with a sentinel value
    const seedPath = `${logPath}.1`;
    // Ensure parent dir exists by triggering the logger first
    const log = createDebugLogger(
      { debug: true, debugLogPath: logPath },
      { maxBytes: 200, now: () => "2026-05-07T00:00:00.000Z", scope: "rot" },
    );
    log.log("seed");
    writeFileSync(seedPath, "STALE-CONTENT-SHOULD-BE-OVERWRITTEN");

    for (let i = 0; i < 25; i++) {
      log.log(`tick-${i}`, { padding: "x".repeat(40) });
    }

    const rotated = readFileSync(seedPath, "utf8");
    expect(rotated).not.toContain("STALE-CONTENT");
  });
});
