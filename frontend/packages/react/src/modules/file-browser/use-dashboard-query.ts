import { tagModel } from '../../shared/monitoring/data.js';
import { bindingOf } from '../../shared/monitoring/client.js';
import {
  beginWorkflow,
  type Workflow,
} from '../../shared/monitoring/workflow.js';
import { useWorkflowCommit } from '../../shared/monitoring/use-workflow-commit.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';

export interface DashboardQuery<T> {
  data?: T;
  loading: boolean;
  error: Error | null;
  retry: () => void;
}

/** Instance-local, cancellable queries. A changed identity never exposes old data. */
export function useDashboardQuery<T>(
  load: (
    client: OvClient,
    options: OvRequestOptions & { signal: AbortSignal },
  ) => Promise<T>,
  requestOptions?: OvRequestOptions,
  enabled = true,
  refreshGeneration = 0,
  monitoringTarget?:
    | 'file_browser_wiki_dashboard_load'
    | 'file_browser_experience_dashboard_load',
  empty?: (data: T) => boolean,
  opaque = false,
): DashboardQuery<T> {
  const client = useOvClient();
  const inheritedSignal = useOvSignal();
  const { baseURL, apiKey, accountId, userId, resourceId, signal } =
    requestOptions ?? {};
  const [generation, setGeneration] = useState(0);
  const retryCause = useRef<
    { owner: unknown[]; error: WeakRef<Error> } | undefined
  >(undefined);
  const [state, setState] = useState<{
    owner: unknown[];
    operation?: Workflow<
      | 'file_browser_wiki_dashboard_load'
      | 'file_browser_experience_dashboard_load'
    >;
    data?: T;
    error: Error | null;
  }>({ owner: [], error: null });
  const owner = useMemo(
    () => [
      client,
      load,
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
      inheritedSignal,
      generation,
      enabled,
      refreshGeneration,
    ],
    [
      client,
      load,
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
      inheritedSignal,
      generation,
      enabled,
      refreshGeneration,
    ],
  );
  const current = owner.every((value, i) => value === state.owner[i]);

  // useEffect 必要性：按资源查询及身份配置同步远端数据，不能由点击覆盖受控 URI 导航。
  // 依赖变化和卸载中止请求，并忽略不遵守 signal 的传输层迟到响应。
  useEffect(() => {
    const prior = retryCause.current;
    retryCause.current = undefined;
    if (!enabled) return;
    if (!client) {
      setState({ owner, error: new Error('OpenVikingProvider is required') });
      return;
    }
    const controller = new AbortController();
    const signals = [controller.signal, signal, inheritedSignal].filter(
      (value): value is AbortSignal => Boolean(value),
    );
    const options = {
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal: AbortSignal.any(signals),
    };
    const operation = monitoringTarget
      ? beginWorkflow(client, monitoringTarget, options.signal, {
          retryError: prior?.owner.every(
            (value, index) => index === 9 || value === owner[index],
          )
            ? prior.error.deref()
            : undefined,
        })
      : undefined;
    if (opaque) operation?.span.gap('opaque-host-service');
    void load(operation?.client(client) ?? client, options).then(
      (data) => {
        if (!options.signal.aborted)
          setState({ owner, data, error: null, operation });
      },
      (cause: unknown) => {
        if (!options.signal.aborted) {
          operation?.fail(cause, 'protocol', options.signal);
          setState({
            owner,
            error: cause instanceof Error ? cause : new Error(String(cause)),
          });
        }
      },
    );
    return () => controller.abort();
  }, [
    client,
    load,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal,
    inheritedSignal,
    generation,
    enabled,
    owner,
    monitoringTarget,
    opaque,
  ]);
  if (current) tagModel(bindingOf(client), state.data, state.operation?.span);
  useWorkflowCommit(
    current ? state.operation : undefined,
    current && state.data !== undefined && !state.error,
    () => ({ empty: empty?.(state.data!) ?? false }),
  );
  return {
    data: current ? state.data : undefined,
    loading: enabled && !current,
    error: current ? state.error : null,
    retry: () => {
      const binding = bindingOf(client);
      retryCause.current =
        current &&
        state.error &&
        binding?.active() &&
        binding.controller.options
          ? { owner: state.owner, error: new WeakRef(state.error) }
          : undefined;
      setGeneration((value) => value + 1);
    },
  };
}
