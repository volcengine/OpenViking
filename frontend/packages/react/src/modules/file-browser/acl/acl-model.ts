import type {
  FileBrowserAclEntry as Entry,
  FileBrowserAclLevel as Level,
  FileBrowserAclReport as Report,
} from '../acl-types.js';

export const LEVELS: readonly Level[] = ['read', 'write', 'manage'];
const RANK = { read: 1, write: 2, manage: 3 };
export const isLevel = (value: unknown): value is Level =>
  LEVELS.includes(value as Level);

export function isAclTarget(uri: string | null): uri is string {
  if (!uri?.startsWith('viking://resources/')) return false;
  const segments = uri.slice('viking://resources/'.length).split('/');
  return segments.every((part) => part && part !== '.' && part !== '..');
}

export function normalizeEntries(entries: readonly Entry[]): Entry[] {
  const byPrincipal = new Map<string, Entry>();
  for (const entry of entries) {
    const principal = entry.principal.trim();
    if (
      !/^(user|group):[^\s:]+$/.test(principal) ||
      principal === 'group:*' ||
      !isLevel(entry.level)
    )
      throw new TypeError('Invalid ACL entry');
    const previous = byPrincipal.get(principal);
    if (!previous || RANK[entry.level] > RANK[previous.level])
      byPrincipal.set(principal, { principal, level: entry.level });
  }
  return [...byPrincipal.values()].sort((a, b) =>
    a.principal.localeCompare(b.principal),
  );
}

export function parseReport(value: unknown, uri: string): Report {
  const report = value as Report | undefined;
  if (
    !report ||
    report.uri !== uri ||
    !['none', 'inherit', 'restricted'].includes(report.acl_mode)
  )
    throw new TypeError('Invalid ACL report');
  for (const key of [
    'direct_entries',
    'inherited_entries',
    'effective_entries',
  ] as const) {
    if (!Array.isArray(report[key])) throw new TypeError('Invalid ACL entries');
  }
  return {
    uri,
    acl_mode: report.acl_mode,
    direct_entries: normalizeEntries(report.direct_entries),
    inherited_entries: normalizeEntries(report.inherited_entries),
    effective_entries: normalizeEntries(report.effective_entries),
  };
}

/** Increases preserve inheritance; reducing an inherited grant needs a restricted boundary. */
export function planChange(report: Report, next: Entry[]) {
  const parent = new Map(
    report.inherited_entries.map((entry) => [entry.principal, entry]),
  );
  const target = new Map(next.map((entry) => [entry.principal, entry]));
  const retained = [
    ...report.inherited_entries,
    ...report.effective_entries,
  ].every((entry) => {
    const value = target.get(entry.principal);
    return value && RANK[value.level] >= RANK[entry.level];
  });
  const entries = normalizeEntries(next);
  if (report.acl_mode === 'restricted')
    return { entries, mode: 'restricted' as const, confirmation: false };
  if (!retained)
    return { entries, mode: 'restricted' as const, confirmation: true };
  return {
    entries: entries.filter((entry) => {
      const inherited = parent.get(entry.principal);
      return !inherited || RANK[entry.level] > RANK[inherited.level];
    }),
    mode: 'inherit' as const,
    confirmation: false,
  };
}

export function restoreDiff(report: Report): Entry[] {
  const inherited = new Map(
    report.inherited_entries.map((entry) => [
      entry.principal,
      RANK[entry.level],
    ]),
  );
  return report.direct_entries.filter(
    (entry) =>
      Math.max(
        inherited.get(entry.principal) ?? 0,
        entry.principal.startsWith('user:')
          ? (inherited.get('user:*') ?? 0)
          : 0,
      ) < RANK[entry.level],
  );
}
