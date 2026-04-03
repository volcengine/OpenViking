import { describe, expect, it } from "vitest";

import {
  openClawSessionToOvStorageId,
  openClawSessionRefToOvStorageId,
  formatMessageFaithful,
} from "../../context-engine.js";

describe("openClawSessionToOvStorageId", () => {
  it("passes through UUID sessionId as lowercase", () => {
    const uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890";
    expect(openClawSessionToOvStorageId(uuid, undefined)).toBe(uuid.toLowerCase());
  });

  it("hashes sessionKey via sha256", () => {
    const result = openClawSessionToOvStorageId(undefined, "agent:myagent:session123");
    expect(result).toMatch(/^[a-f0-9]{64}$/);
  });

  it("prefers UUID sessionId over sessionKey", () => {
    const uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
    const result = openClawSessionToOvStorageId(uuid, "some-session-key");
    expect(result).toBe(uuid);
  });

  it("uses sessionKey when sessionId is not a UUID", () => {
    const result = openClawSessionToOvStorageId("plain-session", "agent:x:y");
    expect(result).toMatch(/^[a-f0-9]{64}$/);
  });

  it("passes through non-UUID sessionId when no sessionKey", () => {
    expect(openClawSessionToOvStorageId("my-session-123", undefined)).toBe("my-session-123");
  });

  it("hashes Windows-unsafe sessionId", () => {
    const result = openClawSessionToOvStorageId("C:\\Users\\test", undefined);
    expect(result).toMatch(/^[a-f0-9]{64}$/);
  });

  it("throws when both sessionId and sessionKey are empty", () => {
    expect(() => openClawSessionToOvStorageId("", "")).toThrow("need sessionId or sessionKey");
  });

  it("throws when both are undefined", () => {
    expect(() => openClawSessionToOvStorageId(undefined, undefined)).toThrow();
  });

  it("trims whitespace from sessionId", () => {
    const uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
    expect(openClawSessionToOvStorageId(`  ${uuid}  `, undefined)).toBe(uuid);
  });
});

describe("openClawSessionRefToOvStorageId", () => {
  it("passes through UUID as lowercase", () => {
    const uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890";
    expect(openClawSessionRefToOvStorageId(uuid)).toBe(uuid.toLowerCase());
  });

  it("passes through safe non-UUID ref unchanged", () => {
    expect(openClawSessionRefToOvStorageId("my-session-123")).toBe("my-session-123");
  });

  it("hashes Windows-unsafe ref", () => {
    const result = openClawSessionRefToOvStorageId("C:\\bad\\path");
    expect(result).toMatch(/^[a-f0-9]{64}$/);
  });

  it("throws for empty ref", () => {
    expect(() => openClawSessionRefToOvStorageId("")).toThrow("empty session ref");
  });

  it("throws for whitespace-only ref", () => {
    expect(() => openClawSessionRefToOvStorageId("   ")).toThrow("empty session ref");
  });
});

describe("formatMessageFaithful", () => {
  it("formats text parts", () => {
    const result = formatMessageFaithful({
      role: "user",
      parts: [{ type: "text", text: "Hello world" }],
    });
    expect(result).toContain("[user]:");
    expect(result).toContain("Hello world");
  });

  it("formats tool parts with status", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{
        type: "tool",
        tool_name: "read_file",
        tool_status: "completed",
        tool_input: { path: "src/app.ts" },
        tool_output: "export const x = 1;",
      }],
    });
    expect(result).toContain("[Tool: read_file] (completed)");
    expect(result).toContain("Input:");
    expect(result).toContain("Output:");
    expect(result).toContain("export const x = 1;");
  });

  it("formats context parts", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{ type: "context", uri: "viking://mem/1", abstract: "User prefers Python" }],
    });
    expect(result).toContain("[Context: viking://mem/1]");
    expect(result).toContain("User prefers Python");
  });

  it("handles empty parts", () => {
    const result = formatMessageFaithful({ role: "user", parts: [] });
    expect(result).toContain("(empty)");
  });

  it("handles missing parts", () => {
    const result = formatMessageFaithful({ role: "user" } as any);
    expect(result).toContain("(empty)");
  });

  it("handles unknown part types", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{ type: "custom", data: "value" }],
    });
    expect(result).toContain("[custom]:");
  });
});
