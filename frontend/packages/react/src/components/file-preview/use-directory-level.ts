import { useState } from 'react';

import type { FilePreviewDirectoryTab, FilePreviewSource } from './types.js';

/**
 * 目录页签当前看的是哪一级摘要。
 *
 * 它必须由预览外壳持有，而不是留在正文组件内部：复制正文取的是「用户正在看的
 * 那一份」，标题行的路径也来自同一级，两处都在外壳这一层取值；留在正文里，复制
 * 到的永远是第一级。
 *
 * 宿主给了 `activeLevelKey` 时以宿主的为准，这里只记非受控页签自己的选择。
 *
 * Which summary level of a directory tab is on screen.
 *
 * It belongs to the preview shell rather than inside the body component: copy-content
 * takes "what the user is looking at" and the header path comes from the same level,
 * both read at this layer — kept in the body, a copy would always take the first
 * level.
 *
 * A host-supplied `activeLevelKey` wins; this only records the choice of an
 * uncontrolled tab.
 */
export interface DirectoryLevelState {
  /** 实际渲染的那一级。The level actually rendered. */
  activeLevel?: FilePreviewDirectoryTab['levels'][number];
  /** 把当前层级固定下来的目录页签，交给正文组件。 */
  /** The directory tab with the level pinned, handed to the body. */
  directoryTab?: FilePreviewDirectoryTab;
  /** 复制路径与复制正文共用的来源。The source shared by copy-path and copy-content. */
  source?: FilePreviewSource;
}

export function useDirectoryLevel(
  tab: FilePreviewDirectoryTab | undefined,
): DirectoryLevelState {
  const [levelKeys, setLevelKeys] = useState<Record<string, string>>({});

  if (!tab) return {};

  const activeLevel =
    tab.levels.find(
      (level) => level.key === (tab.activeLevelKey ?? levelKeys[tab.key]),
    ) ?? tab.levels[0];

  return {
    activeLevel,
    directoryTab: {
      ...tab,
      activeLevelKey: activeLevel.key,
      onActiveLevelChange: (key: string) => {
        if (tab.activeLevelKey === undefined) {
          setLevelKeys((previous) => ({ ...previous, [tab.key]: key }));
        }
        tab.onActiveLevelChange?.(key);
      },
    },
    source: activeLevel.source,
  };
}
