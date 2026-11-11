import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import type {
  FilePreviewAction,
  FilePreviewActionItems,
} from './action-types.js';
import type {
  FilePreviewActions,
  FilePreviewLabels,
  FilePreviewMenuItem,
  FilePreviewToolbarOptions,
} from './types.js';

/** @deprecated Staged adapter for the existing FileBrowser; remove with its migration. */
export function legacyFilePreviewActions(
  items: readonly FilePreviewMenuItem[],
  context: FilePreviewActions,
  text: Required<FilePreviewLabels>,
  options?: FilePreviewToolbarOptions,
): FilePreviewActionItems {
  const commands: FilePreviewMenuItem[] = [];
  if (context.canCopyContent && options?.copyContent !== false)
    commands.push({
      key: 'copyContent',
      label: text.copyContent,
      icon: <CopyIcon width={16} height={16} />,
      onSelect: async () => {
        if (!(await context.copyContent())) throw new Error(text.copyFailed);
      },
    });
  if (context.canDownload && options?.download !== false)
    commands.push({
      key: 'download',
      label: text.download,
      onSelect: context.download,
    });
  commands.push(...items);
  const groups: FilePreviewAction[][] = [];
  for (const command of commands) {
    if (groups.length === 0 || command.separatorBefore) groups.push([]);
    groups[groups.length - 1]!.push({
      key: command.key,
      label: command.label,
      icon: command.icon ?? null,
      disabled: command.disabled,
      className: command.danger
        ? 'ov-file-preview__menu-item--danger'
        : undefined,
      onClick: command.onSelect,
    });
  }
  return groups;
}
