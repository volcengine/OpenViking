import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { OPENVIKING_RECALL_TOOL_DESCRIPTION } from "../index.js";

interface SeedPrompt {
  id: string;
  label: "relevant" | "irrelevant";
  prompt: string;
}

interface DescriptionVariant {
  id: string;
  description: string;
  relevantAutoInvokeRate: number;
  irrelevantFalsePositiveRate: number;
}

interface SeedFixture {
  finalDescriptionId: string;
  thresholds: {
    minRelevantAutoInvokeRate: number;
    maxIrrelevantFalsePositiveRate: number;
  };
  variants: DescriptionVariant[];
  prompts: SeedPrompt[];
}

const fixturePath = new URL("../../test/fixtures/recall-tool-description-seeds.json", import.meta.url);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as SeedFixture;

function finalVariant(): DescriptionVariant {
  const variant = fixture.variants.find((it) => it.id === fixture.finalDescriptionId);
  if (!variant) throw new Error(`Missing final description variant ${fixture.finalDescriptionId}`);
  return variant;
}

describe("OpenViking recall tool description seeds", () => {
  it("keeps the final description single-sourced", () => {
    expect(finalVariant().description).toBe(OPENVIKING_RECALL_TOOL_DESCRIPTION);
  });

  it("keeps the committed 30-prompt seed set balanced across relevant and irrelevant prompts", () => {
    expect(fixture.prompts).toHaveLength(30);
    expect(fixture.prompts.filter((it) => it.label === "relevant")).toHaveLength(20);
    expect(fixture.prompts.filter((it) => it.label === "irrelevant")).toHaveLength(10);
    expect(new Set(fixture.prompts.map((it) => it.id)).size).toBe(30);
  });

  it("records final variant rates that meet the Phase 2 thresholds", () => {
    const variant = finalVariant();
    expect(variant.relevantAutoInvokeRate).toBeGreaterThanOrEqual(fixture.thresholds.minRelevantAutoInvokeRate);
    expect(variant.irrelevantFalsePositiveRate).toBeLessThanOrEqual(fixture.thresholds.maxIrrelevantFalsePositiveRate);
  });
});
