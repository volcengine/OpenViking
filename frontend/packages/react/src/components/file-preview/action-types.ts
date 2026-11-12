import type { ReactNode } from 'react';
import type { FilePreviewActions } from './types.js';

/** A host command. Return values are ignored; rejected promises report failure. */
export interface FilePreviewAction {
  key: string;
  icon: ReactNode;
  label: string;
  disabled?: boolean;
  className?: string;
  onClick: () => unknown;
}

/** Ordered commands and groups; empty groups do not split adjacent commands. */
export type FilePreviewActionItems = readonly (
  FilePreviewAction | readonly FilePreviewAction[]
)[];

/** A factory can follow the active source and component-owned editing state. */
export type FilePreviewActionSource =
  | FilePreviewActionItems
  | ((context: FilePreviewActions) => FilePreviewActionItems);
