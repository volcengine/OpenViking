import type { ConsoleHistoryEntry } from './types.js';

const prefix = 'openviking.console.command-history.v1:';
const limit = 50;
const maxCommandLength = 4096;

type StoredEntry = Pick<ConsoleHistoryEntry, 'command' | 'createdAtMs'>;

function isStoredEntry(value: unknown): value is StoredEntry {
  if (!value || typeof value !== 'object') return false;
  const { command, createdAtMs } = value as Partial<StoredEntry>;
  return (
    typeof command === 'string' &&
    command.length <= maxCommandLength &&
    command.trim().length > 0 &&
    typeof createdAtMs === 'number' &&
    Number.isFinite(new Date(createdAtMs).getTime())
  );
}

export function readCommandHistory(key: string): ConsoleHistoryEntry[] {
  try {
    const raw = window.sessionStorage.getItem(prefix + key);
    const entries: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(entries)) return [];
    return entries
      .slice(-limit)
      .filter(isStoredEntry)
      .map(({ command, createdAtMs }, index) => ({
        id: `restored-command-${index}`,
        command,
        createdAtMs,
      }));
  } catch {
    return [];
  }
}

export function writeCommandHistory(
  key: string,
  history: readonly ConsoleHistoryEntry[],
) {
  try {
    const storage = window.sessionStorage;
    const storageKey = prefix + key;
    if (!history.length) {
      storage.removeItem(storageKey);
      return;
    }
    const entries = history
      .slice(-limit)
      .filter(isStoredEntry)
      .map(({ command, createdAtMs }) => ({ command, createdAtMs }));
    storage.setItem(storageKey, JSON.stringify(entries));
  } catch {
    // Terminal history remains available in memory when storage is unavailable.
  }
}
