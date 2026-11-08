import { lazy, Suspense, type Ref } from 'react';

import type { VikingBotComposerProps, VikingBotComposerRef } from './types.js';

/**
 * 懒加载的输入区：Ark 的 Sender 只在真正渲染输入区时下载。
 *
 * 静态导入会把 Ark 及其浏览器专用依赖拉进包的顶层导入链，破坏服务端渲染
 * 与无 `window` 环境下的模块解析，因此用动态导入隔开。
 *
 * Lazy composer: Ark's Sender downloads only when the composer actually renders.
 * A static import would pull Ark and its browser-only dependencies into the
 * package's top-level import chain, breaking server rendering and module
 * resolution without `window`.
 */
const View = lazy(async () => {
  const { VikingBotComposerView } = await import('./viking-bot-composer.js');
  return { default: VikingBotComposerView };
});

/** 输入区。The composer. */
export function VikingBotComposer({
  ref,
  ...props
}: VikingBotComposerProps & { ref?: Ref<VikingBotComposerRef> }) {
  return (
    <Suspense
      fallback={
        <div className="ov-viking-bot__composer ov-viking-bot__composer--loading" />
      }
    >
      <View {...props} ref={ref} />
    </Suspense>
  );
}
