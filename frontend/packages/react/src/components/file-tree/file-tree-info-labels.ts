import enUS from './locales/en-US.js';
import type { FileTreeInfoKey, FileTreeInfoLabels } from './types.js';

export const DEFAULT_INFO_LABELS: FileTreeInfoLabels = enUS.info;

export function resolveInfoCopy(
  key: FileTreeInfoKey,
  hostLabels: FileTreeInfoLabels | undefined,
): { description?: string; triggerBehavior?: string } {
  const description =
    hostLabels?.description?.[key] ??
    DEFAULT_INFO_LABELS.description?.[key] ??
    undefined;
  const triggerBehavior =
    hostLabels?.triggerBehavior?.[key] ??
    DEFAULT_INFO_LABELS.triggerBehavior?.[key] ??
    undefined;

  return {
    description: description || undefined,
    triggerBehavior: triggerBehavior || undefined,
  };
}
