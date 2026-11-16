import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewFileTab } from '../../components/file-preview/index.js';
import { mimeTypeOf } from './fs-file-kind.js';

export function createPreviewImageResolver(
  client: OvClient,
  uri: string,
  directory: boolean,
  options: OvRequestOptions,
): NonNullable<FilePreviewFileTab['resolveImage']> {
  const base = directory ? `${uri.replace(/\/$/, '')}/` : uri;
  const pending = new Map<string, Promise<string>>();
  return async (src) => {
    if (/^(?:https?:|data:image\/|blob:|\/\/)/i.test(src))
      return Promise.resolve(src);
    const target = new URL(src, base);
    const path = decodeURIComponent(target.pathname);
    const type = mimeTypeOf(path);
    if (target.protocol !== 'viking:' || !type.startsWith('image/')) {
      throw new Error('Unsupported image source');
    }
    const imageUri = `viking://${target.host}${path}`;
    const existing = pending.get(imageUri);
    if (existing) return existing;
    const request = client
      .request<Blob>({
        method: 'GET',
        path: OV_PATHS.contentDownload,
        query: { uri: imageUri },
        responseType: 'blob',
        options,
      })
      .then(
        (blob) =>
          new Promise<string>((resolve, reject) => {
            // A data URL does not leave an object URL alive after the tab is closed.
            const reader = new FileReader();
            reader.onload = () => {
              if (typeof reader.result === 'string') resolve(reader.result);
              else reject(new Error('Invalid image data'));
            };
            reader.onerror = () =>
              reject(
                new Error(reader.error?.message ?? 'Could not read image'),
              );
            reader.readAsDataURL(new Blob([blob], { type }));
          }),
      )
      .catch((error: unknown) => {
        pending.delete(imageUri);
        throw error;
      });
    pending.set(imageUri, request);
    return request;
  };
}
