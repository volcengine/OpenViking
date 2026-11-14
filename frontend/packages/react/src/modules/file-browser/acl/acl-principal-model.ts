import type { AclText } from './acl-labels.js';

export function filterCandidates(
  type: 'user' | 'group',
  keyword: string,
  items: string[],
  existing: ReadonlySet<string>,
  text: AclText,
) {
  const normalized = keyword.trim().toLocaleLowerCase();
  const values = new Set([...(type === 'user' ? ['user:*'] : []), ...items]);
  return [...values].filter((principal) => {
    if (existing.has(principal) || !principal.startsWith(`${type}:`))
      return false;
    const name =
      principal === 'user:*'
        ? text.allUsers
        : principal.slice(principal.indexOf(':') + 1);
    return name.toLocaleLowerCase().includes(normalized);
  });
}
