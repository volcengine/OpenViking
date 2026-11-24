import { useEffect, useLayoutEffect, useRef } from 'react';

import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf, type RequestScope } from './fs-scope.js';

/**
 * 请求作用域：客户端实例、根目录、逐请求身份与局部视图范围。
 *
 * 换 Provider、换 `requestOptions.accountId`、换 `rootUri`，上一个作用域里还没回来
 * 的请求都必须作废，否则 A 的响应会在 B 的树上落地。
 *
 * 中止是**传输副作用**，只在提交之后做，不在渲染里做：React 可以放弃一次渲染
 * （`startTransition` + Suspense 就会），渲染里中止会把仍在屏幕上的那棵树的在途
 * 请求杀掉。渲染只计算作用域键，由布局提交发布它，使异步结果及时识别旧作用域。
 *
 * The request scope: the client instance, root, per-request identity overrides
 * and local view scope.
 *
 * Switching the Provider, `requestOptions.accountId` or `rootUri` must invalidate the
 * requests still in flight, or A's response lands on B's tree.
 *
 * Aborting is a **transport side effect** and happens only after commit, never during
 * render: React may abandon a render (`startTransition` with Suspense does), and
 * aborting there would kill the in-flight requests of the tree still on screen. Render
 * only computes the desired key; layout commit publishes it before async work resumes.
 */
export interface RequestScopeHandle {
  /**
   * 当前**已提交**作用域的键与控制器。
   *
   * 它是判断过期的唯一依据，因为只有提交才会改它：渲染期不写任何东西，被放弃的
   * 渲染因此不会让仍在屏幕上的那棵树的结果作废。
   *
   * The key and controller of the **committed** scope.
   *
   * It is the only basis for staleness because only a commit changes it: render writes
   * nothing, so an abandoned render cannot invalidate the results of the tree still on
   * screen.
   */
  current: () => RequestScope;
}

export function useRequestScope(
  client: OvClient | null,
  requestOptions: OvRequestOptions | undefined,
  rootUri: string,
  viewScopeKey?: string,
): RequestScopeHandle {
  const requestKey = scopeKeyOf(client, requestOptions, rootUri);
  const scopeKey =
    viewScopeKey === undefined
      ? requestKey
      : JSON.stringify([requestKey, viewScopeKey]);
  // 渲染期不写任何 ref：作用域身份只在下面的 layout effect 里发布，也就是提交之后。
  // `startTransition` + Suspense 放弃的那次渲染因此不会改动它。
  //
  // No ref is written during render: the scope identity is published in the layout effect
  // below, after commit, so a render abandoned by `startTransition` with Suspense
  // cannot change it.
  const scopeRef = useRef<RequestScope>({
    key: scopeKey,
    controller: new AbortController(),
  });
  const handleRef = useRef<RequestScopeHandle>({
    current: () => scopeRef.current,
  });

  // Commit network cancellation in the same layout phase as the directory session.
  // Passive effects leave a window for old responses to resume against the new session.
  // Render cannot abort requests because React may abandon it; key changes retire the
  // old scope here, before asynchronous continuations can run after the commit.
  useLayoutEffect(() => {
    const current = scopeRef.current;
    if (current.key === scopeKey) return;
    current.controller.abort();
    scopeRef.current = { key: scopeKey, controller: new AbortController() };
  }, [scopeKey]);

  // Passive cleanup retires transport on unmount and Strict Mode replay. Suspense
  // also cleans up layout effects when hiding a mounted tree, so using layout here
  // would abort navigation whose scope is unchanged. Replace the controller so
  // Strict Mode's next setup can accept new requests.
  useEffect(
    () => () => {
      scopeRef.current.controller.abort();
      scopeRef.current = {
        key: scopeRef.current.key,
        controller: new AbortController(),
      };
    },
    [],
  );

  // 返回值的引用必须稳定：调用方会把它放进 `useMemo` 的依赖，每次渲染都新建对象
  // 会让那些 memo 全部失效，进而让取数 effect 反复重跑——表现为页面卡死。
  //
  // The returned identity must be stable: callers put it in a `useMemo` dependency, and
  // a fresh object every render would void those memos and make the loading effects
  // re-run endlessly — which shows up as a frozen page.
  return handleRef.current;
}
