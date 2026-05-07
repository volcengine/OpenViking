import { describe, expect, it } from "vitest";
import { activate } from "../extension.js";

describe("openviking-copilot vscode extension", () => {
  it("activate() returns a ready handle wired to the shared package", () => {
    const handle = activate();
    expect(handle.ready).toBe(true);
    expect(handle.shared).toBe("@openviking/copilot-shared");
  });
});
