import { describe, expect, it } from "vitest";

import { __test__ } from "../../commands/setup.js";

describe("openviking setup agent prefix validation", () => {
  it.each(["", "  ", "main", "foo_main", "foo-main", "Foo_123"])(
    "accepts valid agent prefix %j",
    (value) => {
      expect(__test__.isValidAgentPrefixInput(value)).toBe(true);
    },
  );

  it.each(["foo.bar", "foo/bar", "foo bar", "中文", "foo:bar"])(
    "rejects invalid agent prefix %j",
    (value) => {
      expect(__test__.isValidAgentPrefixInput(value)).toBe(false);
    },
  );
});
