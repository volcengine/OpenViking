import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '../../shared/components/button/button.js';
import EditIcon from '../../../../icons/svg/file-preview/edit.svg?react';
import { FilePreviewActionList } from './file-preview-action-list.js';
import type {
  FilePreviewAction,
  FilePreviewActionSource,
} from './action-types.js';
import type {
  FilePreviewActions,
  FilePreviewLabels,
  FilePreviewToolbarOptions,
} from './types.js';

export function FilePreviewToolbar({
  actions,
  text,
  options,
  actionItems,
  extraActions,
}: {
  actions: FilePreviewActions;
  text: Required<FilePreviewLabels>;
  options?: FilePreviewToolbarOptions;
  actionItems?: FilePreviewActionSource;
  extraActions?: ReactNode;
}) {
  // The toolbar remains mounted while editing hides its command list.
  const running = useRef(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<{ reason: unknown }>();
  const execute = async (action: FilePreviewAction) => {
    if (running.current || actions.busy || action.disabled) return;
    running.current = true;
    setPending(true);
    setFailure(undefined);
    try {
      await action.onClick();
    } catch (reason) {
      setFailure({ reason });
    } finally {
      running.current = false;
      setPending(false);
    }
  };
  if (actions.editing) return null;
  const items =
    typeof actionItems === 'function'
      ? actionItems(actions)
      : (actionItems ?? []);
  const switchVisible = actions.canSwitchView && options?.viewSwitch !== false;
  const editVisible = actions.canEdit && options?.edit !== false;
  const hasItems = items.some(
    (item) => !Array.isArray(item) || item.length > 0,
  );
  if (!switchVisible && !editVisible && !hasItems && !extraActions) return null;
  return (
    <div className="ov-file-preview__toolbar">
      {switchVisible ? (
        <div
          className="ov-file-preview__switch"
          role="group"
          aria-label={text.viewSwitch}
        >
          {(['preview', 'source'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className="ov-file-preview__switch-item"
              aria-pressed={actions.viewMode === mode}
              disabled={actions.busy}
              onClick={() => actions.setViewMode(mode)}
            >
              {mode === 'preview' ? text.previewMode : text.sourceMode}
            </button>
          ))}
        </div>
      ) : null}
      {editVisible ? (
        <Button
          size="small"
          type="default"
          iconOnly
          className="ov-file-preview__toolbar-button"
          aria-label={text.edit}
          title={text.edit}
          disabled={actions.busy}
          onClick={actions.startEditing}
        >
          <EditIcon width={14} height={14} aria-hidden="true" />
        </Button>
      ) : null}
      <FilePreviewActionList
        items={items}
        pending={pending}
        failure={failure}
        execute={execute}
        disabled={actions.busy}
        text={text}
      />
      {extraActions}
    </div>
  );
}
