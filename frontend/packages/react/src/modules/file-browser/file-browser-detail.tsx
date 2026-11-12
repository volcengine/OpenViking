import type { Locale } from '../../shared/locale.js';
import {
  FilePreview,
  type FilePreviewTab,
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
  extraActions?: ReactNode;
  readOnly?: boolean;
  onActiveKeyChange: (key: string) => void;
}

export function FileBrowserDetail({
  tabs,
  activeKey,
  locale,
  labels,
  footer,
  titleExtra,
  extraActions,
  readOnly,
  onActiveKeyChange,
}: FileBrowserDetailProps) {
  return (
    <div className="ov-file-browser__detail">
      <FilePreview
        tabs={
          decorateTabs(tabs, footer, titleExtra, extraActions, readOnly) as [
            FilePreviewTab,
            ...FilePreviewTab[],
          ]
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
  extraActions: ReactNode,
  readOnly?: boolean,
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
      slots: {
        ...tab.slots,
        footer: tab.slots?.footer ?? footer,
        titleExtra: (
          <>
            {tab.slots?.titleExtra}
            {titleExtra}
          </>
        ),
        extraActions: extraActions ? (
          <>
            {tab.slots?.extraActions}
            {extraActions}
          </>
        ) : (
          tab.slots?.extraActions
        ),
      },
    };
  });
}
