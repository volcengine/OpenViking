import { describe, expect, it } from "vitest";

import { memoryOpenVikingConfigSchema } from "../config.js";

describe("strictAgentIsolation config", () => {
  it("defaults to false when not specified", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.strictAgentIsolation).toBe(false);
  });

  it("defaults to false when explicitly set to false", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: false });
    expect(cfg.strictAgentIsolation).toBe(false);
  });

  it("resolves to true when explicitly set to true", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: true });
    expect(cfg.strictAgentIsolation).toBe(true);
  });

  it("treats non-boolean truthy values as false (strict boolean check)", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: "yes" });
    expect(cfg.strictAgentIsolation).toBe(false);
  });

  it("is accepted in allowedKeys without throwing", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ strictAgentIsolation: true }),
    ).not.toThrow();
  });

  it("still rejects unknown keys alongside strictAgentIsolation", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        strictAgentIsolation: true,
        bogusKey: 42,
      }),
    ).toThrow(/unknown keys.*bogusKey/i);
  });
});
