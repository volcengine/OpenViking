export const PERMISSIONS = [
  { value: 'read', zh: '可阅读', en: 'Read', keywords: 'reader document' },
  { value: 'write', zh: '可编辑', en: 'Edit', keywords: 'editor document' },
  { value: 'manage', zh: '可管理', en: 'Manage', keywords: 'manager admin' },
  { value: 'audit', zh: '可审计', en: 'Audit', keywords: 'auditor review' },
  {
    value: 'archive',
    zh: '这是一条需要省略显示的很长的自定义权限名称',
    en: 'A long custom permission name that needs truncation',
    keywords: 'archive history',
  },
];

/** Simulated server: aliases may match even when the displayed label does not. */
export function searchPermissions(keyword: string, signal: AbortSignal) {
  return new Promise<typeof PERMISSIONS>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      const query = keyword.trim().toLocaleLowerCase();
      if (query === 'error') reject(new Error('Simulated request failure'));
      else
        resolve(
          PERMISSIONS.filter((item) =>
            `${item.zh} ${item.en} ${item.keywords}`
              .toLocaleLowerCase()
              .includes(query),
          ),
        );
    }, 600);
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
}
