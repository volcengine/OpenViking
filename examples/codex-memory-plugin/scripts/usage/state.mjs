import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, rename, rm, stat, utimes, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-state");
const MAX_SESSIONS = 20;
const MAX_TURNS = 50;

export function rootDir() {
  const base = process.env.OPENVIKING_CODEX_STATE_DIR || DEFAULT_STATE_DIR;
  return join(base, "ov-usage");
}

export function safeId(value) {
  return String(value || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function sessionDir(sessionId) {
  return join(rootDir(), safeId(sessionId));
}

export function turnDir(sessionId, turnId) {
  return join(sessionDir(sessionId), `turn-${safeId(turnId)}`);
}

async function writeJsonAtomic(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  await rename(temporary, path);
  const now = new Date();
  await Promise.all([dirname(path), dirname(dirname(path))]
    .map((dir) => utimes(dir, now, now)));
}

export async function readJson(path, fallback = null) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return fallback;
  }
}

export async function writeRecall(sessionId, turnId, recalled) {
  await writeJsonAtomic(join(turnDir(sessionId, turnId), "recall.json"), { recalled });
}

export async function writeLookup(sessionId, turnId, toolUseId, lookup) {
  await writeJsonAtomic(
    join(turnDir(sessionId, turnId), `lookup-${safeId(toolUseId)}.json`),
    lookup,
  );
}

export async function readTurn(sessionId, turnId) {
  const dir = turnDir(sessionId, turnId);
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return { recalled: [], lookups: [] };
  }
  const recall = await readJson(join(dir, "recall.json"), { recalled: [] });
  const lookupFiles = files.filter((file) => file.startsWith("lookup-") && file.endsWith(".json"));
  const lookups = (await Promise.all(lookupFiles.sort().map((file) => readJson(join(dir, file)))))
    .filter(Boolean);
  return {
    recalled: Array.isArray(recall?.recalled) ? recall.recalled : [],
    lookups,
  };
}

async function directoriesByMtime(path, filter = () => true) {
  let entries;
  try {
    entries = await readdir(path, { withFileTypes: true });
  } catch {
    return [];
  }
  const dirs = entries.filter((entry) => entry.isDirectory() && filter(entry.name));
  const stamped = await Promise.all(dirs.map(async (entry) => ({
    path: join(path, entry.name),
    mtime: (await stat(join(path, entry.name))).mtimeMs,
  })));
  return stamped.sort((a, b) => b.mtime - a.mtime);
}

export async function pruneTurns(sessionId, turnId) {
  const dirs = await directoriesByMtime(sessionDir(sessionId), (name) => name.startsWith("turn-"));
  const others = dirs.filter((entry) => entry.path !== turnDir(sessionId, turnId));
  await Promise.all(others.slice(MAX_TURNS - 1).map((entry) => rm(entry.path, { recursive: true, force: true })));
}

export async function pruneSessions(sessionId) {
  const dirs = await directoriesByMtime(rootDir());
  const others = dirs.filter((entry) => entry.path !== sessionDir(sessionId));
  await Promise.all(others.slice(MAX_SESSIONS - 1).map((entry) => rm(entry.path, { recursive: true, force: true })));
}
