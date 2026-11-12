import { useEffect, useRef, useState } from 'react';

import { SEARCH_MODES } from './search-types.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 模式下拉：四种检索方式各一行，带一句说明。选中项打勾。
 *
 * 展开状态由调用方持有——结果区的「切换」按钮与下拉本身是两个触发器，共用一个
 * 开关比各自维护一份状态更简单。
 *
 * The mode menu: one row per retrieval mode with a one-line description, and a check
 * on the current one.
 *
 * The open state belongs to the caller: the result header's switch button and the menu
 * are two triggers, and sharing one flag is simpler than keeping two.
 */
export interface FileBrowserSearchModeMenuProps {
  open: boolean;
  value: SearchMode;
  text: ResolvedLabels;
  onSelect: (mode: SearchMode) => void;
  onClose: () => void;
}

export function FileBrowserSearchModeMenu({
  open,
  value,
  text,
  onSelect,
  onClose,
}: FileBrowserSearchModeMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(value);

  useEffect(() => {
    setActive(value);
  }, [value, open]);

  // useEffect 必要性：与 document 的指针事件同步。下拉挂在结果区里，点击外部
  // 关闭必须监听整个文档——只监听自己的节点收不到外部点击。卸载或关闭时移除。
  //
  // Syncs with the document's pointer events. The menu sits inside the result area,
  // so closing on an outside click needs a document-level listener: a listener on its
  // own node never sees that click. Removed on unmount or close.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (ref.current?.contains(event.target)) return;
      onClose();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      ref={ref}
      className="ov-file-browser-search__menu"
      role="group"
      aria-label={text.searchSwitchMode}
    >
      {SEARCH_MODES.map((mode) => (
        <button
          key={mode}
          type="button"
          aria-pressed={mode === active}
          className="ov-file-browser-search__menu-item"
          onClick={() => onSelect(mode)}
        >
          <span className="ov-file-browser-search__menu-text">
            <span className="ov-file-browser-search__menu-name">{mode}</span>
            <span className="ov-file-browser-search__menu-hint">
              {text.searchModeHints[mode]}
            </span>
          </span>
          {mode === active ? (
            <svg
              aria-hidden="true"
              width={14}
              height={14}
              viewBox="0 0 16 16"
              fill="none"
            >
              <path
                d="M3 8.5 6.5 12 13 4.5"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : null}
        </button>
      ))}
    </div>
  );
}
