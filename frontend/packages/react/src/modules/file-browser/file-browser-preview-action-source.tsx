import { markPreviewAction } from '../../shared/analytics/preview-actions.js';
import { keepPreviewActionWhileEditing } from '../../components/file-preview/file-preview-action-visibility.js';
import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import EditorCopyIcon from '../../../../icons/svg/viking-bot/copy.svg?react';
import type {
  FilePreviewAction,
  FilePreviewActions,
} from '../../components/file-preview/index.js';
import type {
  FileBrowserPreviewActionContext,
  FileBrowserPreviewActions,
} from './preview-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** Content copying uses the editor draft when editing and the preview source otherwise. */
export function previewActionContext(
  context: FilePreviewActions,
  controls: Pick<FileBrowserPreviewActionContext, 'canDelete' | 'deleteFile'>,
  items: readonly (readonly FilePreviewAction[])[],
  options: FileBrowserPreviewActions | undefined,
  text: ResolvedLabels,
): FileBrowserPreviewActionContext {
  const content = context.editing ? context.draft : context.source?.content;
  const commands: FilePreviewAction[] = [];
  if (
    typeof content === 'string' &&
    (context.editing || content.length > 0) &&
    options?.copyContent !== false
  ) {
    commands.push(
      markPreviewAction(
        keepPreviewActionWhileEditing({
          key: 'copyContent',
          pinned: context.editing,
          label: text.copyContent,
          icon: context.editing ? (
            <EditorCopyIcon width={12} height={12} />
          ) : (
            <CopyIcon width={16} height={16} />
          ),
          className: 'ov-file-browser__preview-action--copy-content',
          onClick: () => navigator.clipboard.writeText(content),
        }),
        'copyContent',
      ),
    );
  }
  return {
    ...context,
    ...controls,
    defaultActions:
      context.editing && commands.length
        ? [...items, commands]
        : [[...commands, ...(items[0] ?? [])], ...items.slice(1)],
  };
}
