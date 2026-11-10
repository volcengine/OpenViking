import { after } from 'node:test';
import { JSDOM } from 'jsdom';
import { act, createElement, startTransition } from 'react';

/**
 * 模块层测试的最小 DOM 与渲染环境。模块 Hook 用 effect 取数，静态渲染测不到，
 * 因此这里挂一棵真实组件树并暴露 `render` 等待提交。
 *
 * The minimal DOM and render environment for module tests. A module Hook loads
 * through effects, which static rendering cannot exercise, so a real tree is
 * mounted here and `render` waits for the commit.
 */
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  pretendToBeVisual: true,
});
for (const name of ['window', 'document', 'Node', 'Element', 'HTMLElement']) {
  globalThis[name] = name === 'window' ? dom.window : dom.window[name];
}
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom 没有 ResizeObserver，而订阅容器宽度的 Hook 依赖它；测试里给一个只记录
// 目标、从不回调的替身即可——测试断言的是渲染结果，不是尺寸变化。
//
// jsdom has no ResizeObserver and the container-width Hook needs one; the stub here
// records its target and never calls back, which is enough because these tests assert
// on rendered output rather than on size changes.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
const { createRoot } = await import('react-dom/client');
after(() => dom.window.close());

/**
 * 让出若干次微任务与一个宏任务：effect 里的 await 链要在下一次断言前跑完。
 * Yields a few microtasks and one macrotask so an effect's await chain settles
 * before the next assertion.
 */
export async function settle(times = 3) {
  for (let index = 0; index < times; index++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

/**
 * 挂载一棵组件树并返回重新渲染与卸载。
 * Mounts a tree and returns a re-render and an unmount.
 */
export async function mount(element) {
  const container = document.createElement('div');
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
  await settle();
  return {
    container,
    async render(next) {
      await act(async () => {
        root.render(next);
      });
      await settle();
    },
    /**
     * 在 `startTransition` 里重新渲染，用来制造「这次渲染可能被放弃」的场景。
     * Re-renders inside `startTransition`, for the "this render may be abandoned" case.
     */
    async renderTransition(next) {
      await act(async () => {
        startTransition(() => root.render(next));
        await new Promise((resolve) => setTimeout(resolve, 40));
      });
    },
    async unmount() {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    },
  };
}

/**
 * 记录每次请求并交给 handler 作答的 fetch。
 * A fetch that records every request and lets a handler answer it.
 */
export function recordingFetch(handler) {
  const calls = [];
  const impl = (url, init) => {
    const parsed = new URL(String(url), 'https://ov.test');
    const call = {
      path: parsed.pathname,
      uri: parsed.searchParams.get('uri') ?? '',
      // 其余查询参数原样留着：有的用例要按 `limit` 之类的入参区分应答。
      // The remaining query parameters stay available: some cases answer differently
      // depending on an argument such as `limit`.
      query: Object.fromEntries(parsed.searchParams),
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
      init,
    };
    calls.push(call);
    return handler(call);
  };
  impl.calls = calls;
  impl.countOf = (path) => calls.filter((call) => call.path === path).length;
  return impl;
}

/** 一次成功的 JSON 应答。A successful JSON response. */
export function ok(result, hasMore = false) {
  return Promise.resolve(
    new Response(JSON.stringify({ status: 'ok', result, has_more: hasMore }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
}

/** 一个延迟作答的应答，用来模拟慢请求。A delayed response, to simulate a slow call. */
export function deferred() {
  let resolve;
  const promise = new Promise((settleResolve) => {
    resolve = settleResolve;
  });
  return { promise, resolve };
}
