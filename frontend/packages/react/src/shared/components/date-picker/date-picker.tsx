/**
 * Adapted from Arco Design DatePicker (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/DatePicker
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Keeps the single-day picker: a text field, a month grid with prev/next navigation,
 * today and a clear action. Omits the range picker, time and week/quarter/year modes,
 * shortcuts, timezones, disabledDate and ConfigProvider — none of them is used here, and
 * each would drag in another dependency. The value stays a `YYYY-MM-DD` string rather
 * than exposing `Dayjs` in the public props.
 * Visual rules come from the console's theme-ve-o-aml-design skin.
 */
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import dayjs from 'dayjs';

import { toApiDate } from './api-date.js';
import { parseDate, shiftMonth } from './calendar.js';
import { DatePickerPopup } from './date-picker-popup.js';

export interface DatePickerProps {
  /** 受控值，`YYYY-MM-DD`；空串表示未选。Controlled value; an empty string means none. */
  value: string;
  /** 值变化；清空时传空串。Called with the next value, empty on clear. */
  onChange: (value: string) => void;
  /** 无障碍名称。Accessible name of the field. */
  'aria-label'?: string;
  /** 占位文案。Placeholder copy. */
  placeholder?: string;
  /** 禁用。Disables the control. */
  disabled?: boolean;
  /**
   * 月份与周标题的语言，BCP 47 标签，默认 `zh-CN`。交给 `Intl` 解析，因此传任何
   * 受支持的语言都成立。
   *
   * Language of the month and weekday headings as a BCP 47 tag, `zh-CN` by default.
   * It goes through `Intl`, so any supported locale works.
   */
  locale?: string;
  /** 上一月、下一月的无障碍名称。Accessible names for the month navigation. */
  prevLabel?: string;
  nextLabel?: string;
  /** 清空按钮的无障碍名称。Accessible name of the clear action. */
  clearLabel?: string;
  /**
   * 展开日历按钮的无障碍名称。与输入框分开命名，否则屏幕阅读器会读到两个同名
   * 控件，用户分不清哪个能打字、哪个只展开日历。
   *
   * Accessible name of the calendar toggle. It must differ from the field's, or a screen
   * reader announces two controls with the same name and the user cannot tell which one
   * takes typing and which only opens the calendar.
   */
  calendarLabel?: string;
}

/**
 * 单日选择器：一个文本框加一个日历弹层。
 *
 * 没有 `RangePicker`——时间筛选是两个独立的日期字段，各自选一天，范围由调用方
 * 组合。这样每个组件都只做一件事，也不必把范围高亮、跨月悬停这些逻辑带进来。
 *
 * A single-day picker: a text field and a calendar popup.
 *
 * There is no `RangePicker` — a time filter is two independent date fields, each picking
 * one day, with the range assembled by the caller. That keeps each component to one job
 * and leaves out range highlighting and cross-month hover entirely.
 */
export function DatePicker({
  value,
  onChange,
  placeholder,
  disabled,
  locale = 'zh-CN',
  prevLabel = 'Previous month',
  nextLabel = 'Next month',
  clearLabel = 'Clear',
  calendarLabel = 'Open calendar',
  'aria-label': ariaLabel,
}: DatePickerProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const rootRef = useRef<HTMLDivElement>(null);
  // 点击日历格时输入框先 blur、后 click。blur 若照常提交，就会先按旧草稿发一次
  // onChange，紧接着 onPick 再发一次——宿主收到两次变更，中间还是用户没选的旧
  // 日期。pointerdown 在 blur 之前发生，因此在这里立起标记，blur 看到它就跳过。
  //
  // Clicking a calendar cell blurs the input first and clicks second. A blur that
  // committed normally would fire one onChange with the stale draft and another from
  // onPick — two changes, the first carrying a date the user never picked. pointerdown
  // runs before the blur, so a flag raised there makes the blur stand down.
  const pickingRef = useRef(false);
  const parsed = parseDate(value);
  const [view, setView] = useState(() => {
    const base = parsed ?? dayjs();
    return { year: base.year(), month: base.month() };
  });

  // 受控值变化时同步输入框与视图：宿主清空筛选后，输入框不能还留着旧日期。
  // 这是外部值到本地草稿的同步，渲染时算不出来，只能在值变化后跟随。
  //
  // Syncs the field and the view when the controlled value changes: after a host clears
  // the filter the field must not keep the old date. It mirrors an external value into
  // local draft state, which rendering cannot compute, so it follows the value.
  useEffect(() => {
    setDraft(value);
    const next = parseDate(value);
    if (next) setView({ year: next.year(), month: next.month() });
  }, [value]);

  // 与 document 的指针事件同步：点击弹层外部要收起。弹层挂在组件内部，只监听
  // 自身收不到外部点击；关闭或卸载时移除。
  //
  // Syncs with the document's pointer events to close on an outside click. The popup
  // lives inside this component, so a listener on it never sees that click; removed on
  // close or unmount.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      if (rootRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  const commit = (next: string) => {
    onChange(next);
    setOpen(false);
  };

  const commitDraft = () => {
    if (pickingRef.current) {
      pickingRef.current = false;
      return;
    }
    const next = parseDate(draft);
    if (next) commit(toApiDate(next));
    else setDraft(value);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      commitDraft();
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div className="ov-date-picker" ref={rootRef}>
      <div className="ov-date-picker__field">
        <input
          type="text"
          className="ov-date-picker__input"
          aria-label={ariaLabel}
          placeholder={placeholder}
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitDraft}
          onKeyDown={onKeyDown}
          onFocus={() => !disabled && setOpen(true)}
        />
        {value ? (
          <button
            type="button"
            className="ov-date-picker__clear"
            aria-label={clearLabel}
            disabled={disabled}
            onClick={() => commit('')}
          >
            <svg
              aria-hidden="true"
              width={12}
              height={12}
              viewBox="0 0 16 16"
              fill="none"
            >
              <path
                d="m4 4 8 8M12 4l-8 8"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        ) : null}
        <button
          type="button"
          className="ov-date-picker__calendar"
          aria-label={calendarLabel}
          aria-expanded={open}
          disabled={disabled}
          onClick={() => setOpen((current) => !current)}
        >
          <svg
            aria-hidden="true"
            width={14}
            height={14}
            viewBox="0 0 16 16"
            fill="none"
          >
            <rect
              x="2.5"
              y="3.5"
              width="11"
              height="10"
              rx="1.5"
              stroke="currentColor"
            />
            <path d="M2.5 6.5h11M5.5 2v2.5M10.5 2v2.5" stroke="currentColor" />
          </svg>
        </button>
      </div>
      {open ? (
        <div
          // 在捕获阶段标记「这次指针操作落在弹层里」，让紧随其后的输入框 blur
          // 不再按旧草稿提交。挂在外层而不是每一格上，切换月份与选中走同一条路径。
          //
          // Marks at the capture phase that this pointer interaction lands inside the
          // popup, so the input's blur that follows does not commit the stale draft.
          // It sits on the wrapper rather than each cell, so month navigation and
          // selection take the same path.
          onPointerDownCapture={() => {
            pickingRef.current = true;
          }}
          // 没有触发 blur 时（例如输入框本来就没聚焦）把标记清掉，避免它留到下一次
          // 真正的 blur 上白白吞掉一次提交。
          //
          // Clears the flag when no blur followed — the input may not have been focused
          // — so it cannot linger and swallow the next real blur's commit.
          onPointerUpCapture={() => {
            pickingRef.current = false;
          }}
        >
          <DatePickerPopup
            year={view.year}
            month={view.month}
            selected={parsed}
            locale={locale}
            prevLabel={prevLabel}
            nextLabel={nextLabel}
            onShiftMonth={(delta) =>
              setView((current) =>
                shiftMonth(current.year, current.month, delta),
              )
            }
            onPick={commit}
          />
        </div>
      ) : null}
    </div>
  );
}
