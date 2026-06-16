import { describe, expect, it } from "vitest";

import { __test__ } from "../../commands/setup.js";

describe("openviking setup agent prefix validation", () => {
  it.each(["", "  ", "main", "foo_main", "foo-main", "Foo_123"])(
    "accepts valid agent prefix %j",
    (value) => {
      expect(__test__.isValidPeerPrefixInput(value)).toBe(true);
    },
  );

  it.each(["foo.bar", "foo/bar", "foo bar", "中文", "foo:bar"])(
    "rejects invalid agent prefix %j",
    (value) => {
      expect(__test__.isValidPeerPrefixInput(value)).toBe(false);
    },
  );
});

describe("openviking setup recall target type parsing", () => {
  it("normalizes comma-separated target types", () => {
    expect(__test__.normalizeSetupRecallTargetTypes("resource, user\nagent, user")).toEqual([
      "resource",
      "user",
      "agent",
    ]);
  });

  it("rejects unknown target types", () => {
    expect(() => __test__.normalizeSetupRecallTargetTypes("user,project")).toThrow(
      "unknown resource types: project",
    );
  });

  it("rejects session as a recall target type", () => {
    expect(() => __test__.normalizeSetupRecallTargetTypes("session")).toThrow(
      "unknown resource types: session",
    );
  });
});
