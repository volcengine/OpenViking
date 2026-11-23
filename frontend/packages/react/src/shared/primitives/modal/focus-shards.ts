import { createContext, useContext } from 'react';
import type { RefObject } from 'react';

/**
 * 把 portal 出去的浮层登记进所属 Modal 的焦点范围。
 *
 * 浮层渲染到 `document.body` 后，DOM 上已不在弹窗里，`FocusLock` 的 Tab 序列
 * 因此看不到它：焦点会在弹窗内部循环，浮层里的按钮永远按不到。登记之后
 * `FocusLock` 把浮层当作自己的一个 shard，Tab 能进去，也能从里面出来。
 *
 * Registers a portaled surface into its Modal's focus scope.
 *
 * Once a surface renders into `document.body` it is no longer a DOM descendant of the
 * dialog, so `FocusLock`'s tab sequence cannot see it: focus cycles inside the dialog
 * and the surface's buttons are unreachable. Registering it makes `FocusLock` treat the
 * surface as one of its shards, so Tab enters and leaves it.
 */
export type RegisterFocusShard = (
  shard: RefObject<HTMLElement | null>,
) => () => void;

export const ModalFocusShardContext = createContext<RegisterFocusShard | null>(
  null,
);

/** 最近一层 Modal 的 shard 登记函数；不在 Modal 里时为 `null`。 */
export function useModalFocusShard(): RegisterFocusShard | null {
  return useContext(ModalFocusShardContext);
}
