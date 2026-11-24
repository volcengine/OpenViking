import { BlockAssembler } from "@deepseek-ai/dsh-llm";
import { pluginMessage } from "./capture.mjs";

/** One host model call, without an agent loop, tools, or session-log writes. */
export function createRecallCompressor(agent, config, signal) {
  const llm = agent.ctx?.llm;
  const provider = agent.options?.provider;
  const model = config.recallCompressModel || agent.options?.model;
  if (!llm?.stream || !provider || !model) return null;

  return async prompt => {
    const controller = new AbortController();
    const abort = () => controller.abort(signal.reason);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(
      () => controller.abort(new Error("Recall compression timed out")),
      config.recallCompressTimeoutMs,
    );
    let rejectAbort;
    const aborted = new Promise((_, reject) => {
      rejectAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener("abort", rejectAbort, { once: true });
      if (controller.signal.aborted) rejectAbort();
    });
    try {
      const generate = async () => {
        controller.signal.throwIfAborted();
        const output = new BlockAssembler();
        let stopped = false;
        for await (const chunk of llm.stream({
          provider,
          model,
          ...(config.recallCompressThinking ? { reasoningEffort: config.recallCompressThinking } : {}),
          messages: [pluginMessage(prompt)],
          tools: [],
          signal: controller.signal,
        })) {
          controller.signal.throwIfAborted();
          output.push(chunk);
          if (chunk.type === "finish") {
            if (chunk.reason.kind !== "stop") throw new Error(`Recall compression: ${chunk.reason.kind}`);
            stopped = true;
          }
        }
        if (!stopped) throw new Error("Recall compression ended without a successful finish");
        return output.blocks().filter(block => block.type === "text").map(block => block.text).join("\n");
      };
      // Bound the hook even if a provider fails to honor cancellation promptly.
      return await Promise.race([generate(), aborted]);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      controller.signal.removeEventListener("abort", rejectAbort);
    }
  };
}
