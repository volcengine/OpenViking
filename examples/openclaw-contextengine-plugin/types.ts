export type ContextEngineOpenVikingPluginConfig = {
  mode: "local" | "remote";
  connection: {
    baseUrl: string;
    timeoutMs: number;
    apiKey: string;
    agentId: string;
  };
  retrieval: {
    enabled: boolean;
    lastNUserMessages: number;
    skipGreeting: boolean;
    minQueryChars: number;
    targetUri: string;
    injectMode: "simulated_tool_result" | "text";
    scoreThreshold: number;
  };
  profileInjection: {
    enabled: boolean;
    qualityGateMinScore: number;
    maxChars: number;
  };
  ingestion: {
    writeMode: "compact_batch" | "after_turn_batch";
    maxBatchMessages: number;
  };
};
