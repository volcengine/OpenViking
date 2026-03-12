import { normalizeContent } from "./ingestion.js";

type MessageLike = {
  role?: string;
  content?: unknown;
};

type BuildTurnQueryOptions = {
  skipGreeting?: boolean;
  minQueryChars?: number;
};

type MemoryLike = {
  uri: string;
  score?: number;
  content?: string;
  level?: number;
};

const GREETING_TOKENS = new Set([
  "hi",
  "hello",
  "hey",
  "你好",
  "您好",
  "嗨",
  "哈喽",
  "早上好",
  "下午好",
  "晚上好",
]);

function normalizeGreetingCandidate(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[\s!,.?;:'"`~\-，。！？、；：]/g, "");
}

function isGreeting(text: string): boolean {
  const normalized = normalizeGreetingCandidate(text);
  if (!normalized) {
    return false;
  }
  return GREETING_TOKENS.has(normalized);
}

export function buildTurnQuery(
  messages: MessageLike[],
  maxUserTurns: number,
  options: BuildTurnQueryOptions = {},
): string {
  if (maxUserTurns <= 0) {
    return "";
  }

  const minChars = Math.max(
    1,
    Number.isFinite(options.minQueryChars) ? Math.floor(options.minQueryChars as number) : 1,
  );
  const skipGreeting = options.skipGreeting === true;

  const userTexts: string[] = [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role !== "user") {
      continue;
    }
    const text = normalizeContent(message.content);
    if (!text) {
      continue;
    }
    if (text.length < minChars) {
      continue;
    }
    if (skipGreeting && isGreeting(text)) {
      continue;
    }
    userTexts.push(text);
    if (userTexts.length >= maxUserTurns) {
      break;
    }
  }

  return userTexts.reverse().join("\n");
}

export function filterAndRank(
  memories: MemoryLike[],
  scoreThreshold: number,
  topK: number,
): MemoryLike[] {
  if (topK <= 0) {
    return [];
  }

  const bestByUri = new Map<string, MemoryLike>();
  for (const memory of memories) {
    if (!memory?.uri) {
      continue;
    }
    const score = memory.score ?? 0;
    if (score < scoreThreshold) {
      continue;
    }

    const existing = bestByUri.get(memory.uri);
    const existingScore = existing?.score ?? 0;
    if (!existing || score > existingScore) {
      bestByUri.set(memory.uri, memory);
    }
  }

  return [...bestByUri.values()]
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
    .slice(0, topK);
}
