import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * 悬停停留一段时间后才展示行提示。
 *
 * 用 timeout 而不是 CSS 延迟，是因为提示内容由宿主渲染、需要按行 id 切换；
 * 卸载或换行时清掉未触发的定时器，避免迟到的回调写入已失效的行。
 */
export function useRowTooltip(delay = 400) {
  const [tooltipRowId, setTooltipRowId] = useState<string | null>(null);
  const timerRef = useRef<number | undefined>(undefined);

  useEffect(
    () => () => {
      if (timerRef.current !== undefined) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  const show = useCallback(
    (rowId: string) => {
      if (timerRef.current !== undefined) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setTooltipRowId(rowId);
        timerRef.current = undefined;
      }, delay);
    },
    [delay],
  );

  const hide = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
    setTooltipRowId(null);
  }, []);

  return { tooltipRowId, show, hide };
}
