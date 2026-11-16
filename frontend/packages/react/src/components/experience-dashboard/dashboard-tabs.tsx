import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

/** 指示条宽度，与 CSS 里的 `.ov-experience-dashboard__tab-indicator` 保持一致。 */
/** The indicator width, kept in step with `.ov-experience-dashboard__tab-indicator`. */
const INDICATOR_WIDTH = 16;

/**
 * 指示条的位置：横向居中于选中页签的**标题文字**，而不是整个按钮。
 *
 * 按钮的内边距与文字宽度并不相等，按按钮居中会把指示条压到「内边距+文字」的中点，
 * 视觉上偏左于文字；按文字居中才对得上读者的视线。
 *
 * The indicator's position: horizontally centred on the selected tab's title text
 * rather than the whole button.
 *
 * A button's padding is not symmetric with its text, so centring on the button lands the
 * bar on the midpoint of padding-plus-text, which reads as left of the text. Centring on
 * the text is what the eye follows.
 */
function indicatorLeft(tablist: HTMLElement, title: HTMLElement | null) {
  const target =
    title ?? tablist.querySelector<HTMLElement>('[aria-selected="true"]');
  if (!target) return null;
  const rect = target.getBoundingClientRect();
  const track = tablist.getBoundingClientRect();
  return {
    left: rect.left - track.left + rect.width / 2 - INDICATOR_WIDTH / 2,
    visible: rect.width > 0,
  };
}

/**
 * 看板的页签：一条页签栏加一块面板。
 *
 * 方向键、Home 与 End 在页签之间移动焦点并立即切换，符合 WAI-ARIA 的 tabs 模式。
 * 这个组件只负责结构与键盘行为。
 *
 * The board's tabs: a tab bar and a panel.
 *
 * Arrow keys, Home and End move focus between tabs and switch immediately, following
 * the WAI-ARIA tabs pattern. This component owns the structure and keyboard behaviour.
 */
export function DashboardTabs<T extends string>({
  tabs,
  active,
  label,
  onChange,
  children,
}: {
  tabs: readonly { key: T; title: string }[];
  active: T;
  label: string;
  onChange: (key: T) => void;
  children: ReactNode;
}) {
  const id = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{
    left: number;
    visible: boolean;
  } | null>(null);

  // useLayoutEffect 必要性：指示条的位置来自页签标题的实际布局盒，只有 DOM 提交
  // 后才可测量；标题文案、语言、容器宽度变化都会改变结果，渲染或点击时算不出来。
  // 页签栏尺寸变化时由 ResizeObserver 重新测量，卸载或页签变化时 disconnect。
  //
  // Why a layout effect: the indicator's position comes from the title's real layout
  // box, measurable only after the DOM commits; copy, language and container width all
  // change it and neither render nor a click can compute it. A ResizeObserver
  // re-measures when the bar resizes, and it disconnects on unmount or when the tabs
  // change.
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return undefined;
    const measure = () => {
      const title = list.querySelector<HTMLElement>(
        `[role="tab"][aria-selected="true"] .ov-experience-dashboard__tab-title`,
      );
      const next = indicatorLeft(list, title);
      // 位置没变就不写 state：`tabs` 是调用方每次渲染新建的数组，而这里会重新
      // 测量，无条件 setState 会让「渲染 → 测量 → setState」自成一个循环。
      //
      // Write nothing when the position is unchanged: `tabs` is a fresh array on every
      // caller render and this re-measures, so an unconditional setState would loop
      // render → measure → setState by itself.
      setIndicator((previous) =>
        previous?.left === next?.left && previous?.visible === next?.visible
          ? previous
          : next,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(list);
    for (const button of list.querySelectorAll('[role="tab"]'))
      observer.observe(button);
    return () => observer.disconnect();
  }, [active, tabs]);

  return (
    <>
      <div
        ref={listRef}
        className="ov-experience-dashboard__tabs"
        role="tablist"
        tabIndex={-1}
        aria-label={label}
        style={
          indicator?.visible
            ? ({
                '--ov-experience-indicator-left': `${indicator.left}px`,
              } as React.CSSProperties)
            : undefined
        }
        onKeyDown={(event) => {
          const delta =
            event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (!delta && event.key !== 'Home' && event.key !== 'End') return;
          event.preventDefault();
          const index =
            event.key === 'Home'
              ? 0
              : event.key === 'End'
                ? tabs.length - 1
                : (tabs.findIndex((tab) => tab.key === active) +
                    delta +
                    tabs.length) %
                  tabs.length;
          onChange(tabs[index]!.key);
          const buttons =
            event.currentTarget.querySelectorAll<HTMLButtonElement>(
              '[role="tab"]',
            );
          buttons[index]?.focus();
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            id={`${id}-${tab.key}`}
            role="tab"
            aria-selected={active === tab.key}
            aria-controls={`${id}-panel`}
            tabIndex={active === tab.key ? 0 : -1}
            onClick={() => onChange(tab.key)}
          >
            <span className="ov-experience-dashboard__tab-title">
              {tab.title}
            </span>
          </button>
        ))}
        {/* 量出位置后才挂上指示条：首帧就带着最终坐标，因此不会从左边滑进来；
            之后换页签时坐标变化才会走过渡。
         *
         * The indicator mounts only after its position is measured, so the first frame
         * already carries the final coordinate and it does not slide in from the left;
         * only a later tab switch moves it, which is when the transition runs. */}
        {indicator?.visible ? (
          <span
            className="ov-experience-dashboard__tab-indicator"
            aria-hidden="true"
          />
        ) : null}
      </div>
      <div
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-${active}`}
        className="ov-experience-dashboard__panel"
      >
        {children}
      </div>
    </>
  );
}

export function experienceTabs(
  t: { impact: string; source: string; test: string },
  missingTest: boolean,
) {
  return [
    { key: 'impact' as const, title: t.impact },
    { key: 'source' as const, title: t.source },
    ...(missingTest ? [] : [{ key: 'test' as const, title: t.test }]),
  ];
}
