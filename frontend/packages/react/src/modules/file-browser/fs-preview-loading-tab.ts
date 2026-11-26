import { directoryIcon, fileTypeIcon } from './fs-icon.js';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { nameFromUri } from '../../shared/directory-tree/rows.js';
import { decodePathLabel } from '../../shared/path-label.js';
import { isSystemUri } from '../../shared/resource-uri.js';
import { toSystemDirectoryTab } from './fs-system-directory-tab.js';

/**
 * 加载中占位页签：只带 key 与文件名，`loading` 让组件渲染加载态而不是「空文件」。
 * A placeholder tab while loading: it carries the key and file name, and `loading`
 * makes the component render its loading state rather than "empty file".
 */
export function loadingTab(
  uri: string,
  resourceType: 'directory' | 'file' | 'unknown',
): FilePreviewTab {
  const isDirectory = resourceType === 'directory';
  if (isDirectory && isSystemUri(uri))
    return {
      ...toSystemDirectoryTab(uri),
      loading: true,
    };
  const name = nameFromUri(uri);
  if (isDirectory) {
    return {
      kind: 'directory',
      icon: directoryIcon(),
      key: uri,
      title: decodePathLabel(name),
      loading: true,
      levels: [
        {
          key: 'l0',
          label: '',
          source: {
            fileName: `${name}.md`,
            path: uri,
            fileType: 'markdown',
            content: '',
          },
        },
        {
          key: 'l1',
          label: '',
          source: {
            fileName: `${name}.md`,
            path: uri,
            fileType: 'markdown',
            content: '',
          },
        },
      ],
    };
  }
  return {
    kind: 'file',
    icon: resourceType === 'file' ? fileTypeIcon(name) : undefined,
    key: uri,
    loading: true,
    title: decodePathLabel(name),
    source: { fileName: name, path: uri, content: '' },
  };
}
