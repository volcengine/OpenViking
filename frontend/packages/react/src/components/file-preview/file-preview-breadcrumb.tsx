import { Fragment, useLayoutEffect, useRef, useState } from 'react';

import { FilePreviewBreadcrumbMenu } from './file-preview-breadcrumb-menu.js';
import type { PathSegment } from './file-preview-path.js';

/** 祖先段在溢出时的最大宽度，超出就省略号截断。 */
const MAX_ANCESTOR_WIDTH = 160;
/** 溢出后「…」按钮占用的宽度。 */
const OVERFLOW_BUTTON_WIDTH = 36;
/** 位移动画时长，与内部版一致。 */
const SHIFT_DURATION = 160;

export interface FilePreviewBreadcrumbProps {
  segments: readonly PathSegment[];
  title: string;
  canOpen: boolean;
  disabled: boolean;
  onOpen: (uri: string) => void;
}

/** 判断是否应跳过动画：用户要求减少动效时不做位移。 */
function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * 面包屑可用的宽度：父行宽度减去行内其他兄弟元素（复制按钮、插槽等）占的
 * 宽度与间距。
 *
 * 不能用面包屑自身的 `clientWidth`：它随折叠结果变化，收起后变窄，下一轮
 * 测量就会认为空间更不够，从此再也放不回来。父行宽度只由外部布局决定。
 *
 * The width available to the breadcrumb: the parent row's width minus the space
 * its siblings (copy button, slots) and the gaps take.
 *
 * The breadcrumb's own `clientWidth` cannot be used here: it shrinks as levels
 * collapse, so the next measurement would see even less room and never restore
 * them. The parent row's width is decided by the surrounding layout alone.
 */
function measureAvailable(root: HTMLElement) {
  const row = root.parentElement;
  if (!row) return root.clientWidth;

  const style = window.getComputedStyle(row);
  const gap = Number.parseFloat(style.columnGap) || 0;
  const siblings = Array.from(row.children).filter(
    (child): child is HTMLElement =>
      child !== root && child instanceof HTMLElement,
  );
  const siblingWidth = siblings.reduce(
    (sum, child) => sum + child.getBoundingClientRect().width,
    0,
  );
  const siblingGaps = gap * siblings.length;

  return Math.max(0, row.clientWidth - siblingWidth - siblingGaps);
}

/**
 * 路径面包屑：祖先逐段可点，放不下时按「保两端、缩中间」折叠。
 *
 * 折叠顺序与内部版一致：先保留首段与末段，从紧邻首段的那一级开始逐级收进
 * 「…」；中间全部收起后仍放不下，才连首段一起收起，只留末段。
 *
 * Path breadcrumb: every ancestor is clickable, and overflow collapses by keeping
 * both ends and shrinking the middle. The first level and the trailing name stay
 * as long as possible; ancestors are folded into a "…" menu starting next to the
 * first level, and only when even that does not fit is the first level dropped
 * too, leaving the trailing name alone.
 */
export function FilePreviewBreadcrumb({
  segments,
  title,
  canOpen,
  disabled,
  onOpen,
}: FilePreviewBreadcrumbProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef<HTMLSpanElement>(null);
  const previousRef = useRef<{ signature: string; left: number } | undefined>(
    undefined,
  );
  const [hiddenCount, setHiddenCount] = useState(0);
  const [rootHidden, setRootHidden] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const signature = JSON.stringify([segments.map((s) => s.uri), title]);

  // useLayoutEffect 必要性：折叠几级取决于容器宽度与各段自然宽度，二者都只有
  // DOM 提交后才可测量，且会随外部布局和字体加载变化，无法在渲染或点击事件里
  // 算出。容器或测量行尺寸变化时重新测量，卸载时 disconnect。
  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;

    const run = () => {
      const widths = Array.from(measure.children).map(
        (child) => child.getBoundingClientRect().width,
      );
      // 可用宽度取自父行，而不是面包屑自身：面包屑的宽度随折叠结果变化，
      // 拿它当基准会形成自反馈——收起后自身变窄，下一轮就认为空间更不够，
      // 于是永远收不回来。父行宽度由外部布局决定，与折叠状态无关。
      const available = measureAvailable(root);
      const isOverflowing =
        widths.reduce((sum, width) => sum + width, 0) > available;
      // 先按自然宽度判断是否溢出；确实放不下时才限制祖先段的显示宽度。
      const capped = isOverflowing
        ? widths.map((width, index) =>
            index < widths.length - 1
              ? Math.min(width, MAX_ANCESTOR_WIDTH)
              : width,
          )
        : widths;
      const ancestors = capped.slice(0, -1);
      const trailing = capped[capped.length - 1] ?? 0;
      const total = capped.reduce((sum, width) => sum + width, 0);
      setOverflowing(isOverflowing);

      // 从紧邻首段的那一级开始收起，首段留到最后。
      let count = 0;
      let used = total;
      while (used > available && count < ancestors.length - 1) {
        used -= ancestors[count + 1] ?? 0;
        if (count === 0) used += OVERFLOW_BUTTON_WIDTH;
        count += 1;
      }

      // 中间全部收起仍放不下，才连首段一起藏，把「…」挪到行首。此时布局
      // 只剩「…」+ 末段；只有这确实比保留首段更省时才值得藏。
      // 若连「…」+ 末段都超出可用宽度，说明文件名本身太长，交给 CSS 让
      // 末段自己截断——继续藏层级已经没有东西可藏了。
      let hideRoot = false;
      if (used > available && ancestors.length > 0) {
        const rootCollapsed = OVERFLOW_BUTTON_WIDTH + trailing;
        hideRoot = rootCollapsed < used;
      }
      setHiddenCount(hideRoot ? ancestors.length : count);
      setRootHidden(hideRoot);
    };

    run();
    const observer = new ResizeObserver(run);
    observer.observe(root);
    observer.observe(measure);
    // 父行决定可用宽度：面板拉宽、动作组增减都会改变它，必须一并观察，
    // 否则空间变大时不会重新测量，收起的层级再也放不回来。
    if (root.parentElement) observer.observe(root.parentElement);
    return () => observer.disconnect();
  }, [segments, title]);

  // useLayoutEffect 必要性：驱动末段的位移动画。起点取决于上一帧的
  // offsetLeft，这是浏览器的布局结果，只能在提交后读取；动画对象也是 DOM
  // 资源。签名变化（切换节点）时不动画，位移结束或组件卸载时 cancel，
  // 避免快速切换时动画叠加。
  useLayoutEffect(() => {
    const current = currentRef.current;
    const previous = previousRef.current;
    if (!current) return;
    const left = current.offsetLeft;
    previousRef.current = { signature, left };
    if (
      !previous ||
      previous.signature !== signature ||
      previous.left === left ||
      prefersReducedMotion()
    ) {
      return;
    }
    const animation = current.animate(
      [
        { transform: `translateX(${previous.left - left}px)`, opacity: 0.65 },
        { transform: 'translateX(0)', opacity: 1 },
      ],
      { duration: SHIFT_DURATION, easing: 'cubic-bezier(0.2, 0, 0, 1)' },
    );
    return () => animation.cancel();
  }, [hiddenCount, rootHidden, signature]);

  // 被收起的层级：首段保留时从第 2 段起算，首段也收起时从头算。
  const hiddenSegments = segments.filter((_, index) =>
    rootHidden ? index < hiddenCount : index > 0 && index <= hiddenCount,
  );
  const menu = hiddenSegments.length ? (
    <FilePreviewBreadcrumbMenu
      segments={hiddenSegments}
      canOpen={canOpen}
      disabled={disabled}
      onOpen={onOpen}
    />
  ) : null;

  return (
    <div
      className={[
        'ov-file-preview__breadcrumb',
        overflowing ? 'ov-file-preview__breadcrumb--overflowing' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      ref={rootRef}
    >
      <div
        className="ov-file-preview__breadcrumb-measure"
        ref={measureRef}
        aria-hidden="true"
      >
        {segments.map((segment) => (
          <span key={segment.uri} className="ov-file-preview__breadcrumb-part">
            <span>{segment.isFirst ? `${segment.uri}` : segment.label}</span>
            <span className="ov-file-preview__breadcrumb-separator">/</span>
          </span>
        ))}
        <span>{title}</span>
      </div>
      {rootHidden && menu ? (
        <span className="ov-file-preview__breadcrumb-part">
          {menu}
          <span className="ov-file-preview__breadcrumb-separator">/</span>
        </span>
      ) : null}
      {segments.map((segment, index) => {
        if (hiddenSegments.includes(segment)) return null;
        return (
          <Fragment key={segment.uri}>
            <span className="ov-file-preview__breadcrumb-part">
              <button
                type="button"
                className="ov-file-preview__breadcrumb-link"
                disabled={!canOpen || disabled}
                title={segment.uri}
                onClick={() => onOpen(segment.uri)}
              >
                {segment.isFirst ? `${segment.uri}` : segment.label}
              </button>
              <span className="ov-file-preview__breadcrumb-separator">/</span>
            </span>
            {index === 0 && !rootHidden && menu ? (
              <span className="ov-file-preview__breadcrumb-part">
                {menu}
                <span className="ov-file-preview__breadcrumb-separator">/</span>
              </span>
            ) : null}
          </Fragment>
        );
      })}
      <span
        ref={currentRef}
        className="ov-file-preview__breadcrumb-current"
        aria-current="page"
        title={title}
      >
        {title}
      </span>
    </div>
  );
}
