import type { FilePreviewAction } from './action-types.js';

const availableDuringEdit = new WeakSet<FilePreviewAction>();

export function keepPreviewActionWhileEditing(action: FilePreviewAction) {
  availableDuringEdit.add(action);
  return action;
}

export const isPreviewActionAvailableDuringEdit = (action: FilePreviewAction) =>
  availableDuringEdit.has(action);
