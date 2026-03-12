type MessageLike = {
  role?: string;
  content?: unknown;
  toolCallId?: unknown;
  tool_call_id?: unknown;
  toolName?: unknown;
  tool_name?: unknown;
  isError?: unknown;
  is_error?: unknown;
};

type ToolCallLike = {
  id?: string;
  name: string;
  paramPattern: string;
};

type ToolResultLike = {
  toolCallId?: string;
  toolName?: string;
  isError: boolean;
  errorPattern?: string;
};

type ToolStats = {
  calls: number;
  success: number;
  fail: number;
  paramPatterns: Map<string, number>;
  errorPatterns: Map<string, number>;
};

const TOOL_CALL_BLOCK_TYPES = new Set(["toolCall", "toolUse", "functionCall"]);
const ERROR_HINT_RE =
  /(timeout|timed out|rate limit|not found|permission|forbidden|unauthorized|invalid|failed|error|exception|reject|abort|network|connection|超时|失败|报错|异常|拒绝|无权限|未找到)/i;
const SUCCESS_HINT_RE = /(success|succeeded|done|fixed|resolved|passed|completed|ok|成功|完成|已修复|通过)/i;
const ERROR_WORD_RE = /(error|failed|timeout|exception|invalid|reject|abort|超时|失败|报错|异常|拒绝)/i;
const ERROR_PATTERN_PRIORITY = [
  "timeout",
  "timed out",
  "rate limit",
  "unauthorized",
  "forbidden",
  "permission",
  "not found",
  "invalid",
  "failed",
  "error",
  "exception",
  "reject",
  "abort",
  "network",
  "connection",
  "超时",
  "失败",
  "报错",
  "异常",
  "拒绝",
  "无权限",
  "未找到",
];

const INTENT_HINTS: Array<{ label: string; pattern: RegExp }> = [
  { label: "debug", pattern: /\bdebug\b|调试|排查|定位/i },
  { label: "fix", pattern: /\bfix\b|修复|解决/i },
  { label: "test", pattern: /\btest\b|测试|用例/i },
  { label: "refactor", pattern: /\brefactor\b|重构/i },
  { label: "search", pattern: /\bsearch\b|检索|查找/i },
  { label: "memory", pattern: /\bmemory\b|记忆|回忆/i },
];

function normalizeText(content: unknown): string {
  if (typeof content === "string") {
    return content.trim();
  }
  if (!Array.isArray(content)) {
    return "";
  }
  const parts: string[] = [];
  for (const block of content) {
    if (!block || typeof block !== "object") {
      continue;
    }
    const maybeText = (block as { text?: unknown }).text;
    if (typeof maybeText === "string" && maybeText.trim().length > 0) {
      parts.push(maybeText.trim());
    }
  }
  return parts.join("\n");
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function getString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : undefined;
}

function bumpCounter(map: Map<string, number>, key: string): void {
  map.set(key, (map.get(key) ?? 0) + 1);
}

function resolveParamPattern(input: unknown): string {
  const objectInput = asRecord(input);
  if (!objectInput) {
    return "(none)";
  }
  const keys = Object.keys(objectInput).sort();
  return keys.length > 0 ? keys.join("+") : "(none)";
}

function resolveErrorPattern(text: string): string {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  const firstLine = lines[0] ?? "";
  if (!firstLine) {
    return "unknown";
  }
  const firstLineLower = firstLine.toLowerCase();
  for (const pattern of ERROR_PATTERN_PRIORITY) {
    if (firstLineLower.includes(pattern)) {
      return pattern;
    }
  }
  const hint = firstLine.match(ERROR_HINT_RE)?.[1];
  if (hint) {
    return hint.toLowerCase();
  }
  return firstLine.length > 48 ? `${firstLine.slice(0, 48)}...` : firstLine;
}

function formatTopCounters(map: Map<string, number>, limit: number, fallback: string): string {
  const ranked = [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (ranked.length === 0) {
    return fallback;
  }
  return ranked
    .slice(0, limit)
    .map(([name, count]) => `${name}(${count})`)
    .join(", ");
}

function extractToolCalls(messages: MessageLike[], toolNameByLower: Map<string, string>): ToolCallLike[] {
  const calls: ToolCallLike[] = [];
  for (const message of messages) {
    if (message?.role !== "assistant" || !Array.isArray(message.content)) {
      continue;
    }
    for (const block of message.content) {
      const record = asRecord(block);
      if (!record) {
        continue;
      }
      const type = getString(record.type);
      if (!type || !TOOL_CALL_BLOCK_TYPES.has(type)) {
        continue;
      }
      const rawName = getString(record.name);
      if (!rawName) {
        continue;
      }
      const name = toolNameByLower.get(rawName.toLowerCase());
      if (!name) {
        continue;
      }
      calls.push({
        id: getString(record.id),
        name,
        paramPattern: resolveParamPattern(record.input ?? record.arguments),
      });
    }
  }
  return calls;
}

function extractToolResults(messages: MessageLike[], toolNameByLower: Map<string, string>): ToolResultLike[] {
  const results: ToolResultLike[] = [];
  for (const message of messages) {
    if (message?.role !== "toolResult" && message?.role !== "tool") {
      continue;
    }
    const text = normalizeText(message.content);
    const explicitError =
      typeof message.isError === "boolean"
        ? message.isError
        : typeof message.is_error === "boolean"
          ? message.is_error
          : undefined;
    const isError = explicitError ?? ERROR_WORD_RE.test(text);
    const rawToolName = getString(message.toolName) ?? getString(message.tool_name);
    const toolName = rawToolName ? toolNameByLower.get(rawToolName.toLowerCase()) : undefined;
    results.push({
      toolCallId: getString(message.toolCallId) ?? getString(message.tool_call_id),
      toolName,
      isError,
      errorPattern: isError ? resolveErrorPattern(text) : undefined,
    });
  }
  return results;
}

function shiftFromMap(map: Map<string, ToolResultLike[]>, key: string): ToolResultLike | undefined {
  const queue = map.get(key);
  if (!queue || queue.length === 0) {
    return undefined;
  }
  const head = queue.shift();
  if (!queue.length) {
    map.delete(key);
  }
  return head;
}

function resolveSkillAliases(skill: string): string[] {
  const aliases = new Set<string>([skill.toLowerCase()]);
  const colon = skill.lastIndexOf(":");
  if (colon >= 0 && colon < skill.length - 1) {
    aliases.add(skill.slice(colon + 1).toLowerCase());
  }
  return [...aliases];
}

function containsAnyAlias(text: string, aliases: string[]): boolean {
  const normalized = text.toLowerCase();
  return aliases.some((alias) => normalized.includes(alias));
}

function inferIntentLabel(text: string, fallback: string): string {
  for (const hint of INTENT_HINTS) {
    if (hint.pattern.test(text)) {
      return hint.label;
    }
  }
  return fallback;
}

export function buildToolMemoryHints(
  tools: string[],
  intent: string,
  messages: MessageLike[] = [],
): string {
  if (tools.length === 0) {
    return "";
  }
  const toolNameByLower = new Map(tools.map((tool) => [tool.toLowerCase(), tool]));
  const calls = extractToolCalls(messages, toolNameByLower);
  const results = extractToolResults(messages, toolNameByLower);

  const resultsById = new Map<string, ToolResultLike[]>();
  const resultsByName = new Map<string, ToolResultLike[]>();
  for (const result of results) {
    if (result.toolCallId) {
      const queue = resultsById.get(result.toolCallId) ?? [];
      queue.push(result);
      resultsById.set(result.toolCallId, queue);
    }
    if (result.toolName) {
      const queue = resultsByName.get(result.toolName) ?? [];
      queue.push(result);
      resultsByName.set(result.toolName, queue);
    }
  }

  const stats = new Map<string, ToolStats>();
  for (const tool of tools) {
    stats.set(tool, {
      calls: 0,
      success: 0,
      fail: 0,
      paramPatterns: new Map<string, number>(),
      errorPatterns: new Map<string, number>(),
    });
  }

  for (const call of calls) {
    const item = stats.get(call.name);
    if (!item) {
      continue;
    }
    item.calls += 1;
    bumpCounter(item.paramPatterns, call.paramPattern);

    const matched =
      (call.id ? shiftFromMap(resultsById, call.id) : undefined) ?? shiftFromMap(resultsByName, call.name);
    if (!matched) {
      continue;
    }
    if (matched.isError) {
      item.fail += 1;
      bumpCounter(item.errorPatterns, matched.errorPattern ?? "unknown");
    } else {
      item.success += 1;
    }
  }

  const lines = [`Tool hints for ${intent}:`];
  for (const tool of tools) {
    const item = stats.get(tool)!;
    const successRate = item.calls > 0 ? `${Math.round((item.success / item.calls) * 100)}%` : "n/a";
    lines.push(
      `- ${tool}: calls=${item.calls}, successRate=${successRate}, commonParams=${formatTopCounters(item.paramPatterns, 2, "none")}, errorPatterns=${formatTopCounters(item.errorPatterns, 2, "none")}`,
    );
  }

  return lines.join("\n");
}

export function buildSkillMemoryAugmentation(
  skills: string[],
  intent: string,
  messages: MessageLike[] = [],
): string {
  if (skills.length === 0) {
    return "";
  }

  const textEntries = messages
    .map((message, index) => ({
      role: message.role ?? "",
      text: normalizeText(message.content),
      index,
    }))
    .filter((entry) => entry.text.length > 0);

  const lines = [`Skill hints for ${intent}:`];
  for (const skill of skills) {
    const aliases = resolveSkillAliases(skill);
    const mentions = textEntries.filter((entry) => containsAnyAlias(entry.text, aliases));
    const intentPatterns = new Map<string, number>();
    const errorPatterns = new Map<string, number>();
    let success = 0;
    let fail = 0;

    for (const mention of mentions) {
      if (mention.role === "user") {
        bumpCounter(intentPatterns, inferIntentLabel(mention.text, "general"));
      }
      const nextAssistant = textEntries.find(
        (entry) =>
          entry.role === "assistant" &&
          entry.index > mention.index &&
          entry.index <= mention.index + 4,
      );
      if (!nextAssistant) {
        continue;
      }
      if (ERROR_WORD_RE.test(nextAssistant.text)) {
        fail += 1;
        bumpCounter(errorPatterns, resolveErrorPattern(nextAssistant.text));
      } else if (SUCCESS_HINT_RE.test(nextAssistant.text)) {
        success += 1;
      }
    }

    const measured = success + fail;
    const successRate = measured > 0 ? `${Math.round((success / measured) * 100)}%` : "n/a";
    lines.push(
      `- ${skill}: mentions=${mentions.length}, successRate=${successRate}, commonIntents=${formatTopCounters(intentPatterns, 2, "none")}, errorPatterns=${formatTopCounters(errorPatterns, 2, "none")}`,
    );
  }

  return lines.join("\n");
}

export function buildOvCliGuidance(input: { baseUrl: string; fallbackNote: string }): string {
  return [
    `Use ov CLI against ${input.baseUrl} when manual OpenViking checks are needed.`,
    "Common commands: ov health, ov find, ov sessions.",
    input.fallbackNote,
  ].join("\n");
}
