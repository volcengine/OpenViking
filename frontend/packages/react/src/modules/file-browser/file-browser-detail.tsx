import type { Locale } from '../../shared/locale.js';
import {
  FilePreview,
  type FilePreviewTab,
  type FilePreviewActionSource,
  type FilePreviewToolbarOptions,
  type FilePreviewHeaderSlots,
} from '../../components/file-preview/index.js';
import { useState, type ReactNode } from 'react';
import {
  FilePreviewEditContext,
  type FilePreviewEditRequest,
} from '../../components/file-preview/file-preview-edit-request.js';
import { dashboardKey } from './dashboard-model.js';
import { isEditableText } from './preview-policy.js';

import type { FileBrowserLabels } from './types.js';

export interface FileBrowserDetailProps {
  tabs: readonly FilePreviewTab[];
  activeKey: string | null;
  locale: Locale;
  labels?: FileBrowserLabels['preview'];
  footer?: ReactNode;
  titleExtra?: ReactNode;
  readOnly?: boolean;
  previewSlots?: FilePreviewHeaderSlots;
  toolbar?: FilePreviewToolbarOptions;
  actions?: FilePreviewActionSource;
  canEditConflict: boolean;
  onActiveKeyChange: (key: string) => void;
  onSave?: () => void;
}

export function FileBrowserDetail({
  tabs,
  activeKey,
  locale,
  labels,
  footer,
  titleExtra,
  readOnly,
  previewSlots,
  toolbar,
  actions,
  canEditConflict,
  onActiveKeyChange,
  onSave,
}: FileBrowserDetailProps) {
  const [request, setRequest] = useState<FilePreviewEditRequest | null>(null);
  const conflictFile = canEditConflict
    ? tabs.find(
        (tab) =>
          tab.kind === 'file' &&
          !tab.disabled &&
          dashboardKey(tab.key) === activeKey &&
          isEditableText(tab.key),
      )
    : undefined;
  const editFile = conflictFile
    ? () => {
        const next = {
          tabKey: conflictFile.key,
          onConsumed: () =>
            setRequest((pending) => (pending === next ? null : pending)),
        };
        setRequest(next);
        onActiveKeyChange(conflictFile.key);
      }
    : null;
  return (
    <div className="ov-file-browser__detail">
      <FilePreviewEditContext.Provider
        value={{ request: canEditConflict ? request : null, editFile }}
      >
        <FilePreview
          tabs={
            decorateTabs(
              tabs,
              footer,
              titleExtra,
              readOnly,
              previewSlots,
              actions,
              onSave,
            ) as [FilePreviewTab, ...FilePreviewTab[]]
          }
          toolbar={toolbar}
          activeKey={activeKey ?? undefined}
          locale={locale}
          labels={labels}
          onActiveKeyChange={(key) => {
            setRequest(null);
            onActiveKeyChange(key);
          }}
        />
      </FilePreviewEditContext.Provider>
    </div>
  );
}

function decorateTabs(
  tabs: readonly FilePreviewTab[],
  footer: ReactNode,
  titleExtra: ReactNode,
  readOnly?: boolean,
  previewSlots?: FilePreviewHeaderSlots,
  actions?: FilePreviewActionSource,
  onSave?: () => void,
): readonly FilePreviewTab[] {
  return tabs.map((tab) => {
    if (tab.kind === 'content') return tab;
    const editableTab =
      tab.kind === 'file' && readOnly
        ? {
            ...tab,
            editing: undefined,
            levels: tab.levels?.map((level) => ({
              ...level,
              editing: undefined,
            })),
          }
        : tab;
    return {
      ...editableTab,
      ...(editableTab.kind === 'file' && editableTab.editing && onSave
        ? {
            editing: {
              ...editableTab.editing,
              onSave: (value: string) => {
                editableTab.editing?.onSave?.(value);
                onSave();
              },
            },
          }
        : {}),
      actions: actions ?? tab.actions,
      slots: {
        ...tab.slots,
        actions:
          previewSlots?.actions === undefined
            ? tab.slots?.actions
            : previewSlots.actions,
        footer: tab.slots?.footer ?? footer,
        titleExtra: (
          <>
            {tab.slots?.titleExtra}
            {titleExtra}
          </>
        ),
        extraActions: previewSlots?.extraActions ? (
          <>
            {tab.slots?.extraActions}
            {previewSlots?.extraActions}
          </>
        ) : (
          tab.slots?.extraActions
        ),
      },
    };
  });
}
