import { useEffect, useRef } from 'react';

import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf, type RequestScope } from './fs-scope.js';

/**
 * 请求作用域：客户端实例、根目录与逐请求覆盖的身份字段。
 *
 * 换 Provider、换 `requestOptions.accountId`、换 `rootUri`，上一个作用域里还没回来
 * 的请求都必须作废，否则 A 的响应会在 B 的树上落地。
 *
 * 中止是**传输副作用**，只在提交之后做，不在渲染里做：React 可以放弃一次渲染
 * （`startTransition` + Suspense 就会），渲染里中止会把仍在屏幕上的那棵树的在途
 * 请求杀掉。渲染里只记下「期望的作用域键」，请求回来时用它判断结果是否过期。
 *
 * The request scope: the client instance, the root and the per-request identity
 * overrides.
 *
 * Switching the Provider, `requestOptions.accountId` or `rootUri` must invalidate the
 * requests still in flight, or A's response lands on B's tree.
 *
 * Aborting is a **transport side effect** and happens only after commit, never during
 * render: React may abandon a render (`startTransition` with Suspense does), and
 * aborting there would kill the in-flight requests of the tree still on screen. Render
 * only records the *desired* scope key, which a returning request compares against.
 */
export interface RequestScopeHandle {
  /** 渲染中记录的作用域键，请求回来时与它比较判断是否过期。 */
  /** The scope key recorded during render; a returning request compares against it. */
  desiredKey: () => string;
  /** 当前已提交作用域的键与控制器。Key and controller of the committed scope. */
  current: () => RequestScope;
}

export function useRequestScope(
  client: OvClient | null,
  requestOptions: OvRequestOptions | undefined,
  rootUri: string,
): RequestScopeHandle {
  const scopeKey = scopeKeyOf(client, requestOptions, rootUri);
  const desiredKeyRef = useRef(scopeKey);
  desiredKeyRef.current = scopeKey;
  const scopeRef = useRef<RequestScope>({
    key: scopeKey,
    controller: new AbortController(),
  });
  const handleRef = useRef<RequestScopeHandle>({
    desiredKey: () => desiredKeyRef.current,
    current: () => scopeRef.current,
  });

  // useEffect 必要性：作用域变化与卸载都要中止传输层上仍在进行的请求（与网络这个外
  // 部系统同步）。中止只能在提交之后做；两种情况下都换新控制器（而不是留着已中止的
  // 那个），否则 Strict Mode 的 setup → cleanup → setup 之后每次操作都会在入口直接
  // 返回，树再也不发请求。
  //
  // Why an effect: a scope change and an unmount must abort requests still running in
  // the transport (syncing with the network). Aborting can only happen after commit, and
  // both install a fresh controller — leaving the aborted one would make every operation
  // return at its entry after Strict Mode's setup → cleanup → setup.
  useEffect(() => {
    const current = scopeRef.current;
    if (current.key === scopeKey) return;
    current.controller.abort();
    scopeRef.current = { key: scopeKey, controller: new AbortController() };
  }, [scopeKey]);

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
