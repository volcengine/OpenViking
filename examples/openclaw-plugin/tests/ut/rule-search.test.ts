import { describe, expect, it, vi } from "vitest";
import { OpenVikingClient } from "../../client.js";
import { registerOpenVikingRuleTools } from "../../plugin/openviking-rule-tools.js";

function fixture(rendered = "rule\n".repeat(17000) + "LAST RULE") {
  const transport = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ status: "ok", result: { rendered, rules: [] } }),
    { headers: { "Content-Type": "application/json" } },
  ));
  const client = new OpenVikingClient("http://ov.test", "key", "agent", 1000, "acct", "alice", undefined, { transport });
  const registerTool = vi.fn();
  const deps = {
    registerTool,
    getClient: vi.fn().mockResolvedValue(client),
    resolvePluginSessionRouting: () => ({ agentId: "agent", actorPeerId: "project" }),
    isBypassedSession: vi.fn().mockReturnValue(false),
    makeBypassedToolResult: vi.fn().mockReturnValue({ bypassed: true }),
  };
  registerOpenVikingRuleTools(deps);
  const tool = registerTool.mock.calls[0][0]({ sessionId: "session" });
  return { tool, deps, transport, rendered };
}

describe("Rule search tool", () => {
  it("forwards identity and thresholds through the client without cutting Rules", async () => {
    const { tool, transport, rendered } = fixture();
    const result = await tool.execute("call", { task_context: "deploy", rule_probability_threshold: 0 });
    expect(result.content).toEqual([{ type: "text", text: rendered }]);
    const [url, init] = transport.mock.calls[0];
    expect(url).toBe("http://ov.test/api/v1/rules/search");
    expect(JSON.parse(init.body)).toEqual({ task_context: "deploy", rule_probability_threshold: 0 });
    const headers = new Headers(init.headers);
    expect(headers.get("X-API-Key")).toBe("key");
    expect(headers.get("X-OpenViking-Account")).toBe("acct");
    expect(headers.get("X-OpenViking-User")).toBe("alice");
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("project");
  });

  it("rejects invalid inputs and honors bypass before any request", async () => {
    const { tool, deps, transport } = fixture();
    for (const params of [
      { task_context: " " }, { task_context: "x".repeat(64001) },
      { task_context: "deploy", rule_probability_threshold: null },
      { task_context: "deploy", rule_probability_threshold: -1 },
      { task_context: "deploy", rule_probability_threshold: NaN },
    ]) {
      await expect(tool.execute("call", params)).rejects.toThrow();
    }
    expect(transport).not.toHaveBeenCalled();
    deps.isBypassedSession.mockReturnValue(true);
    expect(await tool.execute("call", { task_context: "deploy" })).toEqual({ bypassed: true });
    expect(deps.getClient).not.toHaveBeenCalled();
  });

  it("does not convert a server error or malformed response into no Rules", async () => {
    const { tool, transport } = fixture();
    transport.mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "UNAVAILABLE", message: "failed" } }), { status: 503 }));
    await expect(tool.execute("call", { task_context: "deploy" })).rejects.toThrow();
    transport.mockResolvedValueOnce(new Response(JSON.stringify({ status: "ok", result: {} })));
    await expect(tool.execute("call", { task_context: "deploy" })).rejects.toThrow(/no rendered Rules/);
  });
});
