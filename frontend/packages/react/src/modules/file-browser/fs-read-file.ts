import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewFileTab } from '../../components/file-preview/index.js';
import { isBinaryFile, mimeTypeOf, toBinaryTab } from './fs-file-kind.js';
import { fileTypeIcon } from './fs-icon.js';
import { readFileContent } from './fs-read-content.js';
import { createPreviewImageResolver } from './fs-preview-images.js';
import { createFileEditing } from './fs-file-editing.js';
import { nameFromUri, toFileTab } from './fs-model.js';

/** 读取正文的行数上限；读满即按截断处理。The line cap; a full read counts as truncated. */
export const MAX_LINES = 2000;

/**
 * 读文件并包装成页签。文本走 `content/read`，二进制走 `content/download` 取原始
 * 字节——前者把内容当文本返回，二进制文件经过它只会得到一串乱码，图片与 PDF
 * 因此永远渲染不出来。
 *
 * Reads a file into a tab. Text goes through `content/read` and binary files
 * through `content/download` for the raw bytes: the former returns content as
 * text, so a binary file only yields mojibake and images or PDFs never render.
 */
export async function readFile(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
  openParent?: (uri: string) => Promise<void>,
  onSave?: () => void,
  isWiki = false,
): Promise<FilePreviewFileTab> {
  const name = nameFromUri(uri);
  const icon = fileTypeIcon(name);

  if (isBinaryFile(name)) {
    const blob = await client.request<Blob>({
      method: 'GET',
      path: OV_PATHS.contentDownload,
      query: { uri },
      responseType: 'blob',
      options,
    });
    // 接口返回 octet-stream，这里按扩展名补上真实类型：底座靠它选择 Adapter。
    // The endpoint answers octet-stream; the real type is restored from the
    // extension, which is what the base uses to pick an adapter.
    const typed = new Blob([blob], { type: mimeTypeOf(name) });
    return toBinaryTab(uri, name, typed, icon, openParent);
  }

  const { content, truncated, wiki } = await readFileContent(
    client,
    uri,
    options,
    MAX_LINES,
    isWiki,
  );
  return {
    ...toFileTab({ uri }, content, MAX_LINES, icon, openParent, truncated),
    resolveImage: createPreviewImageResolver(client, uri, false, options),
    editing: createFileEditing(client, uri, name, options, onSave, wiki),
  };
}
