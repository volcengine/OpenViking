import type { FilePreviewDirectoryTab } from '../../components/file-preview/index.js';
import { directoryIcon } from './fs-icon.js';
import { nameFromUri } from './fs-model.js';

/** Built-in directories keep their path and tab, but have no summary levels. */
export function toSystemDirectoryTab(
  uri: string,
  openParent?: (uri: string) => Promise<void>,
): FilePreviewDirectoryTab {
  const name = nameFromUri(uri);
  return {
    kind: 'directory',
    key: uri,
    title: name,
    icon: directoryIcon(),
    levels: [
      {
        key: 'system',
        label: '',
        source: { path: uri, fileName: name, content: '' },
      },
    ],
    operations: { openParent },
  };
}
