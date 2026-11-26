import { describe, expect, it } from "vitest";

import { prepareRecallQuery } from "../../auto-recall.js";

describe("prepareRecallQuery", () => {
  it("sanitizes the recall query before returning it", () => {
    const result = prepareRecallQuery(
      "  <relevant-memories>stale</relevant-memories>\nhello   world\u0000  ",
    );

    expect(result).toEqual({
      query: "hello world",
      truncated: false,
      originalChars: 11,
      finalChars: 11,
    });
  });

  it("truncates overly long recall queries after sanitization", () => {
    const rawQuery = "x".repeat(4100);

    const result = prepareRecallQuery(rawQuery);

    expect(result.query).toBe("x".repeat(4000));
    expect(result.truncated).toBe(true);
    expect(result.originalChars).toBe(4100);
    expect(result.finalChars).toBe(4000);
  });
});
