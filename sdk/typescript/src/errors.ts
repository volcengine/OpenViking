import type { JsonObject } from "./types.js";

/** Typed HTTP, server-contract, timeout and network error from the SDK. */
export class OpenVikingError extends Error {
  readonly code: string;
  readonly details: JsonObject;
  readonly statusCode: number | undefined;

  constructor(
    message: string,
    options: {
      code?: string;
      details?: JsonObject;
      statusCode?: number;
      cause?: unknown;
    } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = "OpenVikingError";
    this.code = options.code ?? "UNKNOWN";
    this.details = options.details ?? {};
    this.statusCode = options.statusCode;
  }
}

/** Test whether an unknown value is an OpenViking error with an optional code. */
export const isOpenVikingError = (
  error: unknown,
  code?: string,
): error is OpenVikingError =>
  error instanceof OpenVikingError &&
  (code === undefined || error.code === code);
