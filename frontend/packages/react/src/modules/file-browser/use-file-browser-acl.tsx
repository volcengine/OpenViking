import { Button } from '../../shared/components/button/button.js';
import { useEffect, useRef, useState } from 'react';
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
import { scopeKeyOf } from './fs-scope.js';

export function useFileBrowserAcl({
  uri,
  acl,
  locale,
  labels,
  requestOptions,
  refresh,
}: {
  uri: string | null;
  acl?: boolean | FileBrowserAclOptions;
  locale: Locale;
  labels?: FileBrowserAclLabels;
  requestOptions?: OvRequestOptions;
  refresh: () => Promise<void>;
}) {
  const client = useOvClient();
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
  const latest = useRef({ client, requestOptions, options, refresh });
  useEffect(() => {
    latest.current = { client, requestOptions, options, refresh };
  });
  const enabled = Boolean(acl) && Boolean(client) && isAclTarget(uri);
  const resolver = options?.resolvePermission;
  useEffect(() => {
    if (!enabled || !uri) return;
    const controller = new AbortController();
    const current = latest.current;
    if (!current.client) return;
    const composed = signal
      ? AbortSignal.any([controller.signal, signal])
      : controller.signal;
    const context = {
      client: current.client,
      requestOptions: { ...current.requestOptions, signal: composed },
    };
    const load = resolver
      ? resolver(uri, context)
      : readPermission(context.client, uri, context.requestOptions);
    void load.then(
      (level) => {
        if (!composed.aborted) setPermission({ key, level });
      },
      () => {
        if (!composed.aborted) setPermission({ key, error: true });
      },
    );
    return () => controller.abort();
  }, [enabled, key, uri, resolver, signal, generation]);
  const level =
    enabled && permission?.key === key ? permission.level : undefined;
  const text = aclLabels(locale, labels);
  return {
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
        <AclTag level={level} text={text} onManage={() => setOpened(key)} />
      ) : null,
    actions:
      level === 'manage' ? (
        <Button
          size="small"
          type="default"
          ref={triggerRef}
          htmlType="button"
          className="ov-file-browser-acl__manage"
          onClick={() => setOpened(key)}
        >
          {text.manage}
        </Button>
      ) : null,
    drawer:
      enabled && uri && client && level === 'manage' ? (
        <AclModal
          key={key}
          uri={uri}
          open={opened === key}
          text={text}
          locale={locale}
          triggerRef={triggerRef}
          context={{ client, requestOptions: { ...requestOptions, signal } }}
          accountId={identity.accountId}
          options={options}
          onClose={() => setOpened(undefined)}
          onUpdated={(report) => {
            // Keep the Modal mounted while the manager probe refreshes; a downgrade
            // will remove the panel as soon as the authoritative check returns.
            setGeneration((value) => value + 1);
            void latest.current.refresh();
            try {
              latest.current.options?.onChange?.(report);
            } catch {
              // A host notification cannot undo a successful server mutation.
              return;
            }
          }}
        />
      ) : null,
  };
}
