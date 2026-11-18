import { parseArguments, tokenize } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';
import { resolveCommandSpec } from './commands.js';
import type { ConsoleHistoryEntry } from './types.js';

const legacyPrefix = 'openviking.console.command-history.v1:';
const prefix = 'openviking.console.command-history.v2:';
const limit = 50;

// Review new commands before persisting their arguments. Write commands and
// invalid submissions can carry full bodies, instructions or credentials.
const persistable = new Set<CommandSpec['name']>([
  'help',
  'health',
  'status',
  'ls',
  'tree',
  'stat',
  'read',
  'find',
  'search',
  'grep',
  'glob',
  'session list',
  'session get',
  'session get-session-context',
  'session get-session-archive',
  'task list',
  'task status',
]);

type StoredEntry = Pick<ConsoleHistoryEntry, 'command' | 'createdAtMs'>;

function isPersistableCommand(command: string) {
  try {
    const tokens = tokenize(command);
    const spec = resolveCommandSpec(tokens);
    if (!spec || !persistable.has(spec.name)) return false;
    parseArguments(
      tokens.slice(spec.name.split(' ').length),
      spec.positional ?? [],
      spec.flags,
    );
    return true;
  } catch {
    return false;
  }
}

function isStoredEntry(value: unknown): value is StoredEntry {
  if (!value || typeof value !== 'object') return false;
  const { command, createdAtMs } = value as Partial<StoredEntry>;
  return (
    typeof command === 'string' &&
    typeof createdAtMs === 'number' &&
    Number.isFinite(new Date(createdAtMs).getTime()) &&
    isPersistableCommand(command)
  );
}

function toStoredEntries(history: readonly unknown[]): StoredEntry[] {
  const entries: StoredEntry[] = [];
  for (
    let index = history.length - 1;
    index >= 0 && entries.length < limit;
    index--
  ) {
    const item = history[index];
    if (isStoredEntry(item))
      entries.push({ command: item.command, createdAtMs: item.createdAtMs });
  }
  return entries.reverse();
}

function parseStoredEntries(raw: string | null): StoredEntry[] {
  try {
    const entries: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(entries) ? toStoredEntries(entries) : [];
  } catch {
    return [];
  }
}

function saveEntries(storage: Storage, key: string, entries: StoredEntry[]) {
  if (entries.length) storage.setItem(key, JSON.stringify(entries));
  else storage.removeItem(key);
}

function migrateHistory(storage: Storage) {
  const oldKeys = Array.from({ length: storage.length }, (_, index) =>
    storage.key(index),
  ).filter(
    (key): key is string => key !== null && key.startsWith(legacyPrefix),
  );
  for (const oldKey of oldKeys) {
    const raw = storage.getItem(oldKey);
    storage.removeItem(oldKey);
    const key = prefix + oldKey.slice(legacyPrefix.length);
    if (storage.getItem(key) === null) {
      try {
        saveEntries(storage, key, parseStoredEntries(raw));
      } catch {
        // Legacy data has already been removed, even if the new entry exceeds quota.
      }
    }
  }
}

export function readCommandHistory(key: string): ConsoleHistoryEntry[] {
  try {
    const storage = window.sessionStorage;
    migrateHistory(storage);
    return parseStoredEntries(storage.getItem(prefix + key)).map(
      ({ command, createdAtMs }, index) => ({
        id: `restored-command-${index}`,
        command,
        createdAtMs,
      }),
    );
  } catch {
    return [];
  }
}

export function writeCommandHistory(
  key: string,
  history: readonly ConsoleHistoryEntry[],
) {
  try {
    saveEntries(window.sessionStorage, prefix + key, toStoredEntries(history));
  } catch {
    // Terminal history remains available in memory when storage is unavailable.
  }
}
