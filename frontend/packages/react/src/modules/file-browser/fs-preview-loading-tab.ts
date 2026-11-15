import type { FilePreviewTab } from '../../components/file-preview/index.js';
import { nameFromUri } from './fs-model.js';
import { isSystemUri } from './preview-policy.js';
import { toSystemDirectoryTab } from './fs-system-directory-tab.js';

/**
 * 加载中占位页签：只带 key 与文件名，`loading` 让组件渲染加载态而不是「空文件」。
 * A placeholder tab while loading: it carries the key and file name, and `loading`
 * makes the component render its loading state rather than "empty file".
 */
export function loadingTab(uri: string, isDirectory: boolean): FilePreviewTab {
  if (isDirectory && isSystemUri(uri))
    return {
      ...toSystemDirectoryTab(uri),
      loading: true,
    };
  if (isDirectory) {
    return {
      kind: 'directory',
      key: uri,
      loading: true,
      levels: [
        {
          key: 'l0',
          label: '',
          source: {
            fileName: `${nameFromUri(uri)}.md`,
            path: uri,
            fileType: 'markdown',
            content: '',
          },
        },
        {
          key: 'l1',
          label: '',
          source: {
            fileName: `${nameFromUri(uri)}.md`,
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
    key: uri,
    loading: true,
    source: { fileName: nameFromUri(uri), path: uri, content: '' },
  };
}
