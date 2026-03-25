import { describe, it, expect, vi, beforeAll } from "vitest";

// Import the actual plugin module to test real behavior
// Vitest handles TypeScript files directly
// Note: These tests run sequentially since they share module-level state
import contextEnginePlugin from "../index.js";

describe("Plugin Registration Guard (Issue #948)", () => {
  // Track mock APIs across tests since plugin state is module-level
  const mockApis: Array<{
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
  }> = [];

  function createMockApi() {
    const mockApi = {
      pluginConfig: {
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
      },
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
    mockApis.push(mockApi);
    return mockApi;
  }

  it("should verify the plugin has the expected structure", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    
    // Verify plugin structure
    expect(plugin.id).toBe("openviking");
    expect(plugin.name).toBe("Context Engine (OpenViking)");
    expect(plugin.kind).toBe("context-engine");
    expect(typeof plugin.register).toBe("function");
    expect(plugin.configSchema).toBeDefined();
  });

  it("should register the plugin on first call", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    const mockApi = createMockApi();
    
    plugin.register(mockApi);

    // Should have registered tools (memory_recall, memory_store, memory_forget)
    expect(mockApi.registerTool).toHaveBeenCalledTimes(3);
    // Should have registered service
    expect(mockApi.registerService).toHaveBeenCalledTimes(1);
    // Should NOT have logged the skip message
    expect(mockApi.logger.info).not.toHaveBeenCalledWith(
      expect.stringContaining("already registered")
    );
  });

  it("should skip duplicate registration on subsequent calls", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    const mockApi = createMockApi();
    
    // At this point, the plugin is already registered from the previous test
    // This call should be skipped
    plugin.register(mockApi);

    // Should have logged the skip message
    expect(mockApi.logger.info).toHaveBeenCalledWith(
      "openviking: plugin already registered, skipping duplicate registration"
    );

    // Should NOT have registered tools or service
    expect(mockApi.registerTool).not.toHaveBeenCalled();
    expect(mockApi.registerService).not.toHaveBeenCalled();
  });

  it("should handle multiple duplicate registration attempts", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    const mockApi = createMockApi();
    
    // Multiple registration attempts should all be skipped
    for (let i = 0; i < 3; i++) {
      plugin.register(mockApi);
    }

    // Should NOT have registered any tools
    expect(mockApi.registerTool).not.toHaveBeenCalled();
    expect(mockApi.registerService).not.toHaveBeenCalled();
    
    // Should have logged the skip message 3 times
    const skipMessages = mockApi.logger.info.mock.calls.filter(
      call => call[0] === "openviking: plugin already registered, skipping duplicate registration"
    );
    expect(skipMessages.length).toBe(3);
  });

  it("should allow re-registration after stop is called", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    
    // Use the mockApi from "should register the plugin on first call" test
    // It's at index 1 (index 0 is the structure test which doesn't create a mockApi)
    const registeredMockApi = mockApis[0]; // First mockApi with actual registration
    
    // Get the registered service
    expect(registeredMockApi.registerService).toHaveBeenCalledTimes(1);
    const serviceCall = registeredMockApi.registerService.mock.calls[0];
    expect(serviceCall).toBeDefined();
    expect(serviceCall[0]).toBeDefined();
    const service = serviceCall[0];
    expect(service.stop).toBeDefined();

    // Call stop (this should reset the guard)
    service.stop();

    // Verify the stop logged appropriately
    expect(registeredMockApi.logger.info).toHaveBeenCalledWith(
      expect.stringContaining("stopped")
    );

    // Now try to register with a fresh mockApi
    const freshMockApi = createMockApi();
    plugin.register(freshMockApi);

    // Should NOT have logged the skip message about already registered
    expect(freshMockApi.logger.info).not.toHaveBeenCalledWith(
      "openviking: plugin already registered, skipping duplicate registration"
    );

    // Should have registered tools and service again
    expect(freshMockApi.registerTool).toHaveBeenCalledTimes(3);
    expect(freshMockApi.registerService).toHaveBeenCalledTimes(1);
  });

  it("should skip registration again after re-registration", async () => {
    const module = await import("../index.js");
    const plugin = module.default;
    const mockApi = createMockApi();
    
    // Plugin was re-registered in the previous test
    // This should be skipped
    plugin.register(mockApi);

    expect(mockApi.logger.info).toHaveBeenCalledWith(
      "openviking: plugin already registered, skipping duplicate registration"
    );
    expect(mockApi.registerTool).not.toHaveBeenCalled();
  });
});