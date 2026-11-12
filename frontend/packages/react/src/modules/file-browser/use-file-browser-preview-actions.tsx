import { useState } from 'react';
import type { RefObject } from 'react';
import { useOvClient } from '../../client/provider.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvRequestOptions } from '../../client/types.js';
import type { Locale } from '../../shared/locale.js';
import { FileBrowserDeleteDialog } from './file-browser-delete-dialog.js';
import { FileBrowserNativeDialog } from './file-browser-native-dialog.js';
import { PreviewMutationError } from './preview-query-state.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import { previewPolicy } from './preview-policy.js';
import { defaultPreviewActions } from './preview-default-actions.js';
import { downloadPreview } from './preview-native.js';
import { isBinaryFile } from './fs-file-kind.js';
import { scopeKeyOf } from './fs-scope.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import type { FileBrowserPreviewActions } from './preview-types.js';

export function useFileBrowserPreviewActions({
  uri,
  rootUri,
  isFile,
  enabled,
  dashboard,
  canWrite,
  canManage,
  options,
  locale,
  text,
  requestOptions,
  triggerRef,
  onDeleted,
  onUpdated,
}: {
  uri: string | null;
  rootUri: string;
  isFile: boolean;
  enabled: boolean;
  dashboard?: FileBrowserDashboard;
  canWrite: boolean;
  canManage: boolean;
  options?: FileBrowserPreviewActions;
  locale: Locale;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  triggerRef: RefObject<HTMLElement | null>;
  onDeleted: (uri: string) => void;
  onUpdated: () => void;
}) {
  const client = useOvClient();
  const key = scopeKeyOf(client, requestOptions, `${rootUri}:${uri}`);
  const policy = previewPolicy({
    uri: uri ?? '',
    rootUri,
    isFile,
    canWrite,
    canManage,
    dashboard,
  });
  const [opened, setOpened] = useState<{
    key: string;
    kind: 'delete' | 'history' | 'importPack' | 'configure';
    uri: string;
  }>();
  const deletion = usePreviewMutation(`${rootUri}:${uri}`, requestOptions);
  const transfer = usePreviewMutation(`${rootUri}:${uri}`, requestOptions);
  const canDelete = Boolean(client && enabled && policy.delete);
  const show = (kind: NonNullable<typeof opened>['kind']) => {
    if (!uri) return;
    setOpened({
      key,
      kind,
      uri: kind === 'configure' ? (dashboard?.targetUri ?? uri) : uri,
    });
  };
  const deleteFile = () => {
    if (canDelete) {
      deletion.reset();
      show('delete');
    }
  };
  const confirm = () => {
    if (!canDelete || !uri) return;
    void deletion.run(
      (api, overrides) =>
        api.request({
          method: 'DELETE',
          path: OV_PATHS.fsRm,
          query: { uri, wait: true },
          options: overrides,
        }),
      () => {
        setOpened(undefined);
        onDeleted(uri);
      },
    );
  };
  const actionItems =
    client && uri && enabled
      ? defaultPreviewActions({
          policy,
          options,
          text,
          canDelete,
          show,
          deleteFile,
          download: (pack) =>
            transfer.run((api, overrides) =>
              downloadPreview(api, uri, pack, overrides),
            ),
        })
      : [];
  const kind = opened?.key === key ? opened.kind : undefined;
  return {
    policy,
    actionItems,
    context: { canDelete, deleteFile },
    dialog: (
      <>
        <PreviewMutationError error={transfer.error} text={text.workflow} />
        {uri && kind === 'delete' ? (
          <FileBrowserDeleteDialog
            key={key}
            open={canDelete}
            uri={uri}
            busy={deletion.pending}
            error={deletion.error}
            text={text}
            locale={locale}
            triggerRef={triggerRef}
            onClose={() => setOpened(undefined)}
            onConfirm={confirm}
          />
        ) : null}
        {uri && kind && kind !== 'delete' ? (
          <FileBrowserNativeDialog
            key={`${key}:${kind}`}
            kind={kind}
            uri={opened?.uri ?? uri}
            canWrite={canWrite}
            versioned={policy.versionedFile}
            showContent={isFile && !isBinaryFile(uri)}
            text={text}
            locale={locale}
            requestOptions={requestOptions}
            triggerRef={triggerRef}
            onClose={() => setOpened(undefined)}
            onUpdated={onUpdated}
          />
        ) : null}
      </>
    ),
  };
}
