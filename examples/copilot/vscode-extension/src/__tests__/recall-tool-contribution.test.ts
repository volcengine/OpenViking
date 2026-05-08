import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  OPENVIKING_RECALL_TOOL_DESCRIPTION,
  OPENVIKING_RECALL_TOOL_DISPLAY_NAME,
  OPENVIKING_RECALL_TOOL_NAME,
  OPENVIKING_RECALL_TOOL_REFERENCE_NAME,
  OPENVIKING_RECALL_TOOL_USER_DESCRIPTION,
} from "@openviking/copilot-shared";

interface LanguageModelToolContribution {
  name: string;
  tags: string[];
  toolReferenceName: string;
  displayName: string;
  modelDescription: string;
  userDescription: string;
  canBeReferencedInPrompt: boolean;
}

interface ExtensionManifest {
  contributes: {
    languageModelTools: LanguageModelToolContribution[];
  };
}

const packageJson = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8")) as ExtensionManifest;

describe("VS Code OpenViking recall language model tool contribution", () => {
  it("uses the shared Phase 2 tuned description verbatim", () => {
    const tool = packageJson.contributes.languageModelTools.find((it) => it.name === OPENVIKING_RECALL_TOOL_NAME);
    expect(tool).toBeDefined();
    expect(tool?.toolReferenceName).toBe(OPENVIKING_RECALL_TOOL_REFERENCE_NAME);
    expect(tool?.displayName).toBe(OPENVIKING_RECALL_TOOL_DISPLAY_NAME);
    expect(tool?.userDescription).toBe(OPENVIKING_RECALL_TOOL_USER_DESCRIPTION);
    expect(tool?.modelDescription).toBe(OPENVIKING_RECALL_TOOL_DESCRIPTION);
    expect(tool?.canBeReferencedInPrompt).toBe(true);
  });
});
