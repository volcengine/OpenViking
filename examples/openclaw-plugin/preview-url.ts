import type { FindResult, FindResultItem } from "./client.js";

const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".bmp",
  ".svg",
  ".avif",
]);

export type PreviewUrlClient = {
  getPreviewUrl?: (uri: string, actorPeerId?: string) => Promise<string>;
};

export function stripUriFragment(uri: string): string {
  const hashIndex = uri.indexOf("#");
  return hashIndex >= 0 ? uri.slice(0, hashIndex) : uri;
}

export function isPreviewableImageResourceUri(uri: string): boolean {
  const normalized = stripUriFragment(uri).toLowerCase();
  if (!normalized.startsWith("viking://resources/")) {
    return false;
  }
  return [...IMAGE_EXTENSIONS].some((ext) => normalized.endsWith(ext));
}

export async function withPreviewUrls(
  items: FindResultItem[],
  client: PreviewUrlClient,
  actorPeerId?: string,
): Promise<FindResultItem[]> {
  const getPreviewUrl = client.getPreviewUrl;
  if (typeof getPreviewUrl !== "function") {
    return items;
  }

  const enriched = await Promise.all(items.map(async (item) => {
    if (item.preview_url || !isPreviewableImageResourceUri(item.uri)) {
      return item;
    }
    try {
      const previewUrl = await getPreviewUrl(stripUriFragment(item.uri), actorPeerId);
      return previewUrl ? { ...item, preview_url: previewUrl } : item;
    } catch {
      return item;
    }
  }));

  return enriched;
}

export async function withFindResultPreviewUrls(
  result: FindResult,
  client: PreviewUrlClient,
  actorPeerId?: string,
): Promise<FindResult> {
  const resources = result.resources
    ? await withPreviewUrls(result.resources, client, actorPeerId)
    : result.resources;
  return {
    ...result,
    resources,
  };
}
