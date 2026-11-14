import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { useLocale } from '../../shared/locale-context.js';
import { LazyContent } from '../../shared/lazy-content.js';

import { resolveVikingBotLabels } from './labels.js';
import { toRenderItems } from './ark-adapter.js';
import type { VikingBotMessageListProps } from './types.js';

/** 重型视图按需加载，减少控制台首屏下载与解析开销。 */
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
      monitoringTarget="viking_bot_message_list_load"
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
      monitoringRegion="message-list"
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).list}
      locale={props.locale}
      resetKeys={[props.listKey]}
    >
      <VikingBotMessageListContent {...contentProps} />
    </ErrorBoundary>
  );
}
