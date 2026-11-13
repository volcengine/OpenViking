import { useRenderErrors } from '../labels.js';
import { ErrorBoundary } from '../../../shared/error-boundary/error-boundary.js';
import { LazyContent } from '../../../shared/lazy-content.js';

import type { VikingBotInteractionProps } from './types.js';

/**
 * 懒加载的交互区：Ark 只在真正渲染表单时下载。
 *
 * 静态导入会把 Ark 及其浏览器专用依赖拉进包的顶层导入链，破坏服务端渲染
 * 与无 `window` 环境下的模块解析，因此用动态导入隔开——与消息列表同一做法。
 *
 * Lazy interaction area: Ark downloads only when a form actually renders. A
 * static import would pull Ark and its browser-only dependencies into the
 * package's top-level import chain, breaking server rendering and module
 * resolution without `window` — the same boundary the message list uses.
 */
const load = async () => {
  const { VikingBotInteractionView } =
    await import('./viking-bot-interaction.js');
  return { default: VikingBotInteractionView };
};

/** 待确认交互区。The pending-interaction area. */
function VikingBotInteractionContent(props: VikingBotInteractionProps) {
  return (
    <LazyContent
      load={load}
      contentProps={props}
      fallback={
        <section className="ov-viking-bot__interaction ov-viking-bot__interaction--loading" />
      }
    />
  );
}

export function VikingBotInteraction(props: VikingBotInteractionProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).interaction}
      locale={props.locale}
      resetKeys={[props.requestKey, props.request.kind, props.index]}
    >
      <VikingBotInteractionContent {...contentProps} />
    </ErrorBoundary>
  );
}
