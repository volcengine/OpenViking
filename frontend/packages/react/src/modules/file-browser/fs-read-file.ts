import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FilePreviewFileTab } from '../../components/file-preview/index.js';
import { isBinaryFile, mimeTypeOf, toBinaryTab } from './fs-file-kind.js';
import { fileTypeIcon } from './fs-icon.js';
import { joinLines, splitLines } from './fs-lines.js';
import { isMemoryUri, stripMemoryFieldsTrailer } from './fs-memory-fields.js';
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

  const read = (limit: number) =>
    client
      .request<string>({
        method: 'GET',
        path: OV_PATHS.contentRead,
        query: { uri, limit },
        options,
      })
      .then((value) => (typeof value === 'string' ? value : ''));

  const first = await read(MAX_LINES);
  const resolveImage = createPreviewImageResolver(client, uri, false, options);
  const editing = createFileEditing(client, uri, name, options, onSave);
  if (!isMemoryUri(uri)) {
    const tab = toFileTab(
      { uri },
      first,
      MAX_LINES,
      icon,
      openParent,
      splitLines(first).length >= MAX_LINES,
    );
    return {
      ...tab,
      resolveImage,
      editing,
    };
  }

  // 记忆文件末尾的元数据尾注不属于正文：默认读取已由服务端剥掉，宿主把请求变成
  // 原始读取时它会原样回来，这里按同一规则兜底。
  //
  // 但「末尾」只有在确认读到文件末尾时才可信：没读满上限说明响应就是整个文件，
  // 可以直接剥；读满了则可能还有后续内容，此时响应末尾的同形注释可能是正文（例如
  // 文档里举例写出的这段标记），照剥会删掉真实内容、还会把截断状态报成未截断。
  // 这种情况再取一次全文，剥完自己按上限切——顺带也让被上限截断在关闭标记之前的
  // 尾注能被完整识别。
  //
  // The metadata trailer of a memory file is not part of the body: a default read has
  // the server strip it, and a host that turns the request into a raw read gets it
  // back, so the same rule is applied here.
  //
  // "The end" is only trustworthy once the end of the file has been seen: a response
  // short of the cap is the whole file, so stripping is safe; a full one may continue,
  // and a same-shaped comment at its end could be body text (such as the marker quoted
  // in a document) — stripping it would delete real content and report the truncation
  // as false. That case reads the whole file, strips, then applies the cap itself,
  // which also lets a trailer cut off before its closing marker be recognised.
  // 行边界按服务端的分隔符算：`limit` 是服务端按 `splitlines(keepends=True)` 数
  // 的行，用 `split('\n')` 数会让两边对不上——单独 `\r` 换行的文件读满 2000 行却
  // 只数到 1 行，而每行以 `\n` 结尾的文件又会多出一个末尾空行。
  //
  // Line boundaries follow the server's separators: `limit` counts lines the way
  // `splitlines(keepends=True)` does, and `split('\n')` disagrees with it — a file
  // with lone `\r` line breaks reads 2000 lines but counts as 1, while one whose lines
  // each end in `\n` gains a trailing empty line.
  const full = splitLines(first).length < MAX_LINES ? first : await read(-1);
  const lines = splitLines(stripMemoryFieldsTrailer(full));
  const truncated = lines.length > MAX_LINES;
  const visible = joinLines(truncated ? lines.slice(0, MAX_LINES) : lines);
  return {
    ...toFileTab({ uri }, visible, MAX_LINES, icon, openParent, truncated),
    resolveImage,
    editing,
  };
}
