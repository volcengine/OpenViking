import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { __test__ } from "../../commands/setup.js";

describe("openviking setup agent prefix validation", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    vi.restoreAllMocks();
    for (const dir of tempDirs.splice(0)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

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

  it("non-interactive setup can persist resource-only recallTargetTypes for post-install opt-in", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-setup-"));
    tempDirs.push(dir);
    const configPath = path.join(dir, "openclaw.json");

    const result = await __test__.setupNonInteractive(configPath, {
      baseUrl: "http://127.0.0.1:1933",
      allowOffline: true,
      recallTargetTypes: ["resource"],
    });

    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    expect(result.success).toBe(true);
    expect(config.plugins.entries.openviking.config.recallTargetTypes).toEqual(["resource"]);
  });
});
