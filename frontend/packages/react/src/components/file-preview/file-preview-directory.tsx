import type { Locale } from '../../shared/locale.js';
import { useLayoutEffect, useRef, useState } from 'react';

import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';

import type { FilePreviewActions } from './types.js';
import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { LoadingState } from './file-preview-state.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewViewMode,
} from './types.js';

/**
 * 目录页签的正文：标题行之下多一层同级视图切换（L0 摘要 / L1 总览），每一级
 * 各自是一份 Markdown，走与文件页签相同的渲染路径。
 *
 * 这一层属于目录这种实体：文件没有同级视图可切，给它加同样的插槽只会留一个
 * 永远空着的位置。
 *
 * The body of a directory tab: one extra level of sibling switching between
 * summary levels (L0 / L1) under the header, each level its own Markdown going
 * through the same rendering path as a file tab.
 *
 * That level belongs to the directory entity: a file has no sibling views to
 * switch, so giving it the same slot would only leave a permanently empty space.
 */
export interface FilePreviewDirectoryBodyProps {
  tab: FilePreviewDirectoryTab;
  text: Required<FilePreviewLabels>;
  locale?: Locale;
  actions: FilePreviewActions;
  editing: ReturnType<typeof useFilePreviewEditing>;
  viewMode: FilePreviewViewMode;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  onDownload: () => void;
  onCancelEditing: () => void;
}

/**
 * 把某一级摘要包装成文件页签的形状，正文渲染因此只有一条路径。
 * Wraps one level as a file tab so the body has exactly one rendering path.
 */
function levelToFileTab(
  tab: FilePreviewDirectoryTab,
  key: string,
): FilePreviewFileTab | undefined {
  const level = tab.levels.find((candidate) => candidate.key === key);
  if (!level) return undefined;
  return {
    kind: 'file',
    key: `${tab.key}::${level.key}`,
    source: level.source,
    resources: level.resources ?? tab.resources,
    resolveImage: tab.resolveImage,
    operations: tab.operations,
    slots: tab.slots,
  };
}

export function FilePreviewDirectoryBody({
  tab,
  text,
  locale,
  actions,
  editing,
  viewMode,
  onViewModeChange,
  onHandleChange,
  onViewModesChange,
  onDownload,
  onCancelEditing,
}: FilePreviewDirectoryBodyProps) {
  // 层级由外层持有（复制正文要跟着当前层级），这里只补一个非受控时的初值。
  // The level is owned by the outer component, because copy-content follows it; this
  // only supplies the uncontrolled default.
  const [internalKey, setInternalKey] = useState(
    () => tab.defaultLevelKey ?? tab.levels[0].key,
  );
  const activeKey = tab.activeLevelKey ?? internalKey;
  const fileTab =
    levelToFileTab(tab, activeKey) ?? levelToFileTab(tab, tab.levels[0].key);
  const tabsRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });

  // useLayoutEffect 必要性：指示条的位置来自页签按钮的实际布局盒，只有提交后
  // 才可测量；字体加载、容器缩放与层级变化都会改变它，无法在渲染时算出。用
  // 位移动画让它在两级之间滑动，而不是各画一条静止的下划线。
  //
  // A layout effect is required: the indicator's position comes from the tab
  // buttons' layout boxes, measurable only after commit, and font loading,
  // container resizing or a level change all move them. The offset animation makes
  // it slide between levels instead of drawing a static underline on each.
  useLayoutEffect(() => {
    const root = tabsRef.current;
    if (!root) return;
    const measure = () => {
      const active = root.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!active) return;
      setIndicator({
        left: active.offsetLeft + (active.offsetWidth - 14) / 2,
        width: 14,
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [activeKey, tab.levels]);

  const changeLevel = (key: string) => {
    if (tab.activeLevelKey === undefined) setInternalKey(key);
    tab.onActiveLevelChange?.(key);
  };

  // 层级切换同样用 roving tabindex：未选中的一级 `tabIndex=-1`，只有方向键能
  // 把焦点移过去。缺了它，键盘用户 Tab 到当前层级后就直接进入正文，永远切不到
  // 另一级摘要——与页签行上的问题完全一样。
  //
  // Level switching uses a roving tabindex too: unselected levels are `tabIndex=-1`
  // and only the arrow keys reach them. Without it a keyboard user tabs to the
  // current level and straight into the body, never reaching the other summary
  // level — exactly the problem the tab row already solves.
  const onLevelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
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
    const key = tab.levels[next]?.key;
    if (key !== undefined) changeLevel(key);
  };

  // 层级 key 对不上时 `levelToFileTab` 会返回 undefined；此时不渲染标题行与
  // 正文，避免用一个不存在的页签去取渲染数据。
  //
  // A mismatched level key leaves `levelToFileTab` undefined; skip the header and
  // body rather than rendering from a tab that does not exist.
  if (!fileTab) return null;
  // 摘要还在读取：只渲染外壳与加载态，层级切换与正文都等数据到了再说。
  // Still reading the summary: render the shell and the loading state only; the
  // level switch and the body wait for the data.
  if (tab.loading) {
    return (
      <>
        <FilePreviewHeader
          path={fileTab.source.path}
          fileName={fileTab.source.fileName}
          canOpenParent={Boolean(tab.operations?.openParent)}
          titleExtra={tab.slots?.titleExtra}
          slotActions={tab.slots?.actions}
          extraActions={tab.slots?.extraActions}
          actions={actions}
          text={text}
          onOpenParent={(uri) => void tab.operations?.openParent?.(uri)}
        />
        <div className="ov-file-preview__body">
          <LoadingState text={text} />
        </div>
      </>
    );
  }

  return (
    <>
      <FilePreviewHeader
        path={fileTab.source.path}
        fileName={fileTab.source.fileName}
        canOpenParent={Boolean(tab.operations?.openParent)}
        titleExtra={tab.slots?.titleExtra}
        slotActions={tab.slots?.actions}
        extraActions={tab.slots?.extraActions}
        actions={actions}
        text={text}
        onOpenParent={(uri) => void tab.operations?.openParent?.(uri)}
      />
      {/* 同级视图切换：下划线页签，位于标题行与正文之间。 */}
      {/* Sibling switching: underlined tabs between the header and the body. */}
      {tab.levels.length > 1 ? (
        <div
          className="ov-file-preview__levels"
          role="tablist"
          ref={tabsRef}
          tabIndex={-1}
          onKeyDown={onLevelKeyDown}
        >
          {tab.levels.map((level) => (
            <button
              key={level.key}
              type="button"
              role="tab"
              aria-selected={level.key === activeKey}
              tabIndex={level.key === activeKey ? 0 : -1}
              className="ov-file-preview__level"
              data-active={level.key === activeKey || undefined}
              onClick={() => changeLevel(level.key)}
            >
              {level.label}
            </button>
          ))}
          <span
            aria-hidden="true"
            className="ov-file-preview__level-indicator"
            style={{
              transform: `translateX(${indicator.left}px)`,
              width: indicator.width,
            }}
          />
        </div>
      ) : null}
      <div className="ov-file-preview__body">
        <FilePreviewBody
          key={fileTab.key}
          fileTab={fileTab}
          text={text}
          locale={locale}
          viewMode={viewMode}
          editing={editing}
          onViewModeChange={onViewModeChange}
          onHandleChange={onHandleChange}
          onViewModesChange={onViewModesChange}
          onDownload={onDownload}
          onCancelEditing={onCancelEditing}
        />
      </div>
      {tab.slots?.footer ? (
        <div className="ov-file-preview__footer">{tab.slots.footer}</div>
      ) : null}
    </>
  );
}
