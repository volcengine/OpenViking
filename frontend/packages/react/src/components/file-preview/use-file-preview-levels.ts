import { useState } from 'react';
import type { FilePreviewTab } from './types.js';

/** Level data stays with the host; only the uncontrolled selection lives here. */
export function useFilePreviewLevels(selected: FilePreviewTab) {
  const [keys, setKeys] = useState<Record<string, string>>({});
  const levels = selected.kind === 'file' ? selected.levels : undefined;
  const requested =
    selected.kind === 'file'
      ? (selected.activeLevelKey ?? keys[selected.key])
      : undefined;
  const level =
    levels?.find((item) => item.key === requested && !item.disabled) ??
    levels?.find((item) => !item.disabled);
  const tab: FilePreviewTab =
    selected.kind === 'file' && level
      ? {
          ...selected,
          source: level.source,
          resources: level.resources ?? selected.resources,
          editing: level.editing,
        }
      : selected;
  function selectLevel(key: string) {
    if (
      selected.kind !== 'file' ||
      key === level?.key ||
      !levels?.some((item) => item.key === key && !item.disabled)
    )
      return;
    if (selected.activeLevelKey === undefined)
      setKeys((current) => ({ ...current, [selected.key]: key }));
    selected.onLevelChange?.(key);
  }
  return { tab, levelKey: level?.key, selectLevel };
}
