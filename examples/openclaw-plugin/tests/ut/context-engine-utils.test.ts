import { describe, expect, it } from "vitest";

import { formatMessageFaithful } from "../../services/context-message-adapter.js";

describe("formatMessageFaithful", () => {
  it("formats text parts", () => {
    const result = formatMessageFaithful({
      role: "user",
      parts: [{ type: "text", text: "Hello world" }],
    });
    expect(result).toContain("[user]:");
    expect(result).toContain("Hello world");
  });

  it("formats tool parts with status", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{
        type: "tool",
        tool_name: "read_file",
        tool_status: "completed",
        tool_input: { path: "src/app.ts" },
        tool_output: "export const x = 1;",
      }],
    });
    expect(result).toContain("[Tool: read_file] (completed)");
    expect(result).toContain("Input:");
    expect(result).toContain("Output:");
    expect(result).toContain("export const x = 1;");
  });

  it("formats context parts", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{ type: "context", uri: "viking://mem/1", abstract: "User prefers Python" }],
    });
    expect(result).toContain("[Context: viking://mem/1]");
    expect(result).toContain("User prefers Python");
  });

  it("handles empty parts", () => {
    const result = formatMessageFaithful({ role: "user", parts: [] });
    expect(result).toContain("(empty)");
  });

  it("handles missing parts", () => {
    const result = formatMessageFaithful({ role: "user" } as any);
    expect(result).toContain("(empty)");
  });

  it("handles unknown part types", () => {
    const result = formatMessageFaithful({
      role: "assistant",
      parts: [{ type: "custom", data: "value" }],
    });
    expect(result).toContain("[custom]:");
  });
});
