import { describe, expect, it, vi } from "vitest";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { mkdir, rm, readFile, chmod } from "node:fs/promises";

import { normalizeRefId, md5Hex } from "../../sccs/utils.js";
import { DiskBackedStore, MemoryStore } from "../../sccs/storage.js";

// ---------------------------------------------------------------------------
// normalizeRefId — strict MD5 hash validation
// ---------------------------------------------------------------------------

describe("normalizeRefId", () => {
  it("extracts valid hash from [REF_ID: <hash>] format", () => {
    const hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4";
    expect(normalizeRefId(`[REF_ID: ${hash}]`)).toBe(hash);
  });

  it("accepts raw 32-char hex string", () => {
    const hash = "abcdef0123456789abcdef0123456789";
    expect(normalizeRefId(hash)).toBe(hash);
  });

  it("accepts uppercase hex and returns lowercase", () => {
    const upper = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4";
    const lower = upper.toLowerCase();
    expect(normalizeRefId(upper)).toBe(lower);
  });

  it("trims whitespace before validating", () => {
    const hash = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4";
    expect(normalizeRefId(`  ${hash}  `)).toBe(hash);
  });

  it("returns null for path traversal attempt with ../", () => {
    expect(normalizeRefId("../../etc/passwd")).toBeNull();
  });

  it("returns null for path traversal attempt with ..\\", () => {
    expect(normalizeRefId("..\\..\\windows\\system32")).toBeNull();
  });

  it("returns null for short hash (16 chars)", () => {
    expect(normalizeRefId("a1b2c3d4e5f6a1b2")).toBeNull();
  });

  it("returns null for hash with non-hex characters", () => {
    expect(normalizeRefId("g1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(normalizeRefId("")).toBeNull();
  });

  it("returns null for whitespace-only string", () => {
    expect(normalizeRefId("   ")).toBeNull();
  });

  it("returns null for string with special characters", () => {
    expect(normalizeRefId("../../../outside")).toBeNull();
  });

  it("returns null for [REF_ID: ...] with invalid inner hash", () => {
    expect(normalizeRefId("[REF_ID: not-a-hash]")).toBeNull();
  });

  it("returns null for hash with spaces", () => {
    expect(normalizeRefId("a1b2c3d4 e5f6a1b2 c3d4e5f6 a1b2c3d4")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// DiskBackedStore.pathFor — path traversal defense (via set/get)
// ---------------------------------------------------------------------------

describe("DiskBackedStore path traversal prevention", () => {
  let testDir: string;

  // Create a temp dir for each test
  async function createTestDir(): Promise<string> {
    const dir = join(tmpdir(), `sccs-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    await mkdir(dir, { recursive: true });
    return dir;
  }

  it("stores and retrieves a valid refId normally", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      const refId = md5Hex("normal content");
      await store.set(refId, "normal content", 3600);
      const result = await store.get(refId);
      expect(result).toBe("normal content");
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("rejects path traversal refId with ../ in set()", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      await expect(store.set("../../outside", "malicious", 3600)).rejects.toThrow(
        "path traversal detected",
      );
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("rejects path traversal refId with ../ in get()", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      await expect(store.get("../../outside")).rejects.toThrow("path traversal detected");
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("rejects path traversal with absolute path", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      await expect(store.set("/etc/passwd", "malicious", 3600)).rejects.toThrow(
        "path traversal detected",
      );
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("rejects path traversal with ..\\ on any platform", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      await expect(store.set("..\\..\\windows\\system32", "malicious", 3600)).rejects.toThrow(
        "path traversal detected",
      );
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("writes file inside refs/ directory for valid refId", async () => {
    testDir = await createTestDir();
    try {
      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      const refId = md5Hex("file location test");
      await store.set(refId, "test content", 3600);
      // set() now awaits disk write — file should be immediately readable
      const filePath = join(testDir, "refs", `${refId}.json`);
      const raw = await readFile(filePath, "utf8");
      const parsed = JSON.parse(raw);
      expect(parsed.content).toBe("test content");
      expect(typeof parsed.expiresAt).toBe("number");
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("persists data to disk so new store instance can read it", async () => {
    testDir = await createTestDir();
    try {
      const refId = md5Hex("persistence test");
      // Write with one store instance
      const store1 = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      await store1.set(refId, "persisted content", 3600);
      // Read with a fresh store instance (empty memory)
      const store2 = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      const result = await store2.get(refId);
      expect(result).toBe("persisted content");
    } finally {
      await rm(testDir, { recursive: true, force: true });
    }
  });

  it("logs warning when disk write fails", async () => {
    testDir = await createTestDir();
    try {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      // Create a read-only refs dir to cause write failure
      const refsDir = join(testDir, "refs");
      await mkdir(refsDir, { recursive: true });
      await chmod(refsDir, 0o444);

      const store = new DiskBackedStore({ dir: testDir, maxEntries: 100 });
      const refId = md5Hex("write fail test");
      // set() should not throw, but log a warning
      await store.set(refId, "should fail on disk", 3600);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining(`[sccs] disk write failed for refId ${refId}`),
        expect.anything(),
      );
      // Data should still be in memory
      expect(await store.get(refId)).toBe("should fail on disk");
      warnSpy.mockRestore();
    } finally {
      // Restore write permission before cleanup
      const refsDir = join(testDir, "refs");
      await chmod(refsDir, 0o755).catch(() => {});
      await rm(testDir, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// MemoryStore — basic operations (unchanged, smoke test)
// ---------------------------------------------------------------------------

describe("MemoryStore", () => {
  it("stores and retrieves a value", async () => {
    const store = new MemoryStore(100);
    await store.set("abc123", "hello", 3600);
    expect(await store.get("abc123")).toBe("hello");
  });

  it("returns null for missing key", async () => {
    const store = new MemoryStore(100);
    expect(await store.get("nonexistent")).toBeNull();
  });

  it("evicts oldest entry when maxEntries exceeded", async () => {
    const store = new MemoryStore(2);
    await store.set("first", "a", 3600);
    await store.set("second", "b", 3600);
    await store.set("third", "c", 3600);
    // "first" should be evicted
    expect(await store.get("first")).toBeNull();
    expect(await store.get("second")).toBe("b");
    expect(await store.get("third")).toBe("c");
  });
});
