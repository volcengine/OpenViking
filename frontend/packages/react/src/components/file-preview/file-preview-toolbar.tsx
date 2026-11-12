import { useState, type ReactNode } from 'react';
import { Button } from '../../shared/components/button/button.js';
import { WikiEditIcon } from '../../icons/index.js';
import type { FilePreviewActions, FilePreviewLabels } from './types.js';

export function FilePreviewToolbar({
  actions,
  text,
  extraActions,
}: {
  actions: FilePreviewActions;
  text: Required<FilePreviewLabels>;
  extraActions?: ReactNode;
}) {
  const [copyResult, setCopyResult] = useState<boolean>();
  const hasActions =
    actions.editing ||
    actions.canCopyContent ||
    actions.canEdit ||
    actions.canDownload ||
    extraActions;
  if (!actions.canSwitchView && !hasActions) return null;
  const copyLabel =
    copyResult === undefined
      ? text.copyContent
      : copyResult
        ? text.copied
        : text.copyFailed;
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
        </div>
      ) : null}
      {actions.canSwitchView ? (
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
      {actions.canSwitchView && hasActions ? (
        <span className="ov-file-preview__toolbar-divider" aria-hidden="true" />
      ) : null}
      {actions.canCopyContent ? (
        <>
          <Button
            size="small"
            type="default"
            iconOnly
            className="ov-file-preview__toolbar-button"
            aria-label={text.copyContent}
            title={copyLabel}
            disabled={actions.busy}
            onBlur={() => setCopyResult(undefined)}
            onClick={() => void actions.copyContent().then(setCopyResult)}
          >
            <svg
              aria-hidden="true"
              width="14"
              height="14"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
            >
              <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
              <path d="M10.5 5.5v-1a1.5 1.5 0 0 0-1.5-1.5H4a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 4 11h1" />
            </svg>
          </Button>
          {copyResult !== undefined ? (
            <span className="ov-file-preview__sr-only" role="status">
              {copyLabel}
            </span>
          ) : null}
        </>
      ) : null}
      {actions.canEdit ? (
        <Button
          size="small"
          type="default"
          className="ov-file-preview__toolbar-button"
          disabled={actions.busy}
          onClick={actions.startEditing}
        >
          <WikiEditIcon width={14} height={14} aria-hidden="true" />
          {text.edit}
        </Button>
      ) : null}
      {actions.canDownload ? (
        <Button
          size="small"
          type="default"
          iconOnly
          className="ov-file-preview__toolbar-button"
          aria-label={text.download}
          title={text.download}
          disabled={actions.busy}
          onClick={actions.download}
        >
          <svg
            aria-hidden="true"
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M8 2.5v8m-3-3 3 3 3-3M2.5 12.5h11" />
          </svg>
        </Button>
      ) : null}
      {extraActions}
    </div>
  );
}
