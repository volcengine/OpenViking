import { describe, expect, it, vi } from "vitest";

import contextEnginePlugin from "../../index.js";

function withOpenVikingEnv<T>(
  values: Partial<Record<"OPENVIKING_API_KEY" | "OPENVIKING_BASE_URL", string | undefined>>,
  fn: () => T,
): T {
  const previous = {
    OPENVIKING_API_KEY: process.env.OPENVIKING_API_KEY,
    OPENVIKING_BASE_URL: process.env.OPENVIKING_BASE_URL,
  };
  try {
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
    return fn();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

function createPluginApi(pluginConfig: Record<string, unknown>) {
  return {
    pluginConfig,
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn(),
    registerCommand: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  };
}

describe("plugin registration", () => {
  it("keeps runtime enabled for default no-key trusted deployments", () => {
    withOpenVikingEnv(
      {
        OPENVIKING_API_KEY: undefined,
        OPENVIKING_BASE_URL: undefined,
      },
      () => {
        const api = createPluginApi({});

        contextEnginePlugin.register(api as any);

        expect(api.registerTool).toHaveBeenCalled();
        expect(api.registerContextEngine).toHaveBeenCalledWith("openviking", expect.any(Function));
        expect(api.registerService).toHaveBeenCalledWith(expect.objectContaining({ id: "openviking" }));
        expect(api.logger.warn).not.toHaveBeenCalledWith(
          expect.stringContaining("tools and context-engine are disabled"),
        );
      },
    );
  });

  it("keeps runtime enabled when only OPENVIKING_BASE_URL is configured", () => {
    withOpenVikingEnv(
      {
        OPENVIKING_API_KEY: undefined,
        OPENVIKING_BASE_URL: "http://127.0.0.1:1933",
      },
      () => {
        const api = createPluginApi({});

        contextEnginePlugin.register(api as any);

        expect(api.registerTool).toHaveBeenCalled();
        expect(api.registerContextEngine).toHaveBeenCalledWith("openviking", expect.any(Function));
      },
    );
  });
});
