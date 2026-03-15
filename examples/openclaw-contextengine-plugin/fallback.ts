export type FallbackKind = "retrieval_timeout" | "retrieval_error" | "unknown";

export function classifyFallback(error: unknown): FallbackKind {
  const message = error instanceof Error ? error.message.toLowerCase() : String(error ?? "").toLowerCase();

  if (message.includes("timeout") || message.includes("abort")) {
    return "retrieval_timeout";
  }

  if (message.includes("openviking request failed")) {
    return "retrieval_error";
  }

  return "unknown";
}
