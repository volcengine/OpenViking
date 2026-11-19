/**
 * Adapted from Arco Design Pagination (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/Pagination
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Keeps the numbered pager: previous/next steps, page items, `...` jumpers, the active
 * and disabled states. Omits page-size switching, the quick jumper, simple mode, total
 * text, custom icons, ConfigProvider and RTL — none of them is used here, and each
 * would drag in another dependency.
 * Visual rules come from the console's theme-ve-o-aml-design@0.0.36 skin.
 */
export interface PaginationLabels {
  previous: string;
  next: string;
  /** 页码按钮的无障碍名称，`{page}` 为占位符。 Accessible name of a page item. */
  page?: string;
  jumpPrevious?: string;
  jumpNext?: string;
}

export interface PaginationProps {
  /** 当前页，从 1 开始。Current page, 1-based. */
  current: number;
  /** 总页数；为 0 时不渲染。 Total pages; nothing renders at 0. */
  pageCount: number;
  /** 禁用整个分页器。 Disables the whole pager. */
  disabled?: boolean;
  labels: PaginationLabels;
  onChange: (page: number) => void;
}

/** 当前页两侧各显示几个页码，与 Arco 的默认值一致。 Pages shown on each side. */
const BUFFER_SIZE = 2;

const DEFAULTS = {
  page: 'Page {page}',
  jumpPrevious: 'Jump back some pages',
  jumpNext: 'Jump forward some pages',
};

/**
 * 页码列表：首尾各一页，其余按当前页折叠成 `...`。
 *
 * 折叠规则照搬 Arco：页数不多时全部展开，靠近首尾时只折叠另一侧，中间时两侧都
 * 折叠。`...` 本身是跳页按钮，不是省略号文本。
 *
 * The page list: the first and last page always show, the middle folds into `...`.
 *
 * The folding rule is Arco's: show every page when there are few, fold only the far
 * side near either end, and fold both sides in the middle. Each `...` is a jump
 * button, not ellipsis text.
 */
function pageItems(current: number, pageCount: number): (number | 'gap')[] {
  const beginFold = 3 + BUFFER_SIZE;
  const endFold = pageCount - 2 - BUFFER_SIZE;
  if (
    pageCount <= 4 + BUFFER_SIZE * 2 ||
    (current === beginFold && current === endFold)
  )
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  let left = 1;
  let right = pageCount;
  let gapBefore = true;
  let gapAfter = true;
  if (current > beginFold && current < endFold) {
    left = current - BUFFER_SIZE;
    right = current + BUFFER_SIZE;
  } else if (current <= beginFold) {
    gapBefore = false;
    right = Math.max(beginFold, BUFFER_SIZE + current);
  } else {
    gapAfter = false;
    left = Math.min(endFold, current - BUFFER_SIZE);
  }
  const items: (number | 'gap')[] = [];
  if (gapBefore) items.push(1, 'gap');
  for (let page = left; page <= right; page++) items.push(page);
  if (gapAfter) items.push('gap', pageCount);
  return items;
}

export function Pagination({
  current,
  pageCount,
  disabled,
  labels,
  onChange,
}: PaginationProps) {
  if (pageCount <= 0) return null;
  const text = { ...DEFAULTS, ...labels };
  const step = (page: number) => {
    if (disabled || page < 1 || page > pageCount || page === current) return;
    onChange(page);
  };
  return (
    <nav className="ov-pagination">
      <ul className="ov-pagination__list">
        <li>
          <button
            type="button"
            className="ov-pagination__item"
            aria-label={text.previous}
            disabled={disabled || current <= 1}
            onClick={() => step(current - 1)}
          >
            <Chevron direction="start" />
          </button>
        </li>
        {pageItems(current, pageCount).map((page, index) =>
          page === 'gap' ? (
            <li key={`gap-${index}`}>
              <button
                type="button"
                className="ov-pagination__item ov-pagination__item--jump"
                aria-label={index === 1 ? text.jumpPrevious : text.jumpNext}
                disabled={disabled}
                onClick={() =>
                  step(
                    index === 1
                      ? Math.max(1, current - (BUFFER_SIZE * 2 + 1))
                      : Math.min(pageCount, current + BUFFER_SIZE * 2 + 1),
                  )
                }
              >
                <span aria-hidden="true">···</span>
              </button>
            </li>
          ) : (
            <li key={page}>
              <button
                type="button"
                className="ov-pagination__item"
                aria-label={text.page?.replace('{page}', String(page))}
                aria-current={page === current ? 'page' : undefined}
                disabled={disabled}
                onClick={() => step(page)}
              >
                {page}
              </button>
            </li>
          ),
        )}
        <li>
          <button
            type="button"
            className="ov-pagination__item"
            aria-label={text.next}
            disabled={disabled || current >= pageCount}
            onClick={() => step(current + 1)}
          >
            <Chevron direction="end" />
          </button>
        </li>
      </ul>
    </nav>
  );
}

function Chevron({ direction }: { direction: 'start' | 'end' }) {
  return (
    <svg
      aria-hidden="true"
      width={12}
      height={12}
      viewBox="0 0 16 16"
      fill="none"
    >
      <path
        d={direction === 'start' ? 'M10 3 5 8l5 5' : 'm6 3 5 5-5 5'}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
