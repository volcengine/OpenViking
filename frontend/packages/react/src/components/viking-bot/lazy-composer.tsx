import { forwardRef, lazy, Suspense } from 'react';

import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/**
 * 懒加载的输入区：Ark 的 Sender 只在真正渲染输入区时下载。
 *
 * 静态导入会把 Ark 及其浏览器专用依赖拉进包的顶层导入链，破坏服务端渲染
 * 与无 `window` 环境下的模块解析，因此用动态导入隔开。
 *
 * 外层必须继续用 forwardRef：包声明支持 React ^18.2.0，React 18 会截走 ref
 * 而不把它当作普通 prop 传入，改用 ref-as-prop 会让 React 18 下的 ref 丢失。
 *
 * Lazy composer: Ark's Sender downloads only when the composer actually renders.
 * A static import would pull Ark and its browser-only dependencies into the
 * package's top-level import chain, breaking server rendering and module
 * resolution without `window`.
 *
 * The outer component keeps `forwardRef`: the package declares React ^18.2.0, and
 * React 18 intercepts `ref` instead of passing it as a prop, so the ref-as-prop
 * form would silently drop the ref there.
 */
const View = lazy(async () => {
  const { VikingBotComposerView } = await import('./viking-bot-composer.js');
  return { default: VikingBotComposerView };
});

/** 输入区。The composer. */
export const VikingBotComposer = forwardRef<
  VikingBotComposerRef,
  VikingBotComposerProps
>(function VikingBotComposer(props, ref) {
  return (
    <Suspense
      fallback={
        <div className="ov-viking-bot__composer ov-viking-bot__composer--loading">
          {props.disabled && props.disabledReason ? (
            <p className="ov-viking-bot__composer-hint" role="status">
              {props.disabledReason}
            </p>
          ) : null}
        </div>
      }
    >
      <View {...props} ref={ref} />
    </Suspense>
  );
});
