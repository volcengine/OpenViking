import { Button } from '../../shared/primitives/button/button.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useOvClient,
  useOvIdentity,
  useOvSignal,
} from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import type { Locale } from '../../shared/locale.js';
import type {
  FileBrowserAclLabels,
  FileBrowserAclLevel,
  FileBrowserAclOptions,
} from './acl-types.js';
import { AclModal } from './acl/acl-modal.js';
import { AclTag } from './acl/acl-tag.js';
import { aclLabels } from './acl/acl-labels.js';
import { readPermission } from './acl/acl-client.js';
import { isAclTarget } from './acl/acl-model.js';
import { scopeKeyOf } from '../../shared/request/fs-scope.js';

export function useFileBrowserAcl({
  uri,
  acl,
  readOnly,
  locale,
  labels,
  requestOptions,
  refresh,
}: {
  uri: string | null;
  acl?: boolean | FileBrowserAclOptions;
  readOnly?: boolean;
  locale: Locale;
  labels?: FileBrowserAclLabels;
  requestOptions?: OvRequestOptions;
  refresh: () => Promise<void>;
}) {
  const client = useOvClient();
  const permissionClient = useOvClient('local');
  const identity = useOvIdentity(requestOptions);
  const inheritedSignal = useOvSignal();
  const signal = requestOptions?.signal ?? inheritedSignal;
  const options = typeof acl === 'object' ? acl : undefined;
  const key = scopeKeyOf(client, requestOptions, uri ?? undefined);
  const [permission, setPermission] = useState<{
    key: string;
    level?: FileBrowserAclLevel;
    error?: boolean;
  }>();
  const [opened, setOpened] = useState<string>();
  const [generation, setGeneration] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { baseURL, apiKey, accountId, userId, resourceId } =
    requestOptions ?? {};
  // Equal configuration objects must not restart ACL requests or clear a pending
  // confirmation when the host rerenders to change its interface language.
  const context = useMemo(
    () =>
      client
        ? {
            client,
            requestOptions: {
              baseURL,
              apiKey,
              accountId,
              userId,
              resourceId,
              signal,
            },
          }
        : undefined,
    [client, baseURL, apiKey, accountId, userId, resourceId, signal],
  );
  const aclEnabled = Boolean(acl);
  const enabled = aclEnabled && Boolean(client) && isAclTarget(uri);
  const resolver = options?.resolvePermission;
  const loadPermission = useCallback(
    async (target: string, requestSignal: AbortSignal) => {
      if (!context || !permissionClient) return undefined;
      const request = {
        client: permissionClient,
        requestOptions: {
          ...context.requestOptions,
          signal: signal
            ? AbortSignal.any([requestSignal, signal])
            : requestSignal,
        },
      };
      request.requestOptions.signal.throwIfAborted();
      const level = await (resolver
        ? resolver(target, request)
        : readPermission(request.client, target, request.requestOptions));
      request.requestOptions.signal.throwIfAborted();
      return level;
    },
    [context, permissionClient, resolver, signal],
  );
  const checkWrite = useCallback(
    async (target: string, requestSignal: AbortSignal) => {
      if (readOnly) return false;
      if (
        !aclEnabled ||
        (target !== 'viking://resources' && !isAclTarget(target))
      )
        return true;
      const level = await loadPermission(target, requestSignal);
      return level === 'write' || level === 'manage';
    },
    [aclEnabled, loadPermission, readOnly],
  );
  // Sync the active resource with the permission service, including navigation
  // and account changes that do not originate in a click. Abort on scope change
  // or unmount; aborted responses never publish a permission for the next scope.
  useEffect(() => {
    if (!enabled || !uri) return;
    const controller = new AbortController();
    void loadPermission(uri, controller.signal).then(
      (level) => {
        if (!controller.signal.aborted && !signal?.aborted)
          setPermission({ key, level });
      },
      () => {
        if (!controller.signal.aborted && !signal?.aborted)
          setPermission({ key, error: true });
      },
    );
    return () => controller.abort();
  }, [enabled, key, uri, signal, generation, loadPermission]);
  const level =
    enabled && permission?.key === key ? permission.level : undefined;
  const text = aclLabels(locale, labels);
  return {
    checkWrite,
    canDelete: !enabled || level === 'manage',
    canEdit: !enabled || level === 'write' || level === 'manage',
    titleExtra:
      enabled && permission?.key === key && permission.error ? (
        <span className="ov-file-browser-acl__lookup-error" role="status">
          {text.loadFailed}
          <Button
            size="small"
            type="default"
            htmlType="button"
            onClick={() => setGeneration((value) => value + 1)}
          >
            {text.retry}
          </Button>
        </span>
      ) : level ? (
        <AclTag
          level={level}
          text={text}
          triggerRef={triggerRef}
          onManage={() => setOpened(key)}
        />
      ) : null,
    drawer:
      enabled && uri && context && level === 'manage' ? (
        <AclModal
          key={key}
          uri={uri}
          open={opened === key}
          text={text}
          locale={locale}
          triggerRef={triggerRef}
          context={context}
          accountId={identity.accountId}
          userId={identity.userId}
          options={options}
          onClose={() => setOpened(undefined)}
          onUpdated={(report) => {
            // Keep the Modal mounted while the manager probe refreshes; a downgrade
            // will remove the panel as soon as the authoritative check returns.
            setGeneration((value) => value + 1);
            void refresh();
            try {
              options?.onChange?.(report);
            } catch {
              // A host notification cannot undo a successful server mutation.
              return;
            }
          }}
        />
      ) : null,
  };
}
