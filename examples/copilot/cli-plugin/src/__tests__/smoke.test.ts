import { describe, expect, it } from "vitest";
import { buildServerInfo } from "../mcp-server.js";

describe("copilot-cli-memory mcp server", () => {
  it("returns server info wired to the shared package", () => {
    const info = buildServerInfo();
    expect(info.name).toBe("openviking-copilot-mcp");
    expect(info.sharedFrom).toBe("@openviking/copilot-shared");
  });
});
