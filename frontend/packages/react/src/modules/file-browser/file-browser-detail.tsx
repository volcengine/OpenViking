import type { Locale } from '../../shared/locale.js';
import {
  FilePreview,
  type FilePreviewTab,
  type FilePreviewToolbarOptions,
  type FilePreviewHeaderSlots,
} from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

import type { FileBrowserLabels } from './types.js';

export interface FileBrowserDetailProps {
  tabs: readonly FilePreviewTab[];
  activeKey: string | null;
  locale: Locale;
  labels?: FileBrowserLabels['preview'];
  footer?: string;
  titleExtra?: ReactNode;
  readOnly?: boolean;
  previewSlots?: FilePreviewHeaderSlots;
  toolbar?: FilePreviewToolbarOptions;
  onActiveKeyChange: (key: string) => void;
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
  onActiveKeyChange,
}: FileBrowserDetailProps) {
  return (
    <div className="ov-file-browser__detail">
      <FilePreview
        tabs={
          decorateTabs(
            tabs,
            footer,
            titleExtra,
            readOnly,
            previewSlots,
            toolbar,
          ) as [FilePreviewTab, ...FilePreviewTab[]]
        }
        activeKey={activeKey ?? undefined}
        locale={locale}
        labels={labels}
        onActiveKeyChange={onActiveKeyChange}
      />
    </div>
  );
}

function decorateTabs(
  tabs: readonly FilePreviewTab[],
  footer: ReactNode,
  titleExtra: ReactNode,
  readOnly?: boolean,
  previewSlots?: FilePreviewHeaderSlots,
  toolbar?: FilePreviewToolbarOptions,
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
      toolbar: { ...tab.toolbar, ...toolbar },
      slots: {
        ...tab.slots,
        actions:
          previewSlots?.actions === undefined
            ? tab.slots?.actions
            : previewSlots.actions,
        menuItems: [
          ...(tab.slots?.menuItems ?? []),
          ...(previewSlots?.menuItems ?? []),
        ],
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
