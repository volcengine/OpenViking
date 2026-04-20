import { describe, expect, it } from "vitest";

import { memoryOpenVikingConfigSchema } from "../config.js";
import { extractSenderId } from "../context-engine.js";

describe("extractSenderId", () => {
  it("returns found=true for non-empty senderId string", () => {
    expect(extractSenderId({ senderId: "alice" })).toEqual({
      found: true,
      senderId: "alice",
    });
  });

  it("trims surrounding whitespace", () => {
    expect(extractSenderId({ senderId: "  bob  " })).toEqual({
      found: true,
      senderId: "bob",
    });
  });

  it("returns found=false for missing runtimeContext", () => {
    expect(extractSenderId(undefined)).toEqual({ found: false });
  });

  it("returns found=false when senderId key is absent", () => {
    expect(extractSenderId({})).toEqual({ found: false });
  });

  it("returns found=false for empty or whitespace-only string", () => {
    expect(extractSenderId({ senderId: "" })).toEqual({ found: false });
    expect(extractSenderId({ senderId: "   " })).toEqual({ found: false });
  });

  it("returns found=false for non-string senderId values", () => {
    expect(extractSenderId({ senderId: 42 })).toEqual({ found: false });
    expect(extractSenderId({ senderId: null })).toEqual({ found: false });
    expect(extractSenderId({ senderId: { id: "x" } })).toEqual({ found: false });
  });
});

describe("userMode config", () => {
  it("accepts single-user and multi-user", () => {
    const single = memoryOpenVikingConfigSchema.parse({ userMode: "single-user" });
    const multi = memoryOpenVikingConfigSchema.parse({ userMode: "multi-user" });
    expect(single.userMode).toBe("single-user");
    expect(multi.userMode).toBe("multi-user");
  });

  it("defaults to single-user when absent", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.userMode).toBe("single-user");
  });

  it("rejects unknown userMode values", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ userMode: "group-chat" }),
    ).toThrow(/userMode/);
  });
});
