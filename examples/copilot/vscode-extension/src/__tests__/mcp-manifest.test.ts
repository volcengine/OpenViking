import { describe, expect, it } from "vitest";
import type { PluginConfig } from "@openviking/copilot-shared";
import {
  MCP_PROVIDER_ID,
  buildOpenVikingMcpServerDefinition,
} from "../mcp/manifest";

function baseCfg(overrides: Partial<PluginConfig> = {}): PluginConfig {
  return {
    configPath: null,
    baseUrl: "http://127.0.0.1:1933",
    apiKey: "",
    agentId: "copilot-vscode",
    accountId: "",
    userId: "",
    timeoutMs: 5000,
    autoRecall: true,
    recallLimit: 6,
    scoreThreshold: 0.35,
    minQueryLength: 3,
    logRankingDetails: false,
    recallMaxContentChars: 500,
    recallTokenBudget: 2000,
    recallPreferAbstract: true,
    autoCapture: true,
    captureMode: "semantic",
    captureMaxLength: 24000,
    captureTimeoutMs: 30000,
    captureAssistantTurns: true,
    commitTokenThreshold: 20000,
    resumeContextBudget: 32000,
    bypassSession: false,
    bypassSessionPatterns: [],
    writePathAsync: true,
    debug: false,
    debugLogPath: "/tmp/test.log",
    ...overrides,
  };
}

describe("MCP_PROVIDER_ID", () => {
  it("matches the manifest contributes entry", () => {
    expect(MCP_PROVIDER_ID).toBe("openviking");
  });
});

describe("buildOpenVikingMcpServerDefinition — name + uri", () => {
  it("uses 'OpenViking' as the display name", () => {
    const def = buildOpenVikingMcpServerDefinition(baseCfg());
    expect(def.name).toBe("OpenViking");
  });

  it("appends /mcp to the resolved baseUrl", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({ baseUrl: "https://ov.example.com" }),
    );
    expect(def.uri).toBe("https://ov.example.com/mcp");
  });

  it("strips trailing slashes from baseUrl before appending /mcp", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({ baseUrl: "https://ov.example.com/" }),
    );
    expect(def.uri).toBe("https://ov.example.com/mcp");
  });

  it("strips multiple trailing slashes", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({ baseUrl: "https://ov.example.com///" }),
    );
    expect(def.uri).toBe("https://ov.example.com/mcp");
  });
});

describe("buildOpenVikingMcpServerDefinition — local-only mode", () => {
  it("emits an empty headers object when apiKey + tenant fields are empty", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({
        baseUrl: "http://127.0.0.1:1933",
        apiKey: "",
        accountId: "",
        userId: "",
        agentId: "",
      }),
    );
    expect(def.headers).toEqual({});
  });
});

describe("buildOpenVikingMcpServerDefinition — remote / multi-tenant mode", () => {
  it("attaches Authorization when apiKey is set", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({ apiKey: "sk-test-key" }),
    );
    expect(def.headers["Authorization"]).toBe("Bearer sk-test-key");
  });

  it("attaches every tenant header when populated (Account, User, Agent)", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({
        apiKey: "sk-test",
        accountId: "team-acme",
        userId: "alice",
        agentId: "copilot-vscode",
      }),
    );
    expect(def.headers).toEqual({
      Authorization: "Bearer sk-test",
      "X-OpenViking-Account": "team-acme",
      "X-OpenViking-User": "alice",
      "X-OpenViking-Agent": "copilot-vscode",
    });
  });

  it("only attaches the headers whose cfg fields are non-empty", () => {
    const def = buildOpenVikingMcpServerDefinition(
      baseCfg({
        apiKey: "sk-test",
        accountId: "team",
        userId: "", // empty — must not appear
        agentId: "", // empty — must not appear
      }),
    );
    expect(def.headers).toEqual({
      Authorization: "Bearer sk-test",
      "X-OpenViking-Account": "team",
    });
    expect(def.headers["X-OpenViking-User"]).toBeUndefined();
    expect(def.headers["X-OpenViking-Agent"]).toBeUndefined();
  });

  it("matches the OVClient.buildHeaders shape exactly (so MCP + REST share an identity)", () => {
    const cfg = baseCfg({
      apiKey: "tok",
      accountId: "acct",
      userId: "u",
      agentId: "copilot-vscode",
    });
    const def = buildOpenVikingMcpServerDefinition(cfg);
    expect(def.headers["Authorization"]).toBe(`Bearer ${cfg.apiKey}`);
    expect(def.headers["X-OpenViking-Account"]).toBe(cfg.accountId);
    expect(def.headers["X-OpenViking-User"]).toBe(cfg.userId);
    expect(def.headers["X-OpenViking-Agent"]).toBe(cfg.agentId);
  });
});
