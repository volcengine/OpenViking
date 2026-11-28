/** Register the server's tool catalogue once per pi session. */
import { AjvJsonSchemaValidator } from "@modelcontextprotocol/client/validators/ajv";
import type { McpBridge } from "./lib/mcp-bridge.mjs";

export const TOOL_NAME_PREFIX = "openviking_";

export function registerMcpTools(pi: any, bridge: McpBridge): string[] {
  const validator = new AjvJsonSchemaValidator();
  return bridge.state.tools.map((tool) => {
    const name = TOOL_NAME_PREFIX + tool.name;
    const validate = validator.getValidator(tool.inputSchema);
    pi.registerTool({
      name,
      label: "OpenViking " + tool.name,
      description: tool.description?.trim() || tool.title || "OpenViking " + tool.name,
      parameters: tool.inputSchema,
      // Validate before pi coerces values or drops optional null properties.
      prepareArguments(args: unknown) {
        const result = validate(args);
        if (!result.valid) throw new Error("Invalid arguments for " + name + ": " + result.errorMessage);
        return args;
      },
      async execute(_toolCallId: string, params: Record<string, unknown>, signal?: AbortSignal) {
        return bridge.callTool(tool.name, params, { signal });
      },
    });
    return name;
  });
}
