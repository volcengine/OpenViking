import { describe, expect, it } from "vitest";
import { PACKAGE_NAME } from "../index.js";

describe("@openviking/copilot-shared", () => {
  it("exposes its package name", () => {
    expect(PACKAGE_NAME).toBe("@openviking/copilot-shared");
  });
});
