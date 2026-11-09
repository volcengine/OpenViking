import { useCallback, useEffect, useRef, useState } from 'react';

/** 悬停多久后展示浮层。How long the pointer rests before the tooltip opens. */
const DEFAULT_DELAY = 400;

/**
 * 悬停停留一段时间后才展示行浮层。
 *
 * 用定时器而不是 CSS 延迟，是因为浮层内容由宿主渲染、需要按行 id 切换；卸载或
 * 换行时清掉未触发的定时器，避免迟到的回调写入已失效的行。
 *
 * Shows a row tooltip once the pointer has rested for a while.
 *
 * A timer rather than a CSS delay, because the host renders the content and it
 * switches per row id; a pending timer is cleared on unmount or when the row
 * changes, so a late callback never writes to a row that is gone.
 */
export function useRowTooltip(delay = DEFAULT_DELAY) {
  const [tooltipRowId, setTooltipRowId] = useState<string | null>(null);
  const timerRef = useRef<number | undefined>(undefined);

  const clear = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  // 卸载时清掉未触发的定时器。Clear a pending timer on unmount.
  useEffect(() => clear, [clear]);

  const show = useCallback(
    (rowId: string) => {
      clear();
      timerRef.current = window.setTimeout(() => {
        setTooltipRowId(rowId);
        timerRef.current = undefined;
      }, delay);
    },
    [clear, delay],
  );

  const hide = useCallback(() => {
    clear();
    setTooltipRowId(null);
  }, [clear]);

  return { tooltipRowId, show, hide };
}
