import { OpenVikingError } from "./errors.js";
import type {
  ClientConfig,
  JsonObject,
  ResponseEnvelope,
  UploadMode,
} from "./types.js";

const compact = (value: JsonObject): JsonObject =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null,
    ),
  );

export interface TransportOptions {
  query?: JsonObject;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

/** Internal HTTP module shared by JSON and binary SDK operations. */
export class OpenVikingTransport {
  readonly baseUrl: string;
  readonly uploadMode: UploadMode | undefined;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly headers: Headers;
  private readonly timeout: number;
  private readonly profile: boolean;

  constructor(config: ClientConfig) {
    if (!config.baseUrl?.trim())
      throw new TypeError("OpenViking: baseUrl is required");
    const url = new URL(config.baseUrl);
    if (!/^https?:$/.test(url.protocol))
      throw new TypeError("OpenViking: baseUrl must use http or https");
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.fetcher = config.fetch ?? globalThis.fetch;
    if (!this.fetcher)
      throw new TypeError("OpenViking: fetch is not available");
    this.timeout = config.timeout ?? 60_000;
    this.profile = config.profile ?? false;
    this.uploadMode = config.uploadMode;
    this.headers = new Headers(config.headers);
    if (config.apiKey) this.headers.set("X-API-Key", config.apiKey);
    if (config.account)
      this.headers.set("X-OpenViking-Account", config.account);
    if (config.user) this.headers.set("X-OpenViking-User", config.user);
    if (config.actorPeerId)
      this.headers.set("X-OpenViking-Actor-Peer", config.actorPeerId);
  }

  request<T>(
    method: string,
    path: string,
    options: TransportOptions = {},
  ): Promise<T> {
    return this.consume(method, path, options, (response) =>
      this.parseResponse<T>(response),
    );
  }

  async consume<T>(
    method: string,
    path: string,
    options: TransportOptions,
    consume: (response: Response) => Promise<T>,
  ): Promise<T> {
    const url = new URL(
      `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`,
    );
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item !== undefined && item !== null && item !== "")
            url.searchParams.append(key, String(item));
        }
      } else if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
    if (this.profile) url.searchParams.set("profile", "1");
    const headers = new Headers(this.headers);
    let body: string | FormData | undefined;
    if (options.form) body = options.form;
    else if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.body);
    }
    const controller = new AbortController();
    let abortSource: "caller" | "timeout" | undefined;
    const abort = () => {
      if (controller.signal.aborted) return;
      abortSource = "caller";
      controller.abort(options.signal?.reason);
    };
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) abort();
    const timer = setTimeout(() => {
      if (controller.signal.aborted) return;
      abortSource = "timeout";
      controller.abort(new DOMException("Request timed out", "TimeoutError"));
    }, this.timeout);
    try {
      const init: RequestInit = { method, headers, signal: controller.signal };
      if (body !== undefined) init.body = body;
      return await consume(await this.fetcher(url, init));
    } catch (error) {
      if (abortSource === "timeout")
        throw new OpenVikingError("Request timed out", {
          code: "DEADLINE_EXCEEDED",
          cause: error,
        });
      if (abortSource === "caller")
        throw new OpenVikingError("Request was aborted by the caller", {
          code: "ABORTED",
          cause: error,
        });
      if (error instanceof OpenVikingError) throw error;
      throw new OpenVikingError(
        error instanceof Error ? error.message : "Network request failed",
        { code: "UNAVAILABLE", cause: error },
      );
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
    }
  }

  async parseResponse<T>(response: Response): Promise<T> {
    const text = await response.text();
    let envelope: ResponseEnvelope<T> = {};
    if (text) {
      try {
        envelope = JSON.parse(text) as ResponseEnvelope<T>;
      } catch (cause) {
        throw new OpenVikingError(`HTTP ${response.status}: ${text}`, {
          statusCode: response.status,
          cause,
        });
      }
    }
    if (envelope.error || envelope.status === "error" || !response.ok) {
      const info = envelope.error;
      throw new OpenVikingError(
        info?.message ?? String(envelope.detail ?? `HTTP ${response.status}`),
        compact({
          code: info?.code,
          details: info?.details,
          statusCode: response.status,
        }) as { code?: string; details?: JsonObject; statusCode?: number },
      );
    }
    return envelope.result as T;
  }
}
