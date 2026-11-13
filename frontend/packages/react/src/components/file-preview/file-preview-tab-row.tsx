import { useLayoutEffect, useRef, useState } from 'react';

import type { FilePreviewTab } from './types.js';

/** 轮廓在页签上沿与底部收口处的圆角半径，与宿主一致。 */
const OUTLINE_RADIUS = 7;
/** 描边向内收半个线宽，1px 的线才能落在像素中心上。 */
const STROKE_INSET = 0.5;

export interface FilePreviewTabRowProps {
  tabs: readonly FilePreviewTab[];
  activeKey: string;
  disabled: boolean;
  /** 按 key 生成页签的 DOM id，带实例前缀避免多实例重复。 */
  tabId: (key: string) => string;
  /** 按 key 生成面板的 DOM id，与 `tabId` 同源。 */
  panelId: (key: string) => string;
  onSelect: (key: string) => void;
}

/**
 * 选中页签与基线连成一条轮廓线：从左下角起画到页签左沿，绕过左上、右上
 * 两个圆角，再落到页签右沿并接到右边界。基线本身也由这条线画出，因此不会
 * 和页签描边重复叠加。
 *
 * The selected tab and the baseline are one continuous outline: it runs from the
 * bottom-left along the baseline, up the tab's left edge, around both top
 * corners and back down to the baseline. The baseline is part of the same path,
 * so it never double-paints with the tab border.
 */
function buildOutline(
  width: number,
  top: number,
  bottom: number,
  left: number,
  right: number,
) {
  const radius = OUTLINE_RADIUS;
  // 页签太窄时圆角放不下，退化成一条直线，避免出现自交的轮廓。
  if (right - left < radius * 2 + 1) return `M0 ${bottom} H${width}`;

  // 首个页签的左沿就是基线起点，没有可画的引入段，直接从它的左下角起笔。
  const lead =
    left > radius
      ? [`M0 ${bottom}`, `H${left - radius}`]
      : [`M${left} ${bottom}`];

  return [
    ...lead,
    `Q${left} ${bottom} ${left} ${bottom - radius}`,
    `V${top + radius}`,
    `Q${left} ${top} ${left + radius} ${top}`,
    `H${right - radius}`,
    `Q${right} ${top} ${right} ${top + radius}`,
    `V${bottom - radius}`,
    `Q${right} ${bottom} ${right + radius} ${bottom}`,
    `H${width}`,
  ].join(' ');
}

/**
 * 页签行：文件夹卡片式页签，选中页签与内容区连成一体。
 * The tab row: folder-style tabs where the selected one joins the content area.
 */
export function FilePreviewTabRow({
  tabs,
  activeKey,
  disabled,
  tabId,
  panelId,
  onSelect,
}: FilePreviewTabRowProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [outline, setOutline] = useState('');

  // useLayoutEffect 必要性：轮廓的坐标来自页签与轨道的实际布局盒，只有 DOM
  // 提交后才可测量，且标题变化、容器缩放都会改变结果，无法在渲染或点击事件
  // 里算出。轨道尺寸变化时由 ResizeObserver 重新测量，卸载时 disconnect。
  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const measure = () => {
      const trackRect = track.getBoundingClientRect();
      const bottom = trackRect.height - STROKE_INSET;
      const active = track.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!active) {
        setOutline(`M0 ${bottom} H${trackRect.width}`);
        return;
      }
      const rect = active.getBoundingClientRect();
      setOutline(
        buildOutline(
          trackRect.width,
          rect.top - trackRect.top + STROKE_INSET,
          rect.bottom - trackRect.top - STROKE_INSET,
          rect.left - trackRect.left,
          rect.right - trackRect.left,
        ),
      );
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    return () => observer.disconnect();
  }, [activeKey, tabs]);

  // roving tabindex 的键盘导航：未选中的页签 `tabIndex=-1`，只有方向键能把
  // 焦点移过去。缺了它，键盘用户 Tab 到当前页签后就被挡在正文里，永远选不到
  // 其他页签。
  //
  // Keyboard navigation for the roving tabindex: unselected tabs are `tabIndex=-1`
  // and only the arrow keys can reach them. Without this a keyboard user tabs to
  // the active tab and then straight into the body, never reaching another tab.
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const buttons = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>(
        '[role="tab"]:not(:disabled)',
      ),
    );
    if (buttons.length === 0) return;
    const current = buttons.indexOf(event.target as HTMLButtonElement);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) %
            buttons.length;
    const target = buttons[next];
    if (!target) return;
    event.preventDefault();
    target.focus();
    onSelect(target.dataset.key ?? '');
  };

  return (
    <div className="ov-file-preview__tab-row">
      <div
        role="tablist"
        className="ov-file-preview__tab-track"
        ref={trackRef}
        // `tabIndex=-1` 让 tablist 可被编程聚焦（ARIA 要求带交互角色且监听
        // 键盘的元素可聚焦），但不进入 Tab 顺序——焦点仍由各个 tab 承载。
        //
        // `tabIndex=-1` keeps the tablist programmatically focusable, as ARIA
        // requires of an interactive role that handles keys, without putting it in
        // the tab order — the individual tabs carry focus.
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        {tabs.map((tab) => {
          const selected = tab.key === activeKey;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              data-key={tab.key}
              id={tabId(tab.key)}
              aria-selected={selected}
              aria-controls={panelId(tab.key)}
              tabIndex={selected ? 0 : -1}
              disabled={tab.disabled || disabled}
              title={typeof tab.title === 'string' ? tab.title : undefined}
              className={[
                'ov-file-preview__tab',
                selected ? 'ov-file-preview__tab--active' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              onClick={() => onSelect(tab.key)}
            >
              {tab.icon ? (
                <span className="ov-file-preview__tab-icon" aria-hidden="true">
                  {tab.icon}
                </span>
              ) : null}
              <span className="ov-file-preview__tab-title">
                {tab.title ??
                  (tab.kind === 'file' ? tab.source.fileName : tab.key)}
              </span>
            </button>
          );
        })}
        <svg className="ov-file-preview__tab-outline" aria-hidden="true">
          <path d={outline} />
        </svg>
      </div>
    </div>
  );
}
