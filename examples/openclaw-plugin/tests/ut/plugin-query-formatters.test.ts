import { describe, expect, it } from "vitest";

import {
  formatOVListEntry,
  formatOVListText,
  formatOVMultiReadText,
  formatOVReadText,
  formatOVSearchRows,
  formatOVSearchText,
} from "../../plugin/openviking-query-formatters.js";

describe("openviking query formatters", () => {
  it("formats ranked search rows across memories, resources, and skills", () => {
    const rows = formatOVSearchRows({
      memories: [
        { uri: "viking://user/memory/1", level: 2, score: 0.987, abstract: "remember this" },
      ],
      resources: [
        { uri: "viking://resources/doc", score: 0.7, overview: "resource overview" },
      ],
      skills: [
        { uri: "skill://openviking-context-database", abstract: "skill overview" },
      ],
      total: 3,
    });

    expect(rows[0]).toContain("no  type");
    expect(rows[1]).toContain("memory");
    expect(rows[1]).toContain("viking://user/memory/1");
    expect(rows[1]).toContain("0.99");
    expect(rows[1]).toContain("remember this");
    expect(rows.join("\n")).toContain("resource overview");
    expect(rows.join("\n")).toContain("skill overview");
  });

  it("formats search text and empty search text", () => {
    expect(formatOVSearchText("query", "viking://resources", { total: 0 })).toBe(
      'No OpenViking resource or skill results found for "query" under viking://resources.',
    );

    const text = formatOVSearchText("query", undefined, {
      resources: [{ uri: "viking://resources/doc", abstract: "result" }],
      total: 1,
    });

    expect(text).toContain('Found 1 OpenViking results for "query"');
    expect(text).toContain("Use ov_read on exact hit URIs");
    expect(text).toContain("viking://resources/doc");
  });

  it("formats list entries with directory markers and summaries", () => {
    expect(formatOVListEntry("viking://resources/raw")).toBe("viking://resources/raw");
    expect(formatOVListEntry({ uri: "viking://resources/folder", type: "directory" })).toBe(
      "[dir] viking://resources/folder",
    );
    expect(formatOVListText("viking://resources", [
      { uri: "viking://resources/doc", isDir: false, abstract: "  short\nsummary  " },
    ])).toBe("Listed 1 OpenViking entry under viking://resources\n\n[file] viking://resources/doc - short summary");
  });

  it("formats read and multi-read output", () => {
    expect(formatOVReadText("viking://resources/doc", "")).toBe(
      "--- START OF viking://resources/doc ---\n(empty OpenViking content)\n--- END OF viking://resources/doc ---",
    );

    expect(formatOVMultiReadText([
      { uri: "viking://resources/a", content: "content", success: true },
      { uri: "viking://resources/b", content: "boom", success: false },
    ])).toBe(
      "Multi-read results for 2 OpenViking resources:\n\n--- START OF viking://resources/a ---\ncontent\n--- END OF viking://resources/a ---\n\n--- START OF viking://resources/b ---\nERROR: boom\n--- END OF viking://resources/b ---",
    );
  });
});
