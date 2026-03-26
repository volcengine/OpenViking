import { beforeEach, describe, expect, it, vi } from "vitest";

const DUPLICATE_REGISTRATION_LOG =
  "openviking: plugin registration already active, skipping duplicate registration";

type MockApi = {
  pluginConfig: Record<string, unknown>;
  logger: {
    info: ReturnType<typeof vi.fn>;
    warn: ReturnType<typeof vi.fn>;
    error: ReturnType<typeof vi.fn>;
    debug: ReturnType<typeof vi.fn>;
  };
  registerTool: ReturnType<typeof vi.fn>;
  registerService: ReturnType<typeof vi.fn>;
  registerContextEngine: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
};

type MockService = {
  id: string;
  start: (ctx?: unknown) => void | Promise<void>;
  stop?: (ctx?: unknown) => void | Promise<void>;
};

function createParsedConfig(overrides: Record<string, unknown> = {}) {
  return {
    mode: "remote",
    configPath: "/tmp/openviking-test.conf",
    port: 1933,
    baseUrl: "http://127.0.0.1:8000",
    agentId: "test-agent",
    apiKey: "test-key",
    targetUri: "viking://user/memories",
    timeoutMs: 30_000,
    autoCapture: true,
    captureMode: "semantic",
    captureMaxLength: 1_000,
    autoRecall: true,
    recallLimit: 5,
    recallScoreThreshold: 0.7,
    recallMaxContentChars: 500,
    recallPreferAbstract: true,
    recallTokenBudget: 2_000,
    ingestReplyAssist: true,
    ingestReplyAssistMinSpeakerTurns: 2,
    ingestReplyAssistMinChars: 120,
    ...overrides,
  };
}

function createMockApi(): MockApi {
  return {
    pluginConfig: {},
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  };
}

async function loadPlugin(
  options: {
    parseImpl?: (value: unknown) => Record<string, unknown>;
  } = {},
) {
  vi.resetModules();

  const parse = vi.fn((value: unknown) => {
    if (options.parseImpl) {
      return options.parseImpl(value);
    }
    return createParsedConfig(value as Record<string, unknown>);
  });

  const localClientCache = new Map<string, { client: unknown; process: unknown }>();
  const localClientPendingPromises = new Map<
    string,
    {
      promise: Promise<unknown>;
      resolve: (client: unknown) => void;
      reject: (err: unknown) => void;
    }
  >();
  class MockOpenVikingClient {
    healthCheck = vi.fn().mockResolvedValue(undefined);
    find = vi.fn().mockResolvedValue({ memories: [] });
    read = vi.fn().mockResolvedValue("");
    addSessionMessage = vi.fn().mockResolvedValue(undefined);
    commitSession = vi.fn().mockResolvedValue({ archived: true, memories_extracted: 0 });
    deleteSession = vi.fn().mockResolvedValue(undefined);
    deleteUri = vi.fn().mockResolvedValue(undefined);
    getSession = vi.fn().mockResolvedValue({ message_count: 0 });
  }

  vi.doMock("../config.js", () => ({
    memoryOpenVikingConfigSchema: { parse },
  }));
  vi.doMock("../client.js", () => ({
    OpenVikingClient: MockOpenVikingClient,
    localClientCache,
    localClientPendingPromises,
    isMemoryUri: vi.fn((uri: string) => uri.startsWith("viking://")),
  }));
  vi.doMock("../process-manager.js", () => ({
    IS_WIN: false,
    waitForHealth: vi.fn().mockResolvedValue(undefined),
    quickRecallPrecheck: vi.fn().mockResolvedValue({ ok: true }),
    withTimeout: vi.fn((promise: Promise<unknown>) => promise),
    resolvePythonCommand: vi.fn().mockReturnValue("python3"),
    prepareLocalPort: vi.fn().mockResolvedValue(8000),
  }));
  vi.doMock("../context-engine.js", () => ({
    createMemoryOpenVikingContextEngine: vi.fn(() => ({
      commitOVSession: vi.fn().mockResolvedValue(undefined),
    })),
  }));

  const module = await import("../index.js");
  return {
    plugin: module.default,
    parse,
    localClientPendingPromises,
  };
}

function getRegisteredService(api: MockApi, callIndex = 0): MockService {
  return api.registerService.mock.calls[callIndex]![0] as MockService;
}

describe("duplicate registration guard (issue #948)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it("registers tools, hooks, context engine, and service on first call", async () => {
    const { plugin } = await loadPlugin();
    const api = createMockApi();

    plugin.register(api);

    expect(api.registerTool).toHaveBeenCalledTimes(3);
    expect(api.registerService).toHaveBeenCalledTimes(1);
    expect(api.registerContextEngine).toHaveBeenCalledTimes(1);
    expect(api.on).toHaveBeenCalled();
    expect(api.logger.info).not.toHaveBeenCalledWith(DUPLICATE_REGISTRATION_LOG);
  });

  it("skips duplicate registration on the same module instance", async () => {
    const { plugin } = await loadPlugin();
    const api = createMockApi();

    plugin.register(api);

    api.logger.info.mockClear();
    api.registerTool.mockClear();
    api.registerService.mockClear();
    api.registerContextEngine.mockClear();
    api.on.mockClear();

    plugin.register(api);

    expect(api.logger.info).toHaveBeenCalledWith(DUPLICATE_REGISTRATION_LOG);
    expect(api.registerTool).not.toHaveBeenCalled();
    expect(api.registerService).not.toHaveBeenCalled();
    expect(api.registerContextEngine).not.toHaveBeenCalled();
    expect(api.on).not.toHaveBeenCalled();
  });

  it("rolls back registration state after config parse failure", async () => {
    let parseAttempts = 0;
    const { plugin, localClientPendingPromises } = await loadPlugin({
      parseImpl: () => {
        parseAttempts += 1;
        if (parseAttempts === 1) {
          throw new Error("config parse failed");
        }
        return createParsedConfig();
      },
    });

    expect(() => plugin.register(createMockApi())).toThrow("config parse failed");
    expect(localClientPendingPromises.size).toBe(0);

    const retryApi = createMockApi();
    plugin.register(retryApi);

    expect(retryApi.registerService).toHaveBeenCalledTimes(1);
    expect(retryApi.registerContextEngine).toHaveBeenCalledTimes(1);
  });

  it("cleans pending local-client state if registration fails after creating it", async () => {
    const { plugin, localClientPendingPromises } = await loadPlugin({
      parseImpl: () => createParsedConfig({ mode: "local" }),
    });

    const failingApi = createMockApi();
    failingApi.registerService.mockImplementation(() => {
      throw new Error("registerService failed");
    });

    expect(() => plugin.register(failingApi)).toThrow("registerService failed");
    expect(localClientPendingPromises.size).toBe(0);

    const retryApi = createMockApi();
    plugin.register(retryApi);

    expect(retryApi.registerService).toHaveBeenCalledTimes(1);
    expect(localClientPendingPromises.size).toBe(1);
  });

  it("allows clean re-registration after stop", async () => {
    const { plugin } = await loadPlugin();
    const api = createMockApi();

    plugin.register(api);
    const service = getRegisteredService(api);

    api.logger.info.mockClear();
    api.registerTool.mockClear();
    api.registerService.mockClear();
    api.registerContextEngine.mockClear();
    api.on.mockClear();

    service.stop?.();
    plugin.register(api);

    expect(api.logger.info).not.toHaveBeenCalledWith(DUPLICATE_REGISTRATION_LOG);
    expect(api.registerTool).toHaveBeenCalledTimes(3);
    expect(api.registerService).toHaveBeenCalledTimes(1);
    expect(api.registerContextEngine).toHaveBeenCalledTimes(1);
    expect(api.on).toHaveBeenCalled();
  });

  it("ignores stale stop calls from an older registration", async () => {
    const { plugin } = await loadPlugin();

    const firstApi = createMockApi();
    plugin.register(firstApi);
    const firstService = getRegisteredService(firstApi);
    firstService.stop?.();

    const secondApi = createMockApi();
    plugin.register(secondApi);

    secondApi.logger.info.mockClear();
    secondApi.registerTool.mockClear();
    secondApi.registerService.mockClear();
    secondApi.registerContextEngine.mockClear();
    secondApi.on.mockClear();

    firstService.stop?.();
    plugin.register(secondApi);

    expect(secondApi.logger.info).toHaveBeenCalledWith(DUPLICATE_REGISTRATION_LOG);
    expect(secondApi.registerTool).not.toHaveBeenCalled();
    expect(secondApi.registerService).not.toHaveBeenCalled();
    expect(secondApi.registerContextEngine).not.toHaveBeenCalled();
    expect(secondApi.on).not.toHaveBeenCalled();
  });
});
