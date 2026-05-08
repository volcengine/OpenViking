import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  OPENVIKING_SETTINGS,
  SECRETS_API_KEY,
  SET_API_KEY_COMMAND,
} from "../settings-schema";

interface ManifestProperty {
  type?: string;
  default?: unknown;
  enum?: string[];
  items?: { type?: string };
  markdownDescription?: string;
}

interface ManifestCommand {
  command: string;
  title: string;
  category?: string;
}

interface Manifest {
  contributes: {
    commands: ManifestCommand[];
    configuration: {
      properties: Record<string, ManifestProperty>;
    };
  };
}

const manifestPath = join(__dirname, "..", "..", "package.json");
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as Manifest;
const properties = manifest.contributes.configuration.properties;

describe("OPENVIKING_SETTINGS catalogue", () => {
  it("declares 25 settings (PLAN.md §8.2 spec)", () => {
    expect(OPENVIKING_SETTINGS).toHaveLength(25);
  });

  it("every key uses the `openviking.` prefix", () => {
    for (const s of OPENVIKING_SETTINGS) {
      expect(s.key.startsWith("openviking.")).toBe(true);
    }
  });

  it("apiKey is the only entry flagged secret: true", () => {
    const secrets = OPENVIKING_SETTINGS.filter((s) => s.secret).map((s) => s.key);
    expect(secrets).toEqual(["openviking.apiKey"]);
  });

  it("captureMode is the only enum entry and lists exactly the legal values", () => {
    const captureMode = OPENVIKING_SETTINGS.find((s) => s.key === "openviking.captureMode")!;
    expect(captureMode.type).toBe("enum");
    expect(captureMode.enumValues).toEqual(["semantic", "keyword"]);
  });

  it("exposes stable constants for the Set-API-Key flow", () => {
    expect(SECRETS_API_KEY).toBe("openviking.apiKey");
    expect(SET_API_KEY_COMMAND).toBe("openviking.setApiKey");
  });
});

describe("manifest ↔ schema drift", () => {
  it("every setting in the schema is declared in the manifest with a matching JSON-schema type", () => {
    for (const desc of OPENVIKING_SETTINGS) {
      const prop = properties[desc.key];
      expect(prop, `manifest is missing property ${desc.key}`).toBeDefined();
      switch (desc.type) {
        case "string":
          expect(prop!.type).toBe("string");
          break;
        case "boolean":
          expect(prop!.type).toBe("boolean");
          break;
        case "number":
          expect(prop!.type).toBe("number");
          break;
        case "string-array":
          expect(prop!.type).toBe("array");
          expect(prop!.items?.type).toBe("string");
          break;
        case "enum":
          expect(prop!.type).toBe("string");
          expect(prop!.enum).toEqual(desc.enumValues);
          break;
      }
    }
  });

  it("every setting in the schema has a default that matches the manifest", () => {
    for (const desc of OPENVIKING_SETTINGS) {
      const prop = properties[desc.key];
      expect(prop!.default, `default mismatch for ${desc.key}`).toEqual(desc.default);
    }
  });

  it("every manifest property is listed in the schema (no orphan fields)", () => {
    const schemaKeys = new Set(OPENVIKING_SETTINGS.map((s) => s.key));
    for (const key of Object.keys(properties)) {
      expect(schemaKeys.has(key), `manifest has property ${key} that's not in OPENVIKING_SETTINGS`).toBe(true);
    }
  });

  it("apiKey carries a security warning in its markdownDescription", () => {
    const prop = properties["openviking.apiKey"];
    expect(prop?.markdownDescription).toMatch(/Set API Key/i);
    expect(prop?.markdownDescription).toMatch(/SecretStorage/i);
  });

  it("manifest declares the openviking.setApiKey command", () => {
    const cmd = manifest.contributes.commands.find((c) => c.command === SET_API_KEY_COMMAND);
    expect(cmd, "missing openviking.setApiKey command in manifest").toBeDefined();
    expect(cmd!.title).toMatch(/Set API Key/i);
    expect(cmd!.category).toBe("OpenViking");
  });
});
