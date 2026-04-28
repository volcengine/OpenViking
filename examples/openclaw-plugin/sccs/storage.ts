import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve, relative } from "node:path";

export type StoredValue = { content: string; expiresAt: number };
export interface RefStore {
  get(refId: string): Promise<string | null>;
  set(refId: string, content: string, ttlSeconds: number): Promise<void>;
}

export class MemoryStore implements RefStore {
  private entries = new Map<string, StoredValue>();
  private maxEntries?: number;
  constructor(maxEntries?: number) {
    this.maxEntries = maxEntries && maxEntries > 0 ? maxEntries : undefined;
  }
  async get(refId: string): Promise<string | null> {
    const entry = this.entries.get(refId);
    if (!entry) return null;
    if (entry.expiresAt <= Date.now()) {
      this.entries.delete(refId);
      return null;
    }
    return entry.content;
  }
  async set(refId: string, content: string, ttlSeconds: number): Promise<void> {
    const expiresAt = Date.now() + Math.max(1, ttlSeconds) * 1000;
    this.entries.set(refId, { content, expiresAt });
    if (this.maxEntries && this.entries.size > this.maxEntries) {
      const firstKey = this.entries.keys().next().value as string | undefined;
      if (firstKey) this.entries.delete(firstKey);
    }
  }
}

export class DiskBackedStore implements RefStore {
  private memory: MemoryStore;
  private dir: string;
  constructor(params: { dir: string; maxEntries?: number }) {
    this.memory = new MemoryStore(params.maxEntries);
    this.dir = params.dir;
  }
  /**
   * Build a safe file path for a refId.
   * Resolves against the refs directory and verifies the result stays inside it,
   * preventing path traversal even if a non-hash refId slips through.
   */
  private pathFor(refId: string): string {
    const refsDir = resolve(this.dir, "refs");
    const target = resolve(refsDir, `${refId}.json`);
    const rel = relative(refsDir, target);
    if (rel.startsWith("..") || resolve(refsDir, rel) !== target) {
      throw new Error(`[sccs] path traversal detected for refId: ${refId}`);
    }
    return target;
  }
  async get(refId: string): Promise<string | null> {
    const cached = await this.memory.get(refId);
    if (cached !== null) return cached;
    const path = this.pathFor(refId);
    try {
      const raw = await readFile(path, "utf8");
      const parsed = JSON.parse(raw) as StoredValue;
      if (!parsed || typeof parsed.content !== "string") return null;
      if (parsed.expiresAt <= Date.now()) {
        await rm(path, { force: true });
        return null;
      }
      await this.memory.set(refId, parsed.content, Math.ceil((parsed.expiresAt - Date.now()) / 1000));
      return parsed.content;
    } catch {
      return null;
    }
  }
  async set(refId: string, content: string, ttlSeconds: number): Promise<void> {
    await this.memory.set(refId, content, ttlSeconds);
    const expiresAt = Date.now() + Math.max(1, ttlSeconds) * 1000;
    const path = this.pathFor(refId);
    try {
      await mkdir(join(this.dir, "refs"), { recursive: true });
      await writeFile(path, JSON.stringify({ content, expiresAt }), "utf8");
    } catch (err) {
      // Disk persistence is best-effort, but log the failure for observability.
      // Data remains available in memory until evicted or process exits.
      console.warn(`[sccs] disk write failed for refId ${refId}:`, err);
    }
  }
}
