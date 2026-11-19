import type { FilePreviewDirectoryTab, FilePreviewFileTab } from './types.js';
import type { FileSource } from '@ark-design/ui/file-viewer';

import type { FilePreviewSource } from './types.js';

/**
 * `source` 的 memo 依赖。逐项列出而不是取摘要：字符串按值比较，长度与首尾
 * 片段相同的两份正文会被摘要判成同一份，底座就会一直显示旧内容；`File`/`Blob`
 * 按对象身份比较，同名同路径换一个实例也能被认出来。
 *
 * The memo dependency for `source`. Every field is listed rather than hashed:
 * strings compare by value, and a digest built from length plus edges would treat
 * two same-length bodies with matching ends as identical, leaving the base on the
 * old content; `File`/`Blob` compare by identity, so a different instance under
 * the same name and path is still detected.
 */
export function sourceDeps(source: FilePreviewSource) {
  return [
    source.content,
    source.file,
    source.url,
    source.fileName,
    source.fileType,
    source.path,
  ] as const;
}

/** 把正文编码成底座支持的字节来源。 */
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
        file instanceof File
          ? file
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

export function levelToFileTab(
  tab: FilePreviewDirectoryTab,
  key: string,
): FilePreviewFileTab | undefined {
  const level = tab.levels.find((candidate) => candidate.key === key);
  if (!level) return undefined;
  return {
    kind: 'file',
    key: `${tab.key}::${level.key}`,
    source: level.source,
    emptyLabel: level.emptyLabel,
    resources: level.resources ?? tab.resources,
    resolveImage: tab.resolveImage,
    operations: tab.operations,
    slots: tab.slots,
  };
}
