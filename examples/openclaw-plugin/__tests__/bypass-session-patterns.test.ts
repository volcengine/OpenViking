import { describe, expect, it } from "vitest";

import { memoryOpenVikingConfigSchema } from "../config.js";
import {
  compileSessionPatterns,
  matchesSessionPattern,
  shouldBypassSession,
} from "../text-utils.js";

describe("bypass session patterns", () => {
  it("parses bypass session patterns from config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      bypassSessionPatterns: [
        "agent:*:cron:**",
        "agent:ops:maintenance:**",
      ],
    });

    expect(cfg.bypassSessionPatterns).toEqual([
      "agent:*:cron:**",
      "agent:ops:maintenance:**",
    ]);
  });

  it("supports ingestReplyAssistIgnoreSessionPatterns as a deprecated alias", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      ingestReplyAssistIgnoreSessionPatterns: [
        "agent:*:cron:**",
      ],
    });

    expect(cfg.bypassSessionPatterns).toEqual([
      "agent:*:cron:**",
    ]);
  });

  it("defaults bypass session patterns to an empty list", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.bypassSessionPatterns).toEqual([]);
  });

  it("matches lossless-claw style session globs", () => {
    const patterns = compileSessionPatterns([
      "agent:*:cron:**",
      "agent:ops:maintenance:**",
    ]);

    expect(matchesSessionPattern("agent:main:cron:nightly:run:1", patterns)).toBe(true);
    expect(matchesSessionPattern("agent:ops:maintenance:weekly", patterns)).toBe(true);
    expect(matchesSessionPattern("agent:main:main", patterns)).toBe(false);
  });

  it("prefers sessionKey over sessionId when deciding whether to bypass", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldBypassSession(
        {
          sessionId: "agent:main:cron:from-id",
          sessionKey: "agent:main:main",
        },
        patterns,
      ),
    ).toBe(false);
  });

  it("falls back to sessionId when sessionKey is unavailable", () => {
    const patterns = compileSessionPatterns(["agent:*:cron:**"]);

    expect(
      shouldBypassSession(
        {
          sessionId: "agent:main:cron:nightly:run:1",
        },
        patterns,
      ),
    ).toBe(true);
  });
});
