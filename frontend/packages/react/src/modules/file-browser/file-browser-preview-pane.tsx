import { useRenderErrors } from './file-browser-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import type { FilePreviewActions } from '../../components/file-preview/index.js';
import { useRef } from 'react';
import { previewActionContext } from './file-browser-preview-action-source.js';
import { isSystemUri } from './preview-policy.js';
import type { Locale } from '../../shared/locale.js';
import type { OvRequestOptions } from '../../client/types.js';
import { FileBrowserDetail } from './file-browser-detail.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import {
  FileBrowserPreviewFooter,
  previewModelNotice,
} from './file-browser-preview-footer.js';
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

function FileBrowserPreviewPaneContent({
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
  const tab =
    browser.previewTabs?.find(
      (candidate) => candidate.key === browser.previewKey,
    ) ?? browser.previewTabs?.[0];
  const systemDirectory = tab?.kind === 'directory' && isSystemUri(tab.key);
  const modelNotice = previewModelNotice(
    browser.currentUri,
    text.previewModelNotice,
  );
  const previewFooter =
    tab &&
    tab.kind !== 'content' &&
    !tab.loading &&
    tab.key === browser.currentUri &&
    (browser.previewFooter || browser.previewUpdatedAt || modelNotice) ? (
      <FileBrowserPreviewFooter
        count={browser.previewFooter}
        updatedAt={browser.previewUpdatedAt}
        modelNotice={modelNotice}
        locale={locale}
        text={text}
      />
    ) : undefined;
  const controls = useFileBrowserPreviewActions({
    uri: browser.currentUri,
    rootUri,
    isFile: tab?.kind === 'file',
    enabled: Boolean(tab && tab.kind !== 'content' && !tab.loading),
    dashboard: browser.previewDashboard,
    onUpdated: () => {
      void browser.actions.refresh().catch(() => {});
    },
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
  const getContext = (context: FilePreviewActions) =>
    previewActionContext(
      context,
      controls.context,
      controls.actionItems,
      {
        ...previewActions,
        copyContent:
          controls.policy.copyContent && previewActions?.copyContent !== false,
      },
      text,
    );
  const actionItems = previewSlots?.actionItems;
  const commandSource = (context: FilePreviewActions) => {
    const extended = getContext(context);
    return typeof actionItems === 'function'
      ? actionItems(extended)
      : (actionItems ?? extended.defaultActions);
  };
  const customActions = previewSlots?.actions;
  const slots = {
    ...previewSlots,
    actions:
      typeof customActions === 'function'
        ? (context: FilePreviewActions) => customActions(getContext(context))
        : customActions,
  };
  return (
    <div ref={ref} className="ov-file-browser__preview-pane">
      {browser.previewTabs?.length ? (
        <FileBrowserDetail
          tabs={browser.previewTabs}
          activeKey={browser.previewKey}
          locale={locale}
          labels={
            systemDirectory
              ? { ...previewLabels, fileEmpty: text.systemDirectory }
              : previewLabels
          }
          footer={previewFooter}
          titleExtra={permissions.titleExtra}
          readOnly={readOnly || !permissions.canEdit || !controls.policy.edit}
          toolbar={{
            copyPath: previewActions?.copyPath,
            viewSwitch:
              controls.policy.viewSwitch &&
              previewActions?.viewSwitch !== false,
            edit: previewActions?.edit,
          }}
          actions={commandSource}
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
          {browser.currentUri ? (
            <p className="ov-file-browser__hint" role="status">
              {text.loading}
            </p>
          ) : (
            <FileBrowserEmpty label={text.empty} />
          )}
        </div>
      )}
      {controls.dialog}
    </div>
  );
}

export function FileBrowserPreviewPane(
  props: Parameters<typeof FileBrowserPreviewPaneContent>[0],
) {
  return (
    <ErrorBoundary
      message={useRenderErrors(props.locale).preview}
      locale={props.locale}
      resetKeys={[props.browser.currentUri]}
    >
      <FileBrowserPreviewPaneContent {...props} />
    </ErrorBoundary>
  );
}
