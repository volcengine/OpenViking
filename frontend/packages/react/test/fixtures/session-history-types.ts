import type { UseInfiniteQueryResult } from '@tanstack/react-query';
import type { VikingBotSessions } from '@openviking/react/components/viking-bot';

declare const fetchNextPage: UseInfiniteQueryResult['fetchNextPage'];

export const history: VikingBotSessions = {
  items: [],
  onNew() {},
  onSelect() {},
  onLoadMore: fetchNextPage,
};

export const cancellableHistory: VikingBotSessions = {
  ...history,
  onLoadMoreWithSignal: (signal) => signal.throwIfAborted(),
};
