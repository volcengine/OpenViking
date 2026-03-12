import type { FallbackKind } from "./fallback.js";

export type FallbackTelemetry = {
  event: "openviking_retrieval_fallback";
  fallbackKind: FallbackKind;
  errorMessage: string;
};

export function buildFallbackTelemetry(input: {
  fallbackKind: FallbackKind;
  error: unknown;
}): FallbackTelemetry {
  const errorMessage =
    input.error instanceof Error ? input.error.message : String(input.error ?? "unknown_error");

  return {
    event: "openviking_retrieval_fallback",
    fallbackKind: input.fallbackKind,
    errorMessage,
  };
}
