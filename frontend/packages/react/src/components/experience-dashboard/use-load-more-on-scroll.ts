import { useEffect, useRef } from 'react';

/** 距底部多少像素内触发加载。 Distance from the bottom that triggers a load. */
const THRESHOLD = 240;

/**
 * 离元素最近的可滚动祖先。看板的内容区自己滚动，轨迹列表并不直接持有滚动容器，
 * 因此从哨兵元素往上找。
 *
 * The nearest scrollable ancestor. The board's body owns the scrolling, so the timeline
 * does not hold the scroll container itself and the sentinel walks up to find it.
 */
function getScrollParent(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement ?? null;
  while (current) {
    const { overflowY } = window.getComputedStyle(current);
    if (overflowY === 'auto' || overflowY === 'scroll') return current;
    current = current.parentElement;
  }
  return null;
}

/**
 * 滚到接近底部时自动追加下一页。
 *
 * 一次请求只由一次到达底部触发，停在那里不会连续发请求；往回滚或离开触发区就
 * 重新武装，用户再次回到末尾时会再请求一次——失败时内容没有变高，只有这样才能
 * 让「失败后重试同一页」成立。调用方仍需保留可点击的「加载更多」，键盘与读屏
 * 用户不靠滚动也能翻页。
 *
 * Appends the next page once the list is scrolled near its end.
 *
 * One arrival at the end triggers one request, so resting there does not fire
 * repeatedly; scrolling back up or leaving the trigger zone re-arms it, and returning to
 * the end asks again. After a failure the content did not grow, and this is what makes
 * "the next scroll retries the same page" true. The caller still keeps a clickable
 * "load more", so keyboard and screen-reader users page without scrolling.
 */
export function useLoadMoreOnScroll({
  enabled,
  loading,
  onLoadMore,
}: {
  enabled: boolean;
  loading: boolean;
  onLoadMore?: () => void;
}) {
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // 上一次请求时的滚动位置；`null` 表示已重新武装、可以再请求一次。
  //
  // The scroll position of the last request; `null` means it is re-armed and may ask
  // again.
  const requestedAt = useRef<number | null>(null);
  // 上一次请求时的内容高度。成功追加会让它变大，那是「又有新内容可以往下翻」的
  // 唯一信号：此时必须重新武装，否则用户直接滚到新底部时仍在触发区内、又没有往上
  // 滚过，下一页就永远请求不到。
  //
  // The content height at the last request. A successful append grows it, and that is
  // the only signal that there is fresh content below: re-arm on it, or scrolling
  // straight to the new bottom — still inside the trigger zone, without having scrolled
  // up — would never ask for the next page.
  const requestedHeight = useRef(0);
  // 上一次滚动事件看到的位置，用来判断这次是在往回滚。
  //
  // The position the previous scroll event saw, used to tell whether this one moves up.
  const previousTop = useRef(0);

  // useEffect 必要性：与滚动容器这个外部系统同步——滚动事件只发生在浏览器的滚动
  // 元素上，渲染计算和点击都无法得知用户滚到了哪里。哨兵换位、加载状态或回调
  // 变化时重新订阅；卸载或依赖变化时移除监听，避免旧容器继续触发。
  //
  // Why an effect: it syncs with the scroll container, an external system — scroll
  // events only exist on a browser scroller, and neither render nor a click can tell
  // where the user scrolled to. It re-subscribes when the sentinel, the loading state or
  // the callback changes, and removes the listener on unmount or dependency change so an
  // old container stops triggering it.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!enabled || loading || !onLoadMore || !sentinel) return undefined;
    const scroller = getScrollParent(sentinel);
    if (!scroller) return undefined;
    const onScroll = () => {
      const top = scroller.scrollTop;
      const height = scroller.scrollHeight;
      const distance = height - top - scroller.clientHeight;
      const movingUp = top < previousTop.current;
      previousTop.current = top;
      // 三种情况重新武装：往回滚、离开触发区、内容变高了。前两种覆盖失败后的
      // 重试（内容没变高，只能靠用户回到同一位置），第三种覆盖成功后接着往下翻。
      //
      // Three cases re-arm it: scrolling back up, leaving the trigger zone, and content
      // growth. The first two cover retrying after a failure, where the content did not
      // grow and only the user's return to the same place can ask again; the third
      // covers paging on after a success.
      if (
        requestedAt.current !== null &&
        (movingUp || distance > THRESHOLD || height > requestedHeight.current)
      )
        requestedAt.current = null;
      // 往回滚不触发：重试发生在用户回到列表末尾时，而不是往上翻的过程中。
      //
      // Scrolling up does not trigger: the retry happens when the user returns to the
      // end of the list, not while paging back through it.
      if (movingUp || distance > THRESHOLD || requestedAt.current !== null)
        return;
      requestedAt.current = top;
      requestedHeight.current = height;
      onLoadMore();
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, [enabled, loading, onLoadMore]);

  return sentinelRef;
}
