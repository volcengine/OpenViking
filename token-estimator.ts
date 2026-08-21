type TokenCountableAgentMessage = {
  role?: string;
  content?: unknown;
};

function isCjkCodePoint(codePoint: number): boolean {
  return (
    (codePoint >= 0x3400 && codePoint <= 0x4dbf) ||
    (codePoint >= 0x4e00 && codePoint <= 0x9fff) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0x20000 && codePoint <= 0x2ebef) ||
    (codePoint >= 0x3040 && codePoint <= 0x30ff) ||
    (codePoint >= 0x31f0 && codePoint <= 0x31ff) ||
    (codePoint >= 0xac00 && codePoint <= 0xd7af) ||
    (codePoint >= 0x1100 && codePoint <= 0x11ff) ||
    (codePoint >= 0x3130 && codePoint <= 0x318f) ||
    (codePoint >= 0xff00 && codePoint <= 0xffef) ||
    (codePoint >= 0x3000 && codePoint <= 0x303f)
  );
}

function codePointWeight(codePoint: number): number {
  if (isCjkCodePoint(codePoint)) {
    return 1.5;
  }
  if (codePoint > 0xffff) {
    return 2;
  }
  return 0.25;
}

export function estimateTextTokens(text: string | null | undefined): number {
  if (!text) {
    return 0;
  }

  let weightedTokens = 0;
  for (const char of text) {
    weightedTokens += codePointWeight(char.codePointAt(0) ?? 0);
  }
  return Math.ceil(weightedTokens);
}

export function estimateSerializedTokens(value: unknown): number {
  if (value === null || value === undefined) {
    return 0;
  }
  if (typeof value === "string") {
    return estimateTextTokens(value);
  }
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return estimateTextTokens(String(value));
  }

  const serialized = JSON.stringify(value);
  return estimateTextTokens(serialized ?? "");
}

export function estimateAgentMessageTokens(message: TokenCountableAgentMessage): number {
  return estimateSerializedTokens(message);
}

export function estimateAgentMessagesTokens(messages: TokenCountableAgentMessage[]): number {
  return estimateSerializedTokens(messages);
}
