import type { Locale } from '../../shared/locale.js';
import {
  FilePreview,
  type FilePreviewTab,
  type FilePreviewEditing,
} from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

import type { FileBrowserLabels, FileBrowserProps } from './types.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import { FileBrowserFailure } from './file-browser-failure.js';

export interface FileBrowserDetailProps {
  tabs: readonly FilePreviewTab[] | null;
  text: ResolvedLabels;
  error: Error | null;
  onRetry: () => Promise<void>;
  activeKey: string | null;
  locale: Locale;
  labels?: FileBrowserLabels['preview'];
  footer?: string;
  titleExtra?: ReactNode;
  extraActions?: ReactNode;
  readOnly?: boolean;
  resourceUri: string | null;
  renderResourceTab?: FileBrowserProps['renderResourceTab'];
  onEditingChange?: FileBrowserProps['onEditingChange'];
  onSaved?: FileBrowserProps['onSaved'];
  onActiveKeyChange: (key: string) => void;
}

export function FileBrowserDetail({
  tabs,
  text,
  error,
  onRetry,
  activeKey,
  locale,
  labels,
  footer,
  titleExtra,
  extraActions,
  readOnly,
  resourceUri,
  renderResourceTab,
  onEditingChange,
  onSaved,
  onActiveKeyChange,
}: FileBrowserDetailProps) {
  if (!tabs?.length) {
    return (
      <div className="ov-file-browser__detail">
        {resourceUri && error ? (
          <FileBrowserFailure
            message={text.error}
            detail={error.message}
            retryLabel={text.retry}
            onRetry={() => void onRetry()}
          />
        ) : resourceUri ? (
          <p className="ov-file-browser__hint" role="status">
            {text.loading}
          </p>
        ) : (
          <FileBrowserEmpty label={text.empty} />
        )}
      </div>
    );
  }
  const resourceTab = tabs.find((tab) => tab.key === resourceUri);
  const custom =
    resourceUri &&
    resourceTab &&
    resourceTab.kind !== 'content' &&
    !resourceTab.loading
      ? renderResourceTab?.({
          uri: resourceUri,
          isDirectory: resourceTab.kind === 'directory',
          content:
            resourceTab.kind === 'file'
              ? resourceTab.source.content
              : undefined,
        })
      : undefined;
  const decorated = decorateTabs(
    tabs,
    footer,
    titleExtra,
    extraActions,
    readOnly,
    onSaved,
  );
  const previewTabs = custom
    ? [
        ...decorated,
        { kind: 'content' as const, key: `host:${resourceUri}`, ...custom },
      ]
    : decorated;
  return (
    <div className="ov-file-browser__detail">
      <FilePreview
        tabs={previewTabs as [FilePreviewTab, ...FilePreviewTab[]]}
        activeKey={activeKey ?? undefined}
        locale={locale}
        labels={labels}
        onActiveKeyChange={onActiveKeyChange}
        onEditingChange={onEditingChange}
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
  onSaved?: FileBrowserProps['onSaved'],
): readonly FilePreviewTab[] {
  return tabs.map((tab) => {
    if (tab.kind === 'content') return tab;
    const editableTab =
      tab.kind === 'file'
        ? {
            ...tab,
            editing: readOnly
              ? undefined
              : withSaveNotification(tab.editing, tab.source.path, onSaved),
            levels: tab.levels?.map((level) => ({
              ...level,
              editing: readOnly
                ? undefined
                : withSaveNotification(
                    level.editing,
                    level.source.path,
                    onSaved,
                  ),
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

function withSaveNotification(
  editing: FilePreviewEditing | undefined,
  uri: string | undefined,
  onSaved: FileBrowserProps['onSaved'],
) {
  if (!editing || !uri || !onSaved) return editing;
  return {
    ...editing,
    onSave: (value: string) => {
      editing.onSave?.(value);
      onSaved(uri);
    },
  };
}
