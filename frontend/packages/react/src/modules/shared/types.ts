import type { CSSProperties } from 'react';

export interface ModulePlaceholderLabels {
  title?: string;
  description?: string;
}

export interface ModulePlaceholderProps {
  className?: string;
  style?: CSSProperties;
  labels?: ModulePlaceholderLabels;
}
