import { Exposure } from '../../shared/analytics/exposure.js';
import type { ReactNode } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import EditIcon from '../../../../icons/svg/file-preview/edit.svg?react';
import { useFilePreviewCommands } from './file-preview-command-state.js';
import { FilePreviewActionList } from './file-preview-action-list.js';
import { normalizeFilePreviewActions } from './normalize-file-preview-actions.js';
import type { FilePreviewActionSource } from './action-types.js';
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
  const commands = useFilePreviewCommands();
  const groups = normalizeFilePreviewActions(
    typeof actionItems === 'function'
      ? actionItems(actions)
      : (actionItems ?? []),
  );
  const pinned = groups.flat().filter((action) => action.pinned);
  const items = groups.map((group) => group.filter((action) => !action.pinned));
  const switchVisible = actions.canSwitchView && options?.viewSwitch !== false;
  const editVisible = actions.canEdit && options?.edit !== false;
  const hasItems = items.some(
    (item) => !Array.isArray(item) || item.length > 0,
  );
  if (
    !actions.editing &&
    !switchVisible &&
    !editVisible &&
    !hasItems &&
    !pinned.length &&
    !extraActions
  )
    return null;
  return (
    <div className="ov-file-preview__toolbar">
      {actions.editing ? (
        <div className="ov-file-preview__edit-actions">
          <Button
            size="small"
            type="default"
            className="ov-file-preview__edit-action"
            icon={
              <span
                aria-hidden="true"
                className="ov-file-preview__edit-icon ov-file-preview__edit-icon--cancel"
              />
            }
            disabled={actions.busy}
            onClick={actions.cancelEditing}
          >
            {text.leaveEditing}
          </Button>
          <Exposure name="file_preview_save_show">
            <Button
              size="small"
              type="primary"
              className="ov-file-preview__edit-action"
              icon={
                <span
                  aria-hidden="true"
                  className="ov-file-preview__edit-icon ov-file-preview__edit-icon--save"
                />
              }
              loading={actions.busy}
              onClick={() => void actions.saveEditing()}
            >
              {text.save}
            </Button>
          </Exposure>
        </div>
      ) : null}

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
              disabled={actions.busy || actions.editing}
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
      {pinned.map((action) => (
        <FilePreviewActionList
          key={action.key}
          items={[action]}
          pending={commands.pending}
          execute={(item) => commands.execute(item, actions.busy)}
          disabled={actions.busy}
          text={text}
        />
      ))}
      {!actions.editing ? (
        <>
          <FilePreviewActionList
            items={items}
            pending={commands.pending}
            execute={(action) => commands.execute(action, actions.busy)}
            disabled={actions.busy}
            text={text}
          />
          {extraActions}
        </>
      ) : null}
    </div>
  );
}
