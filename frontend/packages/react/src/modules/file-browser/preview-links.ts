import type { FilePreviewDirectoryTab } from '../../components/file-preview/index.js';

/** Resolve summary resource links against their directory. */
export async function openPreviewLink(
  tab: FilePreviewDirectoryTab,
  target: string,
  openResource: (uri: string) => Promise<boolean>,
): Promise<void> {
  if (!target.trim()) return;
  const base = `${tab.key.replace(/\/$/, '')}/`;
  const url = new URL(target, base);
  if (url.protocol === 'viking:') {
    await openResource(
      `viking://${url.host}${decodeURIComponent(url.pathname)}`,
    );
  }
}
