/**
 * ⌘K 的实例仲裁。
 *
 * 一个页面可能挂多个 `FileBrowser`。若每个实例都响应 document 级的快捷键，一次
 * ⌘K 会让所有实例同时弹出搜索框。这里记录挂载顺序与最近交互的实例：有交互时
 * 由交互过的那个响应，没有任何交互时由第一个挂载的响应——页面刚加载、焦点还在
 * `body` 上时 ⌘K 依然有效。
 *
 * 这不是业务状态的单例：它只裁决按键归属，不保存任何模块数据。
 *
 * Arbitration for ⌘K.
 *
 * A page can mount several `FileBrowser` instances. If every one answered the
 * document-level shortcut, a single ⌘K would open all their dialogs at once. This
 * tracks mount order and the instance interacted with last: while there is one, it
 * answers; before any interaction the first mounted one does, so ⌘K still works right
 * after load while focus is on `body`.
 *
 * This is not a singleton of business state: it only decides who receives a key
 * press and stores no module data.
 */

const mounted: symbol[] = [];
let lastActive: symbol | null = null;

/** 登记一个挂载的实例。Registers a mounted instance. */
export function registerInstance(id: symbol): void {
  mounted.push(id);
}

/**
 * 注销实例；它若是最近交互的那个，交还给当前第一个实例。
 *
 * 这里必须把 `lastActive` 清掉：留下一个已经卸载的 id 会让后续每次按键都判定为
 * 「不是它」，⌘K 从此失效。模块级的登记表由挂载与卸载成对维护，因此测试之间也
 * 会自然复位，不需要额外的重置入口。
 *
 * Unregisters an instance, handing ownership back to the first one when this was the
 * most recently interacted.
 *
 * `lastActive` must be cleared here: leaving a symbol whose component has unmounted makes
 * every later key press resolve to "not it", and ⌘K stops working for good. The
 * module-level registry is maintained by paired mount and unmount, so it resets between
 * tests on its own and needs no separate reset entry point.
 */
export function unregisterInstance(id: symbol): void {
  const index = mounted.indexOf(id);
  if (index >= 0) mounted.splice(index, 1);
  if (lastActive === id) lastActive = null;
}

/** 记录某个实例刚被交互过。Records that an instance was just interacted with. */
export function activateInstance(id: symbol): void {
  lastActive = id;
}

/**
 * 这次按键是否该由该实例处理。`container` 用于确认交互确实发生在这个实例内部。
 *
 * Whether this instance should handle the key press. `container` confirms the
 * interaction happened inside this instance.
 */
export function ownsShortcut(
  id: symbol,
  container: HTMLElement | null,
): boolean {
  if (lastActive !== null) return lastActive === id;
  if (container?.contains(document.activeElement)) return true;
  return mounted[0] === id;
}
