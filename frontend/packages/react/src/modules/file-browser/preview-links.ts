import type { FilePreviewDirectoryTab } from '../../components/file-preview/index.js';

/** Resolve summary resource links against their directory. */
export async function openPreviewLink(
  tab: FilePreviewDirectoryTab,
  target: string,
  openResource: (uri: string) => Promise<boolean>,
): Promise<void> {
  if (!target.trim()) return;
  const base = `${tab.key.replace(/\/$/, '')}/`;
  const url = new URL(
    target,
    target.startsWith('//') ? window.location.href : base,
  );
  if (url.protocol === 'viking:' && !url.hash && !url.search) {
    await openResource(
      `viking://${url.host}${decodeURIComponent(url.pathname)}`,
    );
  } else if (
    ['viking:', 'http:', 'https:', 'mailto:', 'tel:'].includes(url.protocol)
  ) {
    // Resource navigation has no query/fragment contract. Preserve native opening
    // for those targets, including browser-relative URLs.
    window.open(
      url.protocol === 'viking:'
        ? new URL(target, window.location.href).href
        : url.href,
      '_blank',
      'noopener,noreferrer',
    );
  }
}
