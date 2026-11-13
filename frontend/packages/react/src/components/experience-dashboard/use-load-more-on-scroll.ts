import { useEffect, useRef } from 'react';

/** 距底部多少像素内触发加载。 Distance from the bottom that triggers a load. */
const THRESHOLD = 240;
/** 两次请求之间至少再滚动的像素数，避免停在底部时连续触发。 */
/** How far the user must scroll again between two requests, so resting at the bottom
 * does not fire repeatedly. */
const SCROLL_DELTA = 24;

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
 * 只在用户又往下滚了一段之后才再请求一次：停在底部不会连续触发，失败后条数没有
 * 增加，再滚一次就重试同一页。调用方仍需保留可点击的「加载更多」，键盘与读屏
 * 用户不靠滚动也能翻页。
 *
 * Appends the next page once the list is scrolled near its end.
 *
 * It asks again only after the user scrolls further: resting at the bottom does not fire
 * repeatedly, and after a failure — where the row count did not grow — the next scroll
 * retries the same page. The caller still keeps a clickable "load more", so keyboard and
 * screen-reader users page without scrolling.
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
  // 起始值比阈值再低一像素：列表短到不用滚动时，用户在顶部的一次滚动也算「又往下
  // 滚了」，仍能触发加载。
  //
  // The start sits one pixel below the delta so a scroll at the very top still counts as
  // moving, which matters when the list is short enough that it never scrolls far.
  const lastScrollTop = useRef(-SCROLL_DELTA - 1);

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
      const distance =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      const moved = scroller.scrollTop > lastScrollTop.current + SCROLL_DELTA;
      if (distance > THRESHOLD || !moved) return;
      lastScrollTop.current = scroller.scrollTop;
      onLoadMore();
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, [enabled, loading, onLoadMore]);

  return sentinelRef;
}
