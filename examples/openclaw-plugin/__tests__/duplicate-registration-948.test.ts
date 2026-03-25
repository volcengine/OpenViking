import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the modules before importing the plugin
vi.mock("./config.js", () => ({
  memoryOpenVikingConfigSchema: {
    parse: vi.fn((config) => ({
      mode: "remote",
      baseUrl: "http://localhost:8000",
      apiKey: "test-key",
      agentId: "test-agent",
      targetUri: "viking://user/memories",
      recallLimit: 5,
      recallScoreThreshold: 0.7,
      autoRecall: true,
      autoCapture: true,
      captureMode: "semantic",
      captureMaxLength: 1000,
      timeoutMs: 30000,
      ...config,
    })),
  },
}));

vi.mock("./client.js", () => ({
  OpenVikingClient: vi.fn().mockImplementation(() => ({
    healthCheck: vi.fn().mockResolvedValue(undefined),
    find: vi.fn().mockResolvedValue({ memories: [] }),
    read: vi.fn().mockResolvedValue(""),
    addSessionMessage: vi.fn().mockResolvedValue(undefined),
    commitSession: vi.fn().mockResolvedValue({ archived: true, memories_extracted: 0 }),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    deleteUri: vi.fn().mockResolvedValue(undefined),
    getSession: vi.fn().mockResolvedValue({ message_count: 0 }),
  })),
  localClientCache: new Map(),
  localClientPendingPromises: new Map(),
  isMemoryUri: vi.fn((uri) => uri?.startsWith("viking://")),
}));

vi.mock("./process-manager.js", () => ({
  IS_WIN: false,
  waitForHealth: vi.fn().mockResolvedValue(undefined),
  quickRecallPrecheck: vi.fn().mockResolvedValue({ ok: true }),
  withTimeout: vi.fn((promise) => promise),
  resolvePythonCommand: vi.fn().mockReturnValue("python3"),
  prepareLocalPort: vi.fn().mockResolvedValue(8000),
}));

// Import the plugin after mocking
// We need to re-import to get a fresh instance with the guard reset
async function importPlugin() {
  const module = await import("./index.js");
  return module.default;
}

describe("Plugin Registration Guard (Issue #948)", () => {
  let mockApi: {
    pluginConfig: Record<string, unknown>;
    logger: {
      info: ReturnType<typeof vi.fn>;
      warn: ReturnType<typeof vi.fn>;
      error: ReturnType<typeof vi.fn>;
      debug?: ReturnType<typeof vi.fn>;
    };
    registerTool: ReturnType<typeof vi.fn>;
    registerService: ReturnType<typeof vi.fn>;
    registerContextEngine?: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    mockApi = {
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
  });

  it("should register the plugin on first call", async () => {
    const plugin = await importPlugin();
    plugin.register(mockApi);

    // Should have registered tools
    expect(mockApi.registerTool).toHaveBeenCalled();
    // Should have registered service
    expect(mockApi.registerService).toHaveBeenCalled();
    // Should NOT have logged the skip message
    expect(mockApi.logger.info).not.toHaveBeenCalledWith(
      expect.stringContaining("already registered")
    );
  });

  it("should skip duplicate registration on subsequent calls", async () => {
    const plugin = await importPlugin();
    
    // First registration
    plugin.register(mockApi);
    const firstCallCount = mockApi.registerTool.mock.calls.length;

    // Reset mock to track second call
    mockApi.registerTool.mockClear();
    mockApi.registerService.mockClear();

    // Second registration attempt (simulating the duplicate registration bug)
    plugin.register(mockApi);

    // Should have logged the skip message
    expect(mockApi.logger.info).toHaveBeenCalledWith(
      "openviking: plugin already registered, skipping duplicate registration"
    );

    // Should NOT have registered tools or service again
    expect(mockApi.registerTool).not.toHaveBeenCalled();
    expect(mockApi.registerService).not.toHaveBeenCalled();
  });

  it("should allow re-registration after stop is called", async () => {
    const plugin = await importPlugin();
    
    // First registration
    plugin.register(mockApi);

    // Get the registered service
    const serviceCall = mockApi.registerService.mock.calls[0];
    const service = serviceCall[0];

    // Reset mocks
    mockApi.registerTool.mockClear();
    mockApi.registerService.mockClear();

    // Call stop (this should reset the guard)
    service.stop();

    // Try to register again
    plugin.register(mockApi);

    // Should NOT have logged the skip message
    expect(mockApi.logger.info).not.toHaveBeenCalledWith(
      expect.stringContaining("already registered")
    );

    // Should have registered tools and service again
    expect(mockApi.registerTool).toHaveBeenCalled();
    expect(mockApi.registerService).toHaveBeenCalled();
  });
});
