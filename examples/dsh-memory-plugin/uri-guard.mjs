import host from "./host.mjs";
import { toolCall } from "./shared/flow/index.mjs";

const payloadOf = exec => ({ tool: { name: exec.name, arguments: exec.arguments } });

export async function guardVikingUri(exec, next) {
  const output = toolCall(host, payloadOf(exec));
  return output.deny ? host.emit("tools/pre-execute", output) : next();
}

// Delegates first so a later listener's block or content replacement survives;
// the notice only rides along as one more context.
export async function noticeVikingUri(exec, _result, next) {
  const payload = payloadOf(exec);
  const output = toolCall(host, payload);
  if (!output.notice) return next();
  const decision = await next();
  return {
    ...decision,
    additionalContexts: [...(decision.additionalContexts ?? []), host.emit("tools/post-execute", output, payload)],
  };
}
