import { useLayoutEffect, useRef, useState } from 'react';

import type { PathSegment } from './file-preview-path.js';

/** 溢出时保留给「…」按钮的宽度。 */
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
 * 路径面包屑：祖先逐段可点，放不下时收进「…」下拉，切换节点时末段做位移过渡。
 *
 * 用隐藏的测量行取各段自然宽度，再从右往左逐个隐藏祖先，直到可见部分与
 * 末段都能容纳；这与内部版 `UriBreadcrumb` 的算法一致。
 *
 * Path breadcrumb: every ancestor is clickable, overflowing levels collapse into
 * a "…" menu, and the trailing segment shifts with a transition when the node
 * changes.
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
  const signature = JSON.stringify([segments.map((s) => s.uri), title]);

  // useLayoutEffect 必要性：用 ResizeObserver 订阅面包屑容器宽度，宽度变化
  // 与文本自然宽度决定要隐藏几级祖先，二者都只有 DOM 提交后才可测量，且容器
  // 宽度会随外部布局变化，无法在渲染或点击事件里算出。容器更换或卸载时
  // disconnect，避免旧订阅继续回调。
  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;

    const run = () => {
      const available = root.clientWidth - OVERFLOW_BUTTON_WIDTH;
      const parts = Array.from(measure.children) as HTMLElement[];
      const widths = parts.map((part) => part.getBoundingClientRect().width);
      let total = widths.reduce((sum, width) => sum + width, 0);
      let hidden = 0;
      while (hidden < segments.length && total > available) {
        total -= widths[hidden] ?? 0;
        hidden += 1;
      }
      setHiddenCount(hidden);
    };

    run();
    const observer = new ResizeObserver(run);
    observer.observe(root);
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
  }, [hiddenCount, signature]);

  const hidden = segments.slice(0, hiddenCount);
  const visible = segments.slice(hiddenCount);

  return (
    <div className="ov-file-preview__breadcrumb" ref={rootRef}>
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
      {hidden.length > 0 ? (
        <details className="ov-file-preview__breadcrumb-more">
          <summary aria-label="Show hidden path levels">…</summary>
          <ul className="ov-file-preview__breadcrumb-menu">
            {hidden.map((segment) => (
              <li key={segment.uri}>
                <button
                  type="button"
                  disabled={!canOpen || disabled}
                  onClick={() => onOpen(segment.uri)}
                >
                  {segment.isFirst ? `${segment.uri}/` : `${segment.label}/`}
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {visible.map((segment) => (
        <span key={segment.uri} className="ov-file-preview__breadcrumb-part">
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
      ))}
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
