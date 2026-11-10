import type { CSSProperties } from 'react';
import {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '../../icons/index.js';

export function WikiFileIcon({ name }: { name: string }) {
  const Icon = getFileTypeIcon(name);
  const key = resolveFileTypeIconKey(name);
  return (
    <Icon
      aria-hidden="true"
      className="ov-wiki-dashboard__file-icon"
      style={
        {
          '--ov-ui-wiki-icon-light': getFileTypeColor(key, 'light'),
          '--ov-ui-wiki-icon-dark': getFileTypeColor(key, 'dark'),
        } as CSSProperties
      }
    />
  );
}
