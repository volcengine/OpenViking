import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import type {
  FilePreviewAction,
  FilePreviewActions,
} from '../../components/file-preview/index.js';
import type {
  FileBrowserPreviewActionContext,
  FileBrowserPreviewActions,
} from './preview-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** Content copying is a module command; the component only exposes the active source. */
export function previewActionContext(
  context: FilePreviewActions,
  controls: Pick<FileBrowserPreviewActionContext, 'canDelete' | 'deleteFile'>,
  items: readonly (readonly FilePreviewAction[])[],
  options: FileBrowserPreviewActions | undefined,
  text: ResolvedLabels,
): FileBrowserPreviewActionContext {
  const content = context.source?.content;
  const commands: FilePreviewAction[] = [];
  if (
    typeof content === 'string' &&
    content.length > 0 &&
    options?.copyContent !== false
  ) {
    commands.push({
      key: 'copyContent',
      label: text.copyContent,
      icon: <CopyIcon width={16} height={16} />,
      onClick: () => navigator.clipboard.writeText(content),
    });
  }
  return {
    ...context,
    ...controls,
    defaultActions: [[...commands, ...(items[0] ?? [])], ...items.slice(1)],
  };
}
