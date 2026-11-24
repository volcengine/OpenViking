import type {
  FilePreviewAction,
  FilePreviewActionItems,
} from './action-types.js';

/** Keep order; explicit nonempty arrays form groups, empty arrays are invisible. */
export function normalizeFilePreviewActions(items: FilePreviewActionItems) {
  const groups: (readonly FilePreviewAction[])[] = [];
  let consecutive: FilePreviewAction[] | undefined;
  for (const item of items) {
    if (Array.isArray(item)) {
      if (item.length === 0) continue;
      groups.push(item);
      consecutive = undefined;
    } else {
      if (!consecutive) {
        consecutive = [];
        groups.push(consecutive);
      }
      consecutive.push(item as FilePreviewAction);
    }
  }
  return groups;
}
