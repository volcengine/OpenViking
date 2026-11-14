import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { useLocale } from '../../shared/locale-context.js';
import { LazyContent } from '../../shared/lazy-content.js';

import { resolveVikingBotLabels } from './labels.js';
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
const load = async () => {
  const { VikingBotMessageListView } =
    await import('./ark-message-list-view.js');
  return { default: VikingBotMessageListView };
};

/** 加载态的占位高度，避免列表出现时页面跳动。 */
function ListSkeleton() {
  return <div className="ov-viking-bot__list ov-viking-bot__list--loading" />;
}

/** 消息列表。The message list. */
function VikingBotMessageListContent(props: VikingBotMessageListProps) {
  const locale = useLocale(props.locale);
  const labels = resolveVikingBotLabels(locale, props.labels);
  const items = toRenderItems(props.messages, props.streaming);

  if (items.length === 0) {
    return (
      <div className="ov-viking-bot__empty">
        <Empty illustration="conversation" title={labels.empty} />
      </div>
    );
  }

  return (
    <LazyContent
      load={load}
      contentProps={{ ...props, locale }}
      fallback={<ListSkeleton />}
    />
  );
}

export function VikingBotMessageList(props: VikingBotMessageListProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).list}
      locale={props.locale}
      resetKeys={[props.listKey]}
    >
      <VikingBotMessageListContent {...contentProps} />
    </ErrorBoundary>
  );
}
