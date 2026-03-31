import { describe, it, expect } from "vitest";
import { memoryOpenVikingConfigSchema } from "../config.js";

describe("strictAgentIsolation config", () => {
  it("defaults to false when not specified", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.strictAgentIsolation).toBe(false);
  });

  it("is true when explicitly set to true", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: true });
    expect(cfg.strictAgentIsolation).toBe(true);
  });

  it("is false when explicitly set to false", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: false });
    expect(cfg.strictAgentIsolation).toBe(false);
  });

  it("is false for non-boolean truthy values (string, number)", () => {
    const cfg1 = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: "true" });
    expect(cfg1.strictAgentIsolation).toBe(false);

    const cfg2 = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: 1 });
    expect(cfg2.strictAgentIsolation).toBe(false);
  });

  it("is accepted by assertAllowedKeys (does not throw)", () => {
    expect(() => memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: true })).not.toThrow();
  });

  it("has uiHints entry for strictAgentIsolation", () => {
    const hints = memoryOpenVikingConfigSchema.uiHints;
    expect(hints.strictAgentIsolation).toBeDefined();
    expect(hints.strictAgentIsolation.label).toBe("Strict Agent Isolation");
    expect(hints.strictAgentIsolation.advanced).toBe(true);
    expect(hints.strictAgentIsolation.help).toContain("viking://agent/memories");
  });
});
