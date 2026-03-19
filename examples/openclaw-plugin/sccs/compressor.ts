import { SummaryExtractor } from "./summarizer.js";
import { extractTextContent, hasRefId, isToolRole, md5Hex, setTextContent } from "./utils.js";
import type { RefStore } from "./storage.js";
export type CompressorConfig = {
  compressThreshold: number;
  summaryMaxChars: number;
  enableSmartSummary: boolean;
  storageTtlSeconds: number;
};

export const REF_ID_INSTRUCTION = `\n=== REF_ID DECISION GUIDELINES ===\nWhen you see any [REF_ID: xxx] in a tool response:\n1. Read the summary carefully.\n2. Ask yourself: 'Does this summary contain enough information for my current task?'\n- Yes → proceed normally, ignore the REF_ID.\n- No  → call fetch_original_data for that REF_ID.\n3. Common cases where you SHOULD fetch:\n- You need more than ~30 lines of code\n- You need exact line numbers/indentation\n- You plan to edit/replace and need full context\n4. Common cases where you can skip:\n- You only needed to confirm a function exists\n- You only care about a small part already in the summary\n`;

const OPENCLAW_CONFIG_WHITELIST = [
  "# SOUL.md",
  "# MEMORY.md",
  "# USER.md",
  "# AGENTS.md",
  "# HEARTBEAT.md",
  "# IDENTIFY.md",
  "# TOOLS.md",
  "# BOOTSTRAP.md"
];

function isOpenClawConfigFile(text: string): boolean {
  const firstLine = text.trim().split('\n')[0]?.trim();
  if (!firstLine) return false;
  return OPENCLAW_CONFIG_WHITELIST.some(pattern => firstLine.includes(pattern));
}

async function buildSummary(params: { text: string; config: CompressorConfig }): Promise<{ refId: string; summary: string }> {
  const summarizer = new SummaryExtractor(params.config.summaryMaxChars);
  const summary = summarizer.summarize(params.text, params.config.enableSmartSummary);
  return { refId: md5Hex(params.text), summary };
}

export async function compressToolMessages(params: {
  messages: Array<{ role?: unknown; content?: unknown }>;
  config: CompressorConfig;
  store: RefStore;
  logger?: { info?: (msg: string) => void; warn?: (msg: string) => void };
}): Promise<{ messages: Array<{ role?: unknown; content?: unknown }>; systemPromptAddition?: string; compressedCount: number }> {
  const { messages, config, store, logger } = params;
  let compressedCount = 0;
  const nextMessages = messages.map((msg) => ({ ...msg }));

  for (let i = 0; i < nextMessages.length; i++) {
    const msg = nextMessages[i];
    if (!isToolRole(msg.role)) continue;
    const text = extractTextContent(msg.content);
    if (!text) continue;
    if (text.length <= config.compressThreshold || hasRefId(text)) continue;
    if (isOpenClawConfigFile(text)) {
      continue;
    }
    const summaryResult = await buildSummary({ text, config });
    await store.set(summaryResult.refId, text, config.storageTtlSeconds);
    const compressed = `[REF_ID: ${summaryResult.refId}] (Summary: ${summaryResult.summary}). NOTE: You can pass this REF_ID directly as a tool parameter.`;
    nextMessages[i] = setTextContent(msg, compressed);
    compressedCount += 1;
    logger?.info?.(`[sccs] compressed tool output #${i} -> REF_ID ${summaryResult.refId.slice(0, 8)}...`);
  }

  return {
    messages: nextMessages,
    compressedCount,
    systemPromptAddition: compressedCount > 0 ? REF_ID_INSTRUCTION : undefined,
  };
}
