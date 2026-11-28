import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';

import { clampResizableSize } from '../../shared/primitives/resizable/index.js';
import type { Locale } from '../../shared/locale.js';
import {
  SIDE_BY_SIDE_MIN_WIDTH,
  STACKED_TREE_MIN_HEIGHT,
  TREE_DEFAULT_WIDTH_BY_LOCALE,
  TREE_MIN_WIDTH,
  resolveStackedTreeMaxHeight,
  resolveTreeMaxWidth,
} from './file-browser-layout.js';

export function useFileBrowserLayout(locale: Locale) {
  const ref = useRef<HTMLDivElement>(null);
  const [{ width, height }, setSize] = useState({ width: 0, height: 0 });
  const [preferredWidth, setPreferredWidth] = useState<number>();
  const [preferredHeight, setPreferredHeight] = useState<number>();

  // useEffect 必要性：观察已挂载容器的宽高，宿主调整布局时也会变化。卸载时断开观察。
  // Observe the mounted container so host layout changes update both limits.
  // Disconnect on cleanup to stop updates from the old element.
  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const update = (box: { width: number; height: number }) => {
      const width = Math.round(box.width);
      const height = Math.floor(box.height);
      setSize((current) =>
        current.width === width && current.height === height
          ? current
          : { width, height },
      );
    };

    update(element.getBoundingClientRect());
    const observer = new ResizeObserver(([entry]) => {
      if (entry) update(entry.contentRect);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const stacked = width > 0 && width < SIDE_BY_SIDE_MIN_WIDTH;
  const maxWidth = resolveTreeMaxWidth(width);
  const treeWidth = Math.min(
    preferredWidth ?? TREE_DEFAULT_WIDTH_BY_LOCALE[locale],
    maxWidth,
  );
  const maxHeight = stacked ? resolveStackedTreeMaxHeight(height) : undefined;
  const stackedDivider =
    maxHeight === undefined
      ? undefined
      : {
          orientation: 'horizontal' as const,
          min: STACKED_TREE_MIN_HEIGHT,
          max: maxHeight,
          value: clampResizableSize(
            preferredHeight ?? height * 0.4,
            STACKED_TREE_MIN_HEIGHT,
            maxHeight,
          ),
          onResize: setPreferredHeight,
        };

  return {
    ref,
    stacked,
    divider: stacked
      ? stackedDivider
      : {
          orientation: 'vertical' as const,
          min: TREE_MIN_WIDTH,
          max: maxWidth,
          value: treeWidth,
          onResize: setPreferredWidth,
        },
    layoutStyle: {
      '--ov-file-browser-tree-width': `${treeWidth}px`,
      '--ov-ui-file-browser-tree-height': stackedDivider
        ? `${stackedDivider.value}px`
        : undefined,
    } as CSSProperties,
  };
}
