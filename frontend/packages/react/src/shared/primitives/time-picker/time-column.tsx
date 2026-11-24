/**
 * Adapted from Arco Design TimePicker/time-column.tsx (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 * Keeps list cells, selected-value scrolling and debounced wheel selection;
 * uses native scrolling instead of b-tween and adds keyboard listbox navigation.
 * https://github.com/arco-design/arco-design/blob/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/TimePicker/time-column.tsx
 */
import { useEffect, useRef } from 'react';

export interface TimeColumnItem {
  label: string;
  value: number;
  selected: boolean;
}

export function TimeColumn({
  list,
  value,
  label,
  onSelect,
}: {
  list: TimeColumnItem[];
  value: number;
  label: string;
  onSelect: (value: number) => void;
}) {
  const items = useRef(new Map<number, HTMLLIElement>());
  const wrapper = useRef<HTMLDivElement>(null);
  const ul = useRef<HTMLUListElement>(null);
  const previousScrollTop = useRef(0);
  const mounted = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  // useEffect 必要性：选中项的 offsetTop 来自已提交的 DOM，不能在渲染时计算。
  // 打开和受控值变化时滚动对应列，减少动态效果时直接定位；清理待处理的滚轮回调。
  useEffect(() => {
    const node = wrapper.current;
    const item = items.current.get(value);
    if (!node || !item) return;
    const top = item.offsetTop;
    const smooth =
      mounted.current &&
      !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (node.scrollTo)
      node.scrollTo({ top, behavior: smooth ? 'smooth' : 'instant' });
    else node.scrollTop = top;
    previousScrollTop.current = top;
    mounted.current = true;
    return () => window.clearTimeout(timer.current);
  }, [value]);

  return (
    <div
      className="ov-time-picker__list"
      ref={wrapper}
      onWheel={() => {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          const node = wrapper.current;
          const content = ul.current;
          if (!node || !content || list.length <= 1) return;
          const height =
            (content.clientHeight - node.clientHeight) / (list.length - 1);
          if (!height) return;
          const round =
            node.scrollTop > previousScrollTop.current ? Math.ceil : Math.floor;
          const item = list[round(node.scrollTop / height)];
          if (item && item.value !== value) onSelect(item.value);
        }, 100);
      }}
    >
      <ul ref={ul} role="listbox" aria-label={label}>
        {list.map((item, index) => (
          <li
            key={item.value}
            role="option"
            aria-selected={item.selected}
            tabIndex={item.selected ? 0 : -1}
            className={[
              'ov-time-picker__cell',
              item.selected && 'ov-time-picker__cell--selected',
            ]
              .filter(Boolean)
              .join(' ')}
            onClick={() => onSelect(item.value)}
            ref={(element) => {
              if (element) items.current.set(item.value, element);
              else items.current.delete(item.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(item.value);
                return;
              }
              const next =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? list.length - 1
                    : event.key === 'ArrowDown'
                      ? (index + 1) % list.length
                      : event.key === 'ArrowUp'
                        ? (index + list.length - 1) % list.length
                        : undefined;
              if (next === undefined) return;
              event.preventDefault();
              const target = items.current.get(list[next]?.value ?? value);
              target?.focus({ preventScroll: true });
              if (wrapper.current && target)
                wrapper.current.scrollTop = target.offsetTop;
            }}
          >
            <div className="ov-time-picker__cell-inner">{item.label}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
