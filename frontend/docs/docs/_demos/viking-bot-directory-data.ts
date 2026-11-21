export const INITIAL = [
  { uri: 'viking://resources', name: 'resources', parent: '' },
  {
    uri: 'viking://resources/compiled',
    name: 'compiled',
    parent: 'viking://resources',
  },
  {
    uri: 'viking://resources/wiki',
    name: 'wiki',
    parent: 'viking://resources',
  },
  {
    uri: 'viking://resources/wiki/archive',
    name: 'archive',
    parent: 'viking://resources/wiki',
  },
  {
    uri: 'viking://resources/read-only',
    name: 'read-only',
    parent: 'viking://resources',
  },
  { uri: 'viking://user/resources', name: 'user/resources', parent: '' },
];
export const pause = (signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, 250);
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, { once: true });
  });
