import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const runtimeModule = "recall-context.ts";

function readText(relativePath: string): string {
  return readFileSync(new URL(`../../${relativePath}`, import.meta.url), "utf8");
}

describe("openclaw-plugin installer file lists", () => {
  it("includes runtime modules in the install manifest", () => {
    const manifest = JSON.parse(readText("install-manifest.json")) as {
      files: { required: string[]; optional: string[] };
    };

    expect([...manifest.files.required, ...manifest.files.optional]).toContain(runtimeModule);
  });

  it("keeps setup-helper and shell fallback file lists in sync with runtime modules", () => {
    expect(readText("setup-helper/install.js")).toContain(`"${runtimeModule}"`);
    expect(readText("install.sh")).toContain(runtimeModule);
  });
});
