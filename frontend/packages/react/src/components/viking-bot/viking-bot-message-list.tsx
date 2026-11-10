import { lazy, Suspense } from 'react';

import { DEFAULT_LABELS } from './labels.js';
import { toRenderItems } from './ark-adapter.js';
import type { VikingBotMessageListProps } from './types.js';

/**
 * 懒加载的消息列表：Ark 只在真正渲染消息时下载。
 *
 * 直接静态导入会把 Ark 及其浏览器专用依赖拉进包的顶层导入链，破坏服务端
 * 渲染与无 `window` 环境下的模块解析，因此这里用动态导入隔开——与
 * FilePreview 的正文入口同一做法。
 *
 * Lazy message list: Ark downloads only when messages actually render. A static
 * import would pull Ark and its browser-only dependencies into the package's
 * top-level import chain, breaking server rendering and module resolution without
 * `window` — the same boundary FilePreview uses for its body.
 */
const List = lazy(async () => {
  const { VikingBotMessageListView } =
    await import('./ark-message-list-view.js');
  return { default: VikingBotMessageListView };
});

/** 加载态的占位高度，避免列表出现时页面跳动。 */
function ListSkeleton() {
  return <div className="ov-viking-bot__list ov-viking-bot__list--loading" />;
}

/** 消息列表。The message list. */
export function VikingBotMessageList(props: VikingBotMessageListProps) {
  const labels = { ...DEFAULT_LABELS, ...props.labels };
  const items = toRenderItems(props.messages, props.streaming);

  if (items.length === 0) {
    return <div className="ov-viking-bot__empty">{labels.empty}</div>;
  }

  return (
    <Suspense fallback={<ListSkeleton />}>
      <List {...props} />
    </Suspense>
  );
}
