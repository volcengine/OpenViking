import type { ConsoleHistoryEntry } from './types.js';

const prefix = 'openviking.console.command-history.v3:';
const previousPrefixes = [
  'openviking.console.command-history.v2:',
  'openviking.console.command-history.v1:',
];

type StoredEntry = Pick<ConsoleHistoryEntry, 'command' | 'createdAtMs'>;

function isStoredEntry(value: unknown): value is StoredEntry {
  if (!value || typeof value !== 'object') return false;
  const { command, createdAtMs } = value as Partial<StoredEntry>;
  return (
    typeof command === 'string' &&
    typeof createdAtMs === 'number' &&
    Number.isFinite(new Date(createdAtMs).getTime())
  );
}

function parseEntries(raw: string | null): StoredEntry[] {
  try {
    const entries: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(entries) ? entries.filter(isStoredEntry) : [];
  } catch {
    return [];
  }
}

function toHistory(entries: StoredEntry[]): ConsoleHistoryEntry[] {
  return entries.map(({ command, createdAtMs }, index) => ({
    id: `stored-command-${index}`,
    command,
    createdAtMs,
  }));
}

function saveEntries(storage: Storage, key: string, entries: StoredEntry[]) {
  for (let first = 0; first < entries.length; first++) {
    const recent = entries.slice(first);
    try {
      storage.setItem(key, JSON.stringify(recent));
      return recent;
    } catch (error) {
      if (
        !(error instanceof DOMException) ||
        error.name !== 'QuotaExceededError'
      )
        break;
    }
  }
  return undefined;
}

function removePreviousHistory(key: string) {
  try {
    for (const previousPrefix of previousPrefixes)
      window.sessionStorage.removeItem(previousPrefix + key);
  } catch {
    // Session storage can be unavailable even when local history is readable.
  }
}

function readEntries(storage: Storage, key: string): StoredEntry[] {
  const storageKey = prefix + key;
  const raw = storage.getItem(storageKey);
  if (raw !== null) return parseEntries(raw);

  try {
    for (const previousPrefix of previousPrefixes) {
      const previous = window.sessionStorage.getItem(previousPrefix + key);
      if (previous === null) continue;
      const entries = parseEntries(previous);
      if (!entries.length) return [];
      const migrated = saveEntries(storage, storageKey, entries);
      if (migrated) removePreviousHistory(key);
      return migrated ?? [];
    }
  } catch {
    // A previous session may be inaccessible.
  }
  return [];
}

export function readCommandHistory(key: string): ConsoleHistoryEntry[] {
  try {
    return toHistory(readEntries(window.localStorage, key));
  } catch {
    return [];
  }
}

export function appendCommandHistory(
  key: string,
  { command, createdAtMs }: StoredEntry,
): ConsoleHistoryEntry[] | undefined {
  try {
    const storage = window.localStorage;
    const entries = readEntries(storage, key);
    const saved = saveEntries(storage, prefix + key, [
      ...entries,
      { command, createdAtMs },
    ]);
    return toHistory(saved ?? entries);
  } catch {
    return undefined;
  }
}

export function clearCommandHistory(key: string): ConsoleHistoryEntry[] {
  removePreviousHistory(key);
  try {
    window.localStorage.removeItem(prefix + key);
    return [];
  } catch {
    return readCommandHistory(key);
  }
}
