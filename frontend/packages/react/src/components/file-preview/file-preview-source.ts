import type { FileSource } from '@ark-design/ui/file-viewer';

import type { FilePreviewSource } from './types.js';

/** 把字符串编码成字节，避免依赖 `File` 构造器（服务端渲染阶段没有它）。 */
function encodeText(content: string): Uint8Array {
  return new TextEncoder().encode(content);
}

/**
 * 把宿主的 `source` 映射成底座的 `FileSource`，按 `content`、`file`、`url`
 * 的顺序取第一个有值的来源。都没有时返回 `undefined`，由组件渲染不支持态。
 *
 * Maps the host `source` onto the base `FileSource`, taking the first available
 * of `content`, `file` and `url`. Returns `undefined` when none is present, and
 * the component then renders the unsupported state.
 */
export function toFileSource(
  source: FilePreviewSource,
): FileSource | undefined {
  const { content, file, url, fileName, fileType } = source;

  if (typeof content === 'string') {
    return {
      kind: 'bytes',
      bytes: encodeText(content),
      name: fileName,
      ...(fileType ? { fileType } : {}),
    };
  }

  if (file) {
    return {
      kind: 'file',
      file:
        typeof File === 'undefined' || file instanceof File
          ? (file as File)
          : new File([file], fileName, { type: file.type }),
      name: fileName,
      ...(fileType ? { fileType } : {}),
    };
  }

  if (url) {
    return {
      kind: 'url',
      url,
      name: fileName,
      ...(fileType ? { fileType } : {}),
    };
  }

  return undefined;
}

/**
 * 判断文件是否为严格空文件。
 *
 * 只把长度为 0 视为空文件；空白字符组成的文件有内容但无可见文本，属于
 * 「暂无内容」，两者提示不同，不能合并成 `trim()` 判断。
 *
 * Whether the file itself is empty. Only a zero-length body counts as empty; a
 * body made of whitespace has content but nothing visible, which is a different
 * message. The two must not be merged into a `trim()` check.
 */
export function isEmptyFile(source: FilePreviewSource): boolean {
  return source.content === '';
}
