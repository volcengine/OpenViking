import type { FilePreviewActions } from '../../components/file-preview/index.js';
import { useRef } from 'react';
import type { Locale } from '../../shared/locale.js';
import type { OvRequestOptions } from '../../client/types.js';
import { FileBrowserDetail } from './file-browser-detail.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import { FileBrowserFailure } from './file-browser-failure.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import type { useFileBrowserAcl } from './use-file-browser-acl.js';
import { useFileBrowserPreviewActions } from './use-file-browser-preview-actions.js';
import type {
  FileBrowserApi,
  FileBrowserLabels,
  FileBrowserPreviewActions,
  FileBrowserPreviewSlots,
} from './types.js';

export function FileBrowserPreviewPane({
  browser,
  permissions,
  rootUri,
  locale,
  text,
  previewLabels,
  readOnly,
  previewActions,
  previewSlots,
  requestOptions,
}: {
  browser: FileBrowserApi;
  permissions: ReturnType<typeof useFileBrowserAcl>;
  rootUri: string;
  locale: Locale;
  text: ResolvedLabels;
  previewLabels: FileBrowserLabels['preview'];
  readOnly?: boolean;
  previewActions?: FileBrowserPreviewActions;
  previewSlots?: FileBrowserPreviewSlots;
  requestOptions?: OvRequestOptions;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tab = browser.previewTabs?.find(
    (candidate) => candidate.key === browser.previewKey,
  );
  const controls = useFileBrowserPreviewActions({
    uri: browser.currentUri,
    rootUri,
    isFile: tab?.kind === 'file',
    canWrite: !readOnly && permissions.canEdit,
    canManage: !readOnly && permissions.canDelete,
    options: previewActions,
    locale,
    text,
    requestOptions,
    triggerRef: ref,
    onDeleted: (uri) => {
      // Success is final; subsequent navigation/refresh errors have their own UI.
      const parent = uri.slice(0, uri.lastIndexOf('/')) || rootUri;
      void browser.actions.openUri(parent).catch(() => {});
      void browser.actions.refresh().catch(() => {});
    },
  });
  const actions = previewSlots?.actions;
  const slots = {
    ...previewSlots,
    actions:
      typeof actions === 'function'
        ? (context: FilePreviewActions) =>
            actions({ ...context, ...controls.context })
        : actions,
    menuItems: [...controls.menuItems, ...(previewSlots?.menuItems ?? [])],
  };
  return (
    <div ref={ref} className="ov-file-browser__preview-pane">
      {browser.previewTabs?.length ? (
        <FileBrowserDetail
          tabs={browser.previewTabs}
          activeKey={browser.previewKey}
          locale={locale}
          labels={previewLabels}
          footer={browser.previewFooter}
          titleExtra={permissions.titleExtra}
          readOnly={
            readOnly || !permissions.canEdit || previewActions?.edit === false
          }
          toolbar={{
            copyContent: previewActions?.copyContent,
            edit: previewActions?.edit,
          }}
          previewSlots={slots}
          onActiveKeyChange={browser.actions.setPreviewKey}
        />
      ) : browser.currentUri && browser.previewError ? (
        <div className="ov-file-browser__detail">
          <FileBrowserFailure
            message={text.error}
            detail={browser.previewError.message}
            retryLabel={text.retry}
            onRetry={() => void browser.actions.refresh()}
          />
        </div>
      ) : (
        <div className="ov-file-browser__detail">
          <FileBrowserEmpty label={text.empty} />
        </div>
      )}
      {controls.dialog}
    </div>
  );
}
