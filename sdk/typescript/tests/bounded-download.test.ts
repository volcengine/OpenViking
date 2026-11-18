import { describe, expect, it, vi } from "vitest";
import { OpenVikingClient, OpenVikingError } from "../src/index.js";

function streamed(chunks: Uint8Array[], headers: Record<string, string> = {}) {
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>(
    {
      pull(controller) {
        const chunk = chunks.shift();
        if (chunk) controller.enqueue(chunk);
        else controller.close();
      },
      cancel,
    },
    { highWaterMark: 0 },
  );
  return { response: new Response(stream, { headers }), cancel };
}
const bytes = (value: string) => new TextEncoder().encode(value);

function client(fetcher: typeof fetch) {
  return new OpenVikingClient({
    baseUrl: "https://example.invalid",
    apiKey: "synthetic",
    account: "a",
    user: "u",
    actorPeerId: "actor",
    fetch: fetcher,
  });
}

describe("bounded binary download", () => {
  it("forwards a limit and preserves the identity owner", async () => {
    const body = streamed([bytes("ab"), bytes("cd")]);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(body.response);
    expect(
      await client(fetcher).downloadBytes("resources/demo/input.bin", {
        maxBytes: 4,
      }),
    ).toEqual(bytes("abcd"));
    const [url, init] = fetcher.mock.calls[0]!;
    expect(new URL(String(url)).searchParams.get("max_bytes")).toBe("4");
    expect(new Headers(init?.headers).get("X-OpenViking-User")).toBe("u");
    expect(new Headers(init?.headers).get("X-OpenViking-Actor-Peer")).toBe(
      "actor",
    );
    expect(body.cancel).not.toHaveBeenCalled();
  });

  it("cancels an oversized chunked response even if an old server ignores the limit", async () => {
    const body = streamed([bytes("ab"), bytes("cd"), bytes("never consumed")]);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(body.response);
    await expect(
      client(fetcher).downloadBytes("resources/demo/input.bin", {
        maxBytes: 3,
      }),
    ).rejects.toMatchObject({ code: "RESOURCE_EXHAUSTED" });
    expect(body.cancel).toHaveBeenCalledOnce();
  });

  it("rejects an oversized declared body without reading it", async () => {
    const body = streamed([bytes("ab")], { "content-length": "2" });
    await expect(
      client(
        vi.fn<typeof fetch>().mockResolvedValue(body.response),
      ).downloadBytes("resources/demo/input.bin", { maxBytes: 1 }),
    ).rejects.toMatchObject({ code: "RESOURCE_EXHAUSTED" });
    expect(body.cancel).toHaveBeenCalledOnce();
  });

  it("accepts an empty body with a zero limit", async () => {
    const body = streamed([]);
    expect(
      await client(
        vi.fn<typeof fetch>().mockResolvedValue(body.response),
      ).downloadBytes("resources/demo/input.bin", { maxBytes: 0 }),
    ).toEqual(new Uint8Array());
  });

  it("retains server error mapping within the bounded body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "error",
          error: { code: "NOT_FOUND", message: "missing" },
        }),
        { status: 404, headers: { "content-type": "application/json" } },
      ),
    );
    await expect(
      client(fetcher).downloadBytes("resources/demo/input.bin", {
        maxBytes: 1024,
      }),
    ).rejects.toBeInstanceOf(OpenVikingError);
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it.each([-1, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid limit %s before transport",
    async (maxBytes) => {
      const fetcher = vi.fn<typeof fetch>();
      await expect(
        client(fetcher).downloadBytes("resources/demo/input.bin", { maxBytes }),
      ).rejects.toBeInstanceOf(TypeError);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("cancels and releases the reader after a streamed transport error", async () => {
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(new Error("synthetic disconnect"));
      },
    });
    const response = new Response(stream);
    await expect(
      client(vi.fn<typeof fetch>().mockResolvedValue(response)).downloadBytes(
        "resources/demo/input.bin",
        { maxBytes: 10 },
      ),
    ).rejects.toMatchObject({ code: "UNAVAILABLE" });
    expect(stream.locked).toBe(false);
  });

  it("checks decoded bytes even when a compressed Content-Length is smaller", async () => {
    const body = streamed([bytes("decoded body exceeds limit")], {
      "content-encoding": "gzip",
      "content-length": "1",
    });
    await expect(
      client(
        vi.fn<typeof fetch>().mockResolvedValue(body.response),
      ).downloadBytes("resources/demo/input.bin", { maxBytes: 10 }),
    ).rejects.toMatchObject({ code: "RESOURCE_EXHAUSTED" });
    expect(body.cancel).toHaveBeenCalledOnce();
  });

  it("uses the existing caller-abort owner", async () => {
    const aborter = new AbortController();
    let start!: () => void;
    const started = new Promise<void>((resolve) => {
      start = resolve;
    });
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_url, init) => {
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            init!.signal!.addEventListener(
              "abort",
              () => controller.error(init!.signal!.reason),
              { once: true },
            );
            start();
          },
        });
        return new Response(stream);
      });
    const result = client(fetcher).downloadBytes("resources/demo/input.bin", {
      maxBytes: 1,
      signal: aborter.signal,
    });
    await started;
    aborter.abort();
    await expect(result).rejects.toMatchObject({ code: "ABORTED" });
  });
});
