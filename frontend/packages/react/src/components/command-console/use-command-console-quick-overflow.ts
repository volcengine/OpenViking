import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

// 服务端渲染没有布局，useLayoutEffect 在那里只会告警；测量只在浏览器有意义。
// A server render has no layout, so a layout effect would only warn there.
const useMeasureEffect =
  typeof window === 'undefined' ? useEffect : useLayoutEffect;

const DESC_SELECTOR = '.ov-command-console__empty-desc';

/**
 * 跟踪哪些快捷命令的说明被单行省略截断，返回列表的 callback ref 与截断项下标。
 *
 * 说明是单行省略的文本，容器变窄或文案变长时会被截断，行内便看不到全文。
 *
 * 返回 callback ref 而不是 ref 对象：空态区域在快捷命令尚未匹配到、或宿主提供
 * 了 `emptyState` 时照样挂载却不渲染列表，此时没有可观察的节点。用 effect 建订阅
 * 会在这时提前返回，而空依赖的 effect 之后不会再跑，列表后来出现就永远没有观察器；
 * 列表隐藏再恢复时，旧观察器也仍绑在已卸载的节点上。callback ref 在节点挂载与
 * 卸载时各触发一次，订阅始终跟随当前列表。
 *
 * useLayoutEffect 必要性：是否截断取决于容器的布局宽度与文本的自然宽度，二者
 * 只有提交后才可测量，无法在渲染或事件里算出；窗口缩放和面板宽度变化都会改变
 * 结果。
 *
 * 观察列表容器，而不是说明节点：说明是网格列里的单行文本，宽度由列宽决定，
 * 文本溢出时它自己的 content box 不变，观察它收不到回调。容器宽度随面板与窗口
 * 变化，是可靠的触发源。
 *
 * 文案变长不改变任何盒尺寸，观察器不会回调，因此每次渲染后再测一次：测量只读
 * scrollWidth/clientWidth，不写 DOM，代价是一次同步布局查询。
 *
 * Tracks which quick command descriptions the single-line ellipsis truncates,
 * returning the list's callback ref and the truncated indices.
 *
 * A layout effect is required: truncation depends on the container's laid-out
 * width and the text's natural width, measurable only after commit, and both
 * change with the window and the panel.
 *
 * It returns a callback ref rather than a ref object: the empty region mounts
 * while no quick command resolves or while the host supplies `emptyState`, and
 * renders no list then. An effect would return early with no node to observe, and
 * an empty-dependency effect never runs again, so a list that appears later would
 * never get an observer; a list hidden and shown again would keep the observer on
 * the unmounted node. A callback ref fires on both mount and unmount, so the
 * subscription always follows the current list.
 *
 * The list container is observed rather than the description nodes: each
 * description is a single line inside a grid column, so its width follows the
 * column and its own content box does not change when the text overflows —
 * watching it receives no callback. The container's width tracks the panel and
 * the window.
 *
 * Longer copy changes no box at all, so the observer never fires; measuring after
 * every render covers it. The measurement only reads `scrollWidth`/`clientWidth`
 * and writes no DOM, so it costs one synchronous layout query.
 */
export function useCommandConsoleQuickOverflow() {
  const listRef = useRef<HTMLUListElement | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const [truncated, setTruncated] = useState<readonly number[]>([]);

  const measure = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    const next: number[] = [];
    list.querySelectorAll<HTMLElement>(DESC_SELECTOR).forEach((node, index) => {
      if (node.scrollWidth > node.clientWidth) next.push(index);
    });
    // 只在结果变化时提交，避免每次渲染都触发一轮新渲染。
    // Commit only on change so a render does not schedule another one.
    setTruncated((current) =>
      current.length === next.length &&
      current.every((value, index) => value === next[index])
        ? current
        : next,
    );
  }, []);

  const attachList = useCallback(
    (list: HTMLUListElement | null) => {
      listRef.current = list;
      observerRef.current?.disconnect();
      observerRef.current = null;
      if (!list) {
        // 列表消失后下标不再对应任何一行，清空避免残留。
        // Without a list the indices point at nothing; drop them.
        setTruncated((current) => (current.length ? [] : current));
        return;
      }
      const observer = new ResizeObserver(measure);
      observer.observe(list);
      observerRef.current = observer;
      measure();
    },
    [measure],
  );

  // 每次渲染后补测一次，覆盖文案变长这类不改变盒尺寸的变化。
  // Re-measure after every render to cover changes that resize no box, such as
  // longer copy.
  useMeasureEffect(measure);

  // 订阅的建立与释放全部交给 callback ref：它在节点挂载时收到列表、卸载时收到
  // null。不要再加一个「卸载时断开」的 effect——StrictMode 会重放 effect 的
  // setup→cleanup→setup，却不会重新调用 DOM callback ref，cleanup 断开的订阅
  // 便无人恢复。
  //
  // The callback ref owns both ends of the subscription: it receives the list on
  // mount and null on unmount. Do not add an "disconnect on unmount" effect —
  // StrictMode replays an effect's setup→cleanup→setup without re-invoking DOM
  // callback refs, so a cleanup that disconnects leaves nothing to restore it.
  return { listRef: attachList, truncated };
}
