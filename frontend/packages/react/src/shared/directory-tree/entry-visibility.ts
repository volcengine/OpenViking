import type { DirState } from './types.js';

/** Presentation keeps raw pages intact for pagination and permission checks. */
export function directoryView(
  dirs: Record<string, DirState>,
  hideDenied = false,
) {
  return Object.fromEntries(
    Object.entries(dirs).map(([uri, dir]) => [
      uri,
      hideDenied
        ? {
            ...dir,
            entries: dir.entries.filter((entry) => entry.access !== 'denied'),
          }
        : dir,
    ]),
  );
}
