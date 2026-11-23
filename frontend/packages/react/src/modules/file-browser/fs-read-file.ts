import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewFileTab } from '../../components/file-preview/index.js';
import { isBinaryFile, mimeTypeOf, toBinaryTab } from './fs-file-kind.js';
import { fileTypeIcon } from './fs-icon.js';
import { readFileContent } from './fs-read-content.js';
import { createPreviewImageResolver } from './fs-preview-images.js';
import { createFileEditing, type WikiSaveStatus } from './fs-file-editing.js';
import { nameFromUri } from '../../shared/directory-tree/rows.js';
import { toFileTab } from './fs-model.js';
import type { ReadDashboardHint } from './dashboard-resource.js';
import type { CachedPreviewBody } from './preview-body-cache.js';

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
  onWikiSaveStatus?: (status?: WikiSaveStatus) => void,
  readHint?: ReadDashboardHint,
  cache?: CachedPreviewBody,
): Promise<{
  tab: FilePreviewFileTab & { compileOutput?: boolean };
  resolveWikiTab?: () => Promise<
    (FilePreviewFileTab & { compileOutput?: boolean }) | undefined
  >;
}> {
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
    return { tab: toBinaryTab(uri, name, typed, icon, openParent) };
  }

  const result = await readFileContent(
    client,
    uri,
    options,
    MAX_LINES,
    false,
    readHint,
    true,
    cache,
  );
  const resolveImage = createPreviewImageResolver(client, uri, false, options);
  const toTab = ({
    content,
    truncated,
    wiki,
    compileOutput,
  }: typeof result) => ({
    ...toFileTab({ uri }, content, MAX_LINES, icon, openParent, truncated),
    compileOutput,
    resolveImage,
    editing: createFileEditing(
      client,
      uri,
      name,
      options,
      onSave,
      wiki,
      onWikiSaveStatus,
      readHint,
    ),
  });
  const resolveWiki = result.resolveWiki;
  return {
    tab: toTab(result),
    resolveWikiTab:
      resolveWiki &&
      (async () => {
        const wiki = await resolveWiki();
        if (wiki) return toTab({ ...wiki, resolveWiki: undefined });
      }),
  };
}
