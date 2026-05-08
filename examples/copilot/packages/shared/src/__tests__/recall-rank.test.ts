import { describe, expect, it } from "vitest";
import type { RecallHit } from "../ov-client.js";
import {
  buildQueryProfile,
  clampScore,
  dedupeItems,
  estimateTokens,
  isEventOrCaseItem,
  lexicalOverlapBoost,
  rankItem,
  rankRecallHits,
} from "../recall/rank.js";

const NEUTRAL_PROFILE = buildQueryProfile("");

describe("clampScore", () => {
  it("clamps to [0, 1]", () => {
    expect(clampScore(-0.5)).toBe(0);
    expect(clampScore(0)).toBe(0);
    expect(clampScore(0.42)).toBeCloseTo(0.42);
    expect(clampScore(1)).toBe(1);
    expect(clampScore(1.7)).toBe(1);
  });

  it("treats non-numbers / NaN / undefined as 0", () => {
    expect(clampScore(undefined)).toBe(0);
    expect(clampScore(null)).toBe(0);
    expect(clampScore("0.5")).toBe(0);
    expect(clampScore(NaN)).toBe(0);
  });
});

describe("estimateTokens", () => {
  it("uses chars/4 ceiling and treats falsy text as 0", () => {
    expect(estimateTokens("")).toBe(0);
    expect(estimateTokens(undefined)).toBe(0);
    expect(estimateTokens(null)).toBe(0);
    expect(estimateTokens("abcd")).toBe(1);
    expect(estimateTokens("abcde")).toBe(2);
    expect(estimateTokens("a".repeat(40))).toBe(10);
  });
});

describe("buildQueryProfile", () => {
  it("tokenises and removes stopwords", () => {
    const p = buildQueryProfile("What is the auth migration plan?");
    expect(p.tokens).toEqual(["auth", "migration", "plan"]);
  });

  it("flags wantsPreference for preference cues", () => {
    expect(buildQueryProfile("which editor do you prefer?").wantsPreference).toBe(true);
    expect(buildQueryProfile("hello there").wantsPreference).toBe(false);
  });

  it("flags wantsTemporal for temporal cues", () => {
    expect(buildQueryProfile("when did we ship that?").wantsTemporal).toBe(true);
    expect(buildQueryProfile("describe the system").wantsTemporal).toBe(false);
  });

  it("handles empty / whitespace queries", () => {
    expect(buildQueryProfile("").tokens).toEqual([]);
    expect(buildQueryProfile("   ").tokens).toEqual([]);
  });
});

describe("lexicalOverlapBoost", () => {
  it("returns 0 when there are no tokens or no text", () => {
    expect(lexicalOverlapBoost([], "anything")).toBe(0);
    expect(lexicalOverlapBoost(["foo"], "")).toBe(0);
  });

  it("scales with the number of matching tokens, capped at 0.2", () => {
    const boost1 = lexicalOverlapBoost(["auth"], "auth migration plan");
    expect(boost1).toBeCloseTo(0.2);

    const boost2 = lexicalOverlapBoost(["auth", "migration", "plan", "schema"], "auth migration plan schema");
    // 4 matches / min(4,4) = 1 * 0.2 = 0.2 capped
    expect(boost2).toBeCloseTo(0.2);

    const boost3 = lexicalOverlapBoost(["auth", "migration", "plan", "unrelated"], "auth migration plan");
    // 3 matches / min(4,4) = 0.75 * 0.2 = 0.15
    expect(boost3).toBeCloseTo(0.15);
  });

  it("considers at most 8 query tokens (the source's slice cap)", () => {
    const tenTokens = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"];
    // Only first 8 are checked. If only the 9th matched, boost is 0.
    const text = "i j";
    expect(lexicalOverlapBoost(tenTokens, text)).toBe(0);
  });
});

describe("rankItem", () => {
  it("returns the clamped base score when no boosts apply", () => {
    const item: RecallHit = { uri: "viking://x", score: 0.6, abstract: "" };
    expect(rankItem(item, NEUTRAL_PROFILE)).toBeCloseTo(0.6);
  });

  it("applies the leaf boost (+0.12) for level=2 items", () => {
    const item: RecallHit = { uri: "viking://x", score: 0.5, abstract: "", level: 2 };
    expect(rankItem(item, NEUTRAL_PROFILE)).toBeCloseTo(0.62);
  });

  it("applies the leaf boost (+0.12) for *.md URIs", () => {
    const item: RecallHit = { uri: "viking://x/note.md", score: 0.5, abstract: "" };
    expect(rankItem(item, NEUTRAL_PROFILE)).toBeCloseTo(0.62);
  });

  it("applies the event boost (+0.10) only when query is temporal AND item is in events/", () => {
    const item: RecallHit = {
      uri: "viking://memories/events/launch.md", score: 0.5, abstract: "",
    };
    // "yesterday?" sets wantsTemporal=true and its only non-stopword token
    // ("yesterday") doesn't appear in the URI, so the lexical overlap
    // boost is 0 and we can pin the boost arithmetic exactly.
    expect(rankItem(item, buildQueryProfile("yesterday?")))
      .toBeCloseTo(0.5 + 0.12 + 0.1); // .md leaf + temporal event
    expect(rankItem(item, NEUTRAL_PROFILE))
      .toBeCloseTo(0.5 + 0.12); // no temporal cue → no event boost
  });

  it("applies the preference boost (+0.08) only when query is preference AND item is preferences/", () => {
    const item: RecallHit = {
      uri: "viking://memories/preferences/git.md", score: 0.5, abstract: "",
    };
    // "favorite color?" sets wantsPreference=true; neither token appears
    // in the URI so the overlap boost stays at 0. Avoids using "prefer"
    // in the query because it would substring-match "preferences/" in
    // the item URI and add an overlap boost.
    expect(rankItem(item, buildQueryProfile("favorite color?")))
      .toBeCloseTo(0.5 + 0.12 + 0.08); // .md leaf + preference
    expect(rankItem(item, NEUTRAL_PROFILE))
      .toBeCloseTo(0.5 + 0.12); // no pref cue → no pref boost
  });

  it("applies the lexical overlap boost based on item.uri + abstract text", () => {
    const profile = buildQueryProfile("auth migration plan");
    const matchA: RecallHit = {
      uri: "viking://x/a", score: 0, abstract: "auth migration plan details",
    };
    const matchB: RecallHit = {
      uri: "viking://x/b", score: 0, abstract: "unrelated content here",
    };
    expect(rankItem(matchA, profile)).toBeGreaterThan(rankItem(matchB, profile));
  });
});

describe("isEventOrCaseItem + dedupeItems", () => {
  it("flags events / cases by category or by URI segment", () => {
    expect(isEventOrCaseItem({ uri: "viking://x/events/y.md" })).toBe(true);
    expect(isEventOrCaseItem({ uri: "viking://x/cases/y.md" })).toBe(true);
    expect(isEventOrCaseItem({ uri: "viking://x/y.md", category: "events" })).toBe(true);
    expect(isEventOrCaseItem({ uri: "viking://x/y.md" })).toBe(false);
  });

  it("dedupes non-event items by abstract content", () => {
    const items: RecallHit[] = [
      { uri: "viking://a", abstract: "shared abstract" },
      { uri: "viking://b", abstract: "shared abstract" },   // dropped
      { uri: "viking://c", abstract: "different abstract" },
    ];
    const out = dedupeItems(items);
    expect(out.map((i) => i.uri)).toEqual(["viking://a", "viking://c"]);
  });

  it("dedupes event/case items by URI (so duplicate abstracts don't collapse distinct events)", () => {
    const items: RecallHit[] = [
      { uri: "viking://m/events/2026-01.md", abstract: "shared", category: "events" },
      { uri: "viking://m/events/2026-02.md", abstract: "shared", category: "events" },
      { uri: "viking://m/events/2026-01.md", abstract: "shared", category: "events" }, // dropped
    ];
    const out = dedupeItems(items);
    expect(out.map((i) => i.uri)).toEqual([
      "viking://m/events/2026-01.md",
      "viking://m/events/2026-02.md",
    ]);
  });

  it("falls back to URI when abstract is empty", () => {
    const items: RecallHit[] = [
      { uri: "viking://a" },
      { uri: "viking://a" }, // dropped — same uri-fallback key
      { uri: "viking://b" },
    ];
    expect(dedupeItems(items).map((i) => i.uri)).toEqual(["viking://a", "viking://b"]);
  });
});

describe("rankRecallHits — pipeline", () => {
  const HITS: RecallHit[] = [
    { uri: "viking://m/a.md", score: 0.9, abstract: "auth migration steps" },
    { uri: "viking://m/b.md", score: 0.7, abstract: "an unrelated topic" },
    { uri: "viking://m/c.md", score: 0.4, abstract: "below threshold" },
    { uri: "viking://m/d.md", score: 0.85, abstract: "another auth migration" },
    { uri: "viking://m/events/launch.md", score: 0.6, abstract: "launch event", category: "events" },
  ];

  it("filters out hits below scoreThreshold", () => {
    const out = rankRecallHits(HITS, { query: "any", scoreThreshold: 0.5, recallLimit: 10 });
    expect(out.find((h) => h.uri === "viking://m/c.md")).toBeUndefined();
  });

  it("truncates to recallLimit", () => {
    const out = rankRecallHits(HITS, { query: "any", scoreThreshold: 0, recallLimit: 2 });
    expect(out).toHaveLength(2);
  });

  it("sorts by rank descending and uses query-aware boosts", () => {
    const out = rankRecallHits(HITS, {
      query: "auth migration plan",
      scoreThreshold: 0.5,
      recallLimit: 4,
    });
    // Both 'auth migration' items get an overlap boost; they should land
    // above the unrelated 0.7-score item.
    const top = out.map((h) => h.uri);
    expect(top.indexOf("viking://m/a.md")).toBeLessThan(top.indexOf("viking://m/b.md"));
    expect(top.indexOf("viking://m/d.md")).toBeLessThan(top.indexOf("viking://m/b.md"));
  });

  it("preserves input order on tied rank scores (V8 stable sort contract)", () => {
    // Two items with identical fields → identical rankItem output. They
    // must come back in their input order.
    const tied: RecallHit[] = [
      { uri: "viking://tied-1", score: 0.5, abstract: "same" },
      { uri: "viking://tied-2", score: 0.5, abstract: "same" },
      { uri: "viking://tied-3", score: 0.5, abstract: "same" },
    ];
    // dedupeItems would collapse them by abstract — disable that by
    // giving them distinct abstracts that still produce identical
    // rankItem values.
    const tiedDistinctAbstracts: RecallHit[] = [
      { uri: "viking://t/1", score: 0.5, abstract: "abs-1" },
      { uri: "viking://t/2", score: 0.5, abstract: "abs-2" },
      { uri: "viking://t/3", score: 0.5, abstract: "abs-3" },
    ];
    const out = rankRecallHits(tiedDistinctAbstracts, {
      query: "no overlap",
      scoreThreshold: 0,
      recallLimit: 10,
    });
    expect(out.map((h) => h.uri)).toEqual([
      "viking://t/1",
      "viking://t/2",
      "viking://t/3",
    ]);
    // Sanity: with identical abstracts dedupe collapses to one
    expect(rankRecallHits(tied, { query: "x", scoreThreshold: 0, recallLimit: 10 })).toHaveLength(1);
  });

  it("applies dedupe AFTER sorting, keeping the highest-ranked of each duplicate", () => {
    const items: RecallHit[] = [
      { uri: "viking://low", score: 0.5, abstract: "shared" },
      { uri: "viking://high", score: 0.95, abstract: "shared" },
    ];
    const out = rankRecallHits(items, { query: "x", scoreThreshold: 0, recallLimit: 5 });
    expect(out).toHaveLength(1);
    expect(out[0]!.uri).toBe("viking://high");
  });

  it("returns [] for an empty input", () => {
    expect(rankRecallHits([], { query: "x", scoreThreshold: 0, recallLimit: 5 })).toEqual([]);
  });

  it("handles negative recallLimit by returning []", () => {
    expect(rankRecallHits(HITS, { query: "x", scoreThreshold: 0, recallLimit: -5 })).toEqual([]);
  });
});
