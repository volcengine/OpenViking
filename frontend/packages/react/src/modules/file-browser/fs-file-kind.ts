import type { FilePreviewFileTab } from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

/**
 * 按扩展名判断一个文件该用哪种方式读取。
 *
 * `content/read` 把内容当文本返回，二进制文件走它只会得到一串乱码——图片、
 * PDF、Office 文档因此必须走 `content/download` 拿原始字节。分流的依据是扩展名，
 * 而不是去猜接口返回什么。
 *
 * Decides how a file should be read, by extension.
 *
 * `content/read` returns text, so a binary file through it only yields mojibake —
 * images, PDFs and Office documents must go through `content/download` for the raw
 * bytes. The split follows the extension rather than guessing from the response.
 */
const BINARY_EXTENSIONS = new Set([
  // 图片 Images
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'avif',
  'bmp',
  'ico',
  'svg',
  // 文档 Documents
  'pdf',
  'doc',
  'docx',
  'ppt',
  'pptx',
  'xls',
  'xlsx',
  // 媒体 Media
  'mp4',
  'webm',
  'mov',
  'mp3',
  'wav',
  'ogg',
  'm4a',
  // 压缩包 Archives
  'zip',
  'gz',
  'tar',
  'ovpack',
]);

/**
 * 该文件是否应按二进制读取。没有扩展名或扩展名未知时按文本处理——绝大多数
 * 无扩展名的资源都是文本，例如 `Dockerfile`、`Makefile`。
 *
 * Whether the file should be read as binary. A file with no extension, or an
 * unknown one, is treated as text — most extensionless resources are text, such as
 * `Dockerfile` and `Makefile`.
 */
export function isBinaryFile(name: string): boolean {
  const dot = name.lastIndexOf('.');
  if (dot < 0 || dot === name.length - 1) return false;

  return BINARY_EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

/**
 * 二进制文件的 MIME 类型，用于把字节包成 Blob 时带上类型；未知时用
 * `application/octet-stream`。
 *
 * The MIME type of a binary file, used when wrapping its bytes in a Blob; unknown
 * extensions fall back to `application/octet-stream`.
 */
const MIME_BY_EXTENSION: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  avif: 'image/avif',
  bmp: 'image/bmp',
  ico: 'image/x-icon',
  svg: 'image/svg+xml',
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  mp4: 'video/mp4',
  webm: 'video/webm',
  mov: 'video/quicktime',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  ogg: 'audio/ogg',
  m4a: 'audio/mp4',
  zip: 'application/zip',
  gz: 'application/gzip',
  tar: 'application/x-tar',
};

/** 该文件名对应的 MIME 类型。The MIME type for this file name. */
export function mimeTypeOf(name: string): string {
  const dot = name.lastIndexOf('.');
  if (dot < 0) return 'application/octet-stream';

  return (
    MIME_BY_EXTENSION[name.slice(dot + 1).toLowerCase()] ??
    'application/octet-stream'
  );
}

/**
 * 一个二进制文件的预览页签。字节直接交给底座，按 `fileName` 的扩展名选 Adapter。
 * A preview tab for a binary file. The bytes go to the base, which picks an adapter
 * from the `fileName` extension.
 */
export function toBinaryTab(
  uri: string,
  name: string,
  file: Blob,
  icon?: ReactNode,
  openParent?: (uri: string) => Promise<void>,
): FilePreviewFileTab {
  return {
    kind: 'file',
    key: uri,
    title: name,
    icon,
    operations: { openParent },
    source: { file, fileName: name, path: uri },
  };
}
