import { describe, expect, it } from "vitest";
import { deriveSessionId, SESSION_ID_PREFIX } from "../session/id.js";

describe("deriveSessionId — format", () => {
  it("returns cp- prefix followed by 64 lowercase hex chars", () => {
    const id = deriveSessionId("copilot-vscode", "abc123");
    expect(id.startsWith(SESSION_ID_PREFIX)).toBe(true);
    expect(id).toMatch(/^cp-[0-9a-f]{64}$/);
  });

  it("exports the prefix as a stable constant", () => {
    expect(SESSION_ID_PREFIX).toBe("cp-");
  });
});

describe("deriveSessionId — determinism", () => {
  it("returns the same id for the same inputs across calls", () => {
    const a = deriveSessionId("copilot-vscode", "session-xyz");
    const b = deriveSessionId("copilot-vscode", "session-xyz");
    expect(a).toBe(b);
  });

  it("returns the same id even when the inputs are constructed from different string instances", () => {
    const host1 = ["copilot", "cli"].join("-");
    const host2 = `copilot-${"cli"}`;
    expect(deriveSessionId(host1, "abc")).toBe(deriveSessionId(host2, "abc"));
  });
});

describe("deriveSessionId — input sensitivity", () => {
  it("differs when the host string differs (vscode vs cli)", () => {
    const vscodeId = deriveSessionId("copilot-vscode", "shared-id");
    const cliId = deriveSessionId("copilot-cli", "shared-id");
    expect(vscodeId).not.toBe(cliId);
  });

  it("differs when only the hostSessionId differs", () => {
    const a = deriveSessionId("copilot-vscode", "session-A");
    const b = deriveSessionId("copilot-vscode", "session-B");
    expect(a).not.toBe(b);
  });

  it("treats the empty hostSessionId as a valid (but distinct) input", () => {
    const empty = deriveSessionId("copilot-vscode", "");
    const nonEmpty = deriveSessionId("copilot-vscode", "x");
    expect(empty).toMatch(/^cp-[0-9a-f]{64}$/);
    expect(empty).not.toBe(nonEmpty);
  });

  it("uses ':' as the host/sessionId separator (not concatenation)", () => {
    // If concatenation were used, ("ab", "c") and ("a", "bc") would produce
    // the same digest. The ':' separator must prevent that collision.
    const a = deriveSessionId("ab", "c");
    const b = deriveSessionId("a", "bc");
    expect(a).not.toBe(b);
  });
});

describe("deriveSessionId — pinned SHA-256 vectors", () => {
  // These hashes are computed from `printf '%s' '<host>:<id>' | shasum -a 256`
  // (POSIX). Changing the algorithm, prefix, or separator must break
  // these tests so the wire-compatibility implication is impossible to
  // miss in review.
  it("matches the pinned digest for ('copilot-vscode', 'abc123')", () => {
    expect(deriveSessionId("copilot-vscode", "abc123")).toBe(
      "cp-8dbccca6c1fc9639bf2fb78ee08eb69461bf91996dcb547fb1a2a5ffb2780488",
    );
  });

  it("matches the pinned digest for ('copilot-cli', 'abc123')", () => {
    expect(deriveSessionId("copilot-cli", "abc123")).toBe(
      "cp-f4a4513315483a76d19c5b6283e4b81a9978d2f0bc0d0f31ca878c0aca5f2780",
    );
  });

  it("matches the pinned digest for ('copilot-vscode', '')", () => {
    expect(deriveSessionId("copilot-vscode", "")).toBe(
      "cp-f8afa53597ca79847700fb1a2642663aaf940d74f8d4d69b8386cb816be5bb23",
    );
  });
});
