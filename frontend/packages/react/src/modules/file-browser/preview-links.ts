import type { FilePreviewDirectoryTab } from '../../components/file-preview/index.js';

/** Resolve summary resource links against their directory. */
export async function openPreviewLink(
  tab: FilePreviewDirectoryTab,
  target: string,
  openResource: (uri: string) => Promise<boolean>,
): Promise<void> {
  if (!target.trim()) return;
  const pathStart = tab.key.indexOf('/', 'viking://'.length);
  const origin = pathStart < 0 ? tab.key : tab.key.slice(0, pathStart);
  const path = pathStart < 0 ? '' : tab.key.slice(pathStart);
  // Storage paths are literal: escape each segment before URL parsing so `#`,
  // `?` and `%` in directory names cannot become URL syntax or escape sequences.
  const encodedPath = path.split('/').map(encodeURIComponent).join('/');
  const base = `${origin}${encodedPath.replace(/\/$/, '')}/`;
  const url = new URL(target, base);
  if (url.protocol === 'viking:') {
    await openResource(
      `viking://${url.host}${decodeURIComponent(url.pathname)}`,
    );
  }
}
