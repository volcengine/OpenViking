type TextBlock = {
  type?: string;
  text?: string;
};

type MessageLike = {
  role?: string;
  content?: unknown;
};

type BatchMessage = {
  role: string;
  content: string;
};

type BatchOptions = {
  messages: MessageLike[];
  includeSystemPrompt: boolean;
  includeToolCalls: boolean;
  maxBatchMessages: number;
  dedupeWindow?: number;
};

type CommitClient = {
  createSession: () => Promise<string>;
  addSessionMessage: (sessionId: string, role: string, content: string) => Promise<void>;
  commitSession: (sessionId: string) => Promise<{ extractedCount: number }>;
  deleteSession: (sessionId: string) => Promise<void>;
};

export function normalizeContent(content: unknown): string {
  if (typeof content === "string") {
    return content.trim();
  }
  if (!Array.isArray(content)) {
    return "";
  }

  const parts: string[] = [];
  for (const block of content as TextBlock[]) {
    if (block?.type === "text" && typeof block.text === "string") {
      const text = block.text.trim();
      if (text) {
        parts.push(text);
      }
    }
  }
  return parts.join("\n");
}

function shouldIncludeRole(role: string, includeSystemPrompt: boolean, includeToolCalls: boolean): boolean {
  if (role === "system") {
    return includeSystemPrompt;
  }
  if (role === "tool") {
    return includeToolCalls;
  }
  return role === "user" || role === "assistant";
}

export function toBatchPayload(options: BatchOptions): BatchMessage[] {
  const filtered: BatchMessage[] = [];

  for (const msg of options.messages) {
    const role = msg?.role;
    if (!role || !shouldIncludeRole(role, options.includeSystemPrompt, options.includeToolCalls)) {
      continue;
    }
    const content = normalizeContent(msg.content);
    if (!content) {
      continue;
    }
    filtered.push({ role, content });
  }

  const maxBatchMessages = Math.max(1, options.maxBatchMessages);
  let tail = filtered.slice(-maxBatchMessages);

  const dedupeWindow = options.dedupeWindow ?? 0;
  if (dedupeWindow > 1) {
    const deduped: BatchMessage[] = [];
    for (const item of tail) {
      const recent = deduped.slice(-dedupeWindow + 1);
      const duplicate = recent.some((r) => r.role === item.role && r.content === item.content);
      if (!duplicate) {
        deduped.push(item);
      }
    }
    tail = deduped;
  }

  return tail;
}

export async function writeBatchAndCommit(
  client: CommitClient,
  payload: BatchMessage[],
): Promise<{ extractedCount: number }> {
  const sessionId = await client.createSession();
  try {
    for (const message of payload) {
      await client.addSessionMessage(sessionId, message.role, message.content);
    }
    return await client.commitSession(sessionId);
  } finally {
    try {
      await client.deleteSession(sessionId);
    } catch {
      // Best-effort cleanup must not mask the original write/commit error.
    }
  }
}
