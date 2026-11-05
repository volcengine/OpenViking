import { Type } from "@sinclair/typebox";
import type { OpenVikingClient } from "../client.js";
import type {
  OpenVikingQuerySession,
  OpenVikingQueryToolContext,
} from "./openviking-query-tools.js";

export type OpenVikingRuleToolsDeps = {
  registerTool: (toolOrFactory: unknown, opts: { name: string }) => void;
  getClient: () => Promise<Pick<OpenVikingClient, "ruleSearch">>;
  resolvePluginSessionRouting: (ctx?: OpenVikingQueryToolContext) => OpenVikingQuerySession;
  isBypassedSession: (ctx?: OpenVikingQueryToolContext) => boolean;
  makeBypassedToolResult: (toolName: string) => unknown;
};

export function registerOpenVikingRuleTools(deps: OpenVikingRuleToolsDeps): void {
  deps.registerTool(
    (ctx: OpenVikingQueryToolContext) => ({
      name: "ov_rule_search",
      label: "Rule Search (OpenViking)",
      description:
        "Check task-specific constraints before a planned action or response. " +
        "Search compiled OpenViking Rules and return complete rule blocks, including applicability conditions and exceptions.",
      parameters: Type.Object({
        task_context: Type.String({
          minLength: 1,
          maxLength: 64000,
          description: "User goal, planned action, and facts affecting Rule applicability.",
        }),
        rule_probability_threshold: Type.Optional(Type.Number({
          minimum: 0,
          maximum: 1,
          default: 0.65,
          description: "Minimum applicability probability.",
        })),
      }, { additionalProperties: false }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (deps.isBypassedSession(ctx)) {
          return deps.makeBypassedToolResult("ov_rule_search");
        }
        const taskContext = params.task_context;
        const threshold = params.rule_probability_threshold === undefined ? 0.65 : params.rule_probability_threshold;
        if (typeof taskContext !== "string" || !taskContext.trim() || taskContext.length > 64000) {
          throw new Error("task_context must be a non-empty string of at most 64000 characters");
        }
        if (typeof threshold !== "number" || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
          throw new Error("rule_probability_threshold must be between 0 and 1");
        }
        const session = deps.resolvePluginSessionRouting(ctx);
        const client = await deps.getClient();
        const result = await client.ruleSearch(taskContext, threshold, session.actorPeerId);
        if (typeof result.rendered !== "string") {
          throw new Error("OpenViking Rule search returned no rendered Rules");
        }
        return { content: [{ type: "text" as const, text: result.rendered }], details: result };
      },
    }),
    { name: "ov_rule_search" },
  );
}
