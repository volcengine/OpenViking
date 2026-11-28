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

  it("retains the start and end of a sanitized query within the character budget", () => {
    const rawQuery = "<relevant-memories>stale</relevant-memories>" + "a".repeat(2100) + "b".repeat(2000);

    const result = prepareRecallQuery(rawQuery);

    expect(result.query).toMatch(/^a+ … b+$/);
    expect(result.truncated).toBe(true);
    expect(result.originalChars).toBe(4100);
    expect(result.finalChars).toBe(4000);
  });

  it("does not leave an unpaired surrogate where the middle was removed", () => {
    const result = prepareRecallQuery("🚀".repeat(2050));

    expect(result.truncated).toBe(true);
    expect(result.query).not.toMatch(/[\uD800-\uDFFF]/u);
    expect(result.finalChars).toBeLessThanOrEqual(4000);
  });
});
