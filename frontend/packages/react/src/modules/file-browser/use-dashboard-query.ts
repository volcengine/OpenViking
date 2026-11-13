import { useEffect, useMemo, useState } from 'react';
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
  load: (client: OvClient, options: OvRequestOptions) => Promise<T>,
  requestOptions?: OvRequestOptions,
  enabled = true,
  refreshGeneration = 0,
): DashboardQuery<T> {
  const client = useOvClient();
  const inheritedSignal = useOvSignal();
  const { baseURL, apiKey, accountId, userId, resourceId, signal } =
    requestOptions ?? {};
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState<{
    owner: unknown[];
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
    void load(client, options).then(
      (data) => {
        if (!options.signal.aborted) setState({ owner, data, error: null });
      },
      (cause: unknown) => {
        if (!options.signal.aborted)
          setState({
            owner,
            error: cause instanceof Error ? cause : new Error(String(cause)),
          });
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
  ]);
  return {
    data: current ? state.data : undefined,
    loading: enabled && !current,
    error: current ? state.error : null,
    retry: () => setGeneration((value) => value + 1),
  };
}
