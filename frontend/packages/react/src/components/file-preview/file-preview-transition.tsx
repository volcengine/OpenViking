import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  LEVEL_TRANSITION_GRACE_MS,
  shouldCommitProbe,
} from './level-transition-phase.js';
import type { ProbeStatus, TransitionPhase } from './level-transition-phase.js';
import { LoadingState } from './file-preview-state.js';
import type { FilePreviewLabels } from './types.js';

export { needsAsyncLoad } from './media-kind.js';
export { probeStatusOf } from './level-transition-phase.js';
export type { ProbeStatus } from './level-transition-phase.js';

/**
 * 换层级时正文的过渡：旧内容留在屏幕上，新内容在它背后加载，就绪后无缝换上。
 *
 * 为什么需要：底座把每一次 `source` 变化都当成换了一个文件，先渲染加载态再
 * 渲染正文。层级切换只换一份摘要，这个来回会把内容区先清空再填满——实测约两
 * 帧，读起来是「闪一下」。
 *
 * 两段时间：
 *
 * - 宽限期 150ms：旧内容仍可见。新内容在这段时间内就绪就无缝替换，用户看不到
 *   任何空白。
 * - 最短停留 250ms：宽限期用尽后加载态露出，并至少停留这么久。否则内容紧随
 *   阈值就绪时，加载态会一闪而过——只是把闪动挪了个位置。
 *
 * 两个槽位而不是一个：就绪的那一份必须在「隐藏时已经建好」的 DOM 上原地显示，
 * 换 key 重新渲染等于重来一遍，闪动照旧。因此槽位身份固定，切换只翻转哪一个
 * 可见，旧槽位在新槽位可见后才卸载。
 *
 * The body's transition while a level changes: the previous content stays on screen
 * while the new one loads behind it and swaps in when ready. The base treats every
 * `source` change as a different file and renders a loading state first, which empties
 * and refills the body — about two frames, read as a flash.
 *
 * Two slots rather than one: the ready content must appear on the DOM already built
 * while hidden; re-rendering it under a new key would start over and flash again. Slot
 * identity is fixed, a switch only flips which one is visible, and the old slot
 * unmounts after the new one is on screen.
 */
export interface TransitionLayer<T> {
  targetKey?: string;
  tab: T;
  /** 这一层在后台加载，尚未显示。 This layer loads behind the scenes. */
  probe: boolean;
  /** 后台层把状态报回来；前台层传的是空操作。 */
  /** The background layer reports back; the foreground one gets a no-op. */
  onProbeStatus: (status: ProbeStatus) => void;
  /**
   * 这一层自己还在显示加载态时调用，结束时传 `false`。底座的外壳进入 `ready`
   * 之后，渲染器自己的遮罩还会停留一两帧，光等外壳会把它露出来。
   *
   * Called while this layer still shows its own loading state, `false` when it ends.
   * After the shell reaches `ready` the renderer's overlay lingers a frame or two, so
   * the shell alone is not enough.
   */
  onLoadingChange: (loading: boolean) => void;
  /**
   * 这一层是否该向外上报句柄与视图模式。后台层必须为 `false`：两个实例同时在
   * 树上，后台那个交出的句柄会覆盖屏幕上这个的，视图模式也会被它改写。
   *
   * Whether this layer may report handles and view modes outward. `false` for the
   * background one: both instances are on the tree, so the background handle would
   * overwrite the visible one's and its view modes would replace them too.
   */
  reports: boolean;
}

type SlotName = 'a' | 'b';
interface Slot<T> {
  targetKey?: string;
  identity: string;
  tab: T;
}

const noop = () => {};

/**
 * @param identity 当前内容身份。文件页签切换层级时页签 key 不变、只有 `source`
 *   变，因此不能拿 key 当身份，否则层级切换不会走过渡。
 * @param tab 当前内容，交给 `children` 渲染。
 */
export function FilePreviewTransition<T>({
  identity,
  tab,
  targetKey,
  needsLoad,
  text,
  children,
}: {
  identity: string;
  tab: T;
  targetKey?: string;
  needsLoad: boolean;
  text: Pick<FilePreviewLabels, 'loading'>;
  children: (layer: TransitionLayer<T>) => ReactNode;
}) {
  const [slots, setSlots] = useState<Record<SlotName, Slot<T> | null>>({
    a: { identity, tab, targetKey },
    b: null,
  });
  const [visible, setVisible] = useState<SlotName>('a');
  const [phase, setPhase] = useState<TransitionPhase>('idle');
  const [probeStatus, setProbeStatus] = useState<ProbeStatus>('pending');
  // 后台层是否仍在显示它自己的加载态。
  const [probeLoading, setProbeLoading] = useState(false);
  // 加载态露出的时刻，用来保证最短停留。
  const loadingAt = useRef<number | null>(null);
  const hidden: SlotName = visible === 'a' ? 'b' : 'a';
  const visibleSlot = slots[visible];
  const switching = !visibleSlot || visibleSlot.identity !== identity;

  // 目标变化：把新内容放进隐藏槽位并开始宽限期。不需要异步加载的内容直接换上。
  // 目标又回到屏幕上那一份时，整轮过渡作废：清掉后台槽位与所有过渡状态，否则
  // 遮罩会一直盖着已经回到视野的内容。
  //
  // useEffect 必要性：宽限期是「从现在起 150ms」，只能由定时器推进，渲染时算
  // 不出未来的时刻；目标再变时清除上一个定时器，旧计时不会提前结束新一轮。
  //
  // The grace period is "150ms from now", which only a timer can advance; the
  // previous timer is cleared when the target changes, so a stale tick cannot end a
  // newer grace early. A target that returns to what is already on screen cancels the
  // whole transition — leaving it running would keep the overlay over visible content.
  useEffect(() => {
    if (!switching) {
      setSlots((current) =>
        current[hidden] ? { ...current, [hidden]: null } : current,
      );
      setPhase('idle');
      setProbeStatus('pending');
      setProbeLoading(false);
      loadingAt.current = null;
      return;
    }
    setSlots((current) => ({
      ...current,
      [hidden]: { identity, tab, targetKey },
    }));
    setPhase(needsLoad ? 'grace' : 'idle');
    setProbeStatus('pending');
    setProbeLoading(false);
    loadingAt.current = null;
    if (!needsLoad) {
      setVisible(hidden);
      setSlots((current) => ({ ...current, [visible]: null }));
      return;
    }
    const timer = setTimeout(() => {
      loadingAt.current = Date.now();
      setPhase('loading');
    }, LEVEL_TRANSITION_GRACE_MS);
    return () => clearTimeout(timer);
    // `tab` 只在身份变化时被读取；对象身份每帧都新，不能进依赖。
    // `tab` is read only when the identity changes; it is a new object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, switching, needsLoad]);

  // 后台那份就绪：宽限期内立即换槽位；已经在显示加载态时，等它停满最短时间，
  // 否则内容紧随阈值就绪会让加载态一闪而过。失败态立即换上，不让兜底提示被
  // 加载遮罩挡住。后台层还在显示自己的遮罩时一律不换——外壳 `ready` 之后它还会
  // 再停一两帧，此时换上去会把「加载中」露出来。
  //
  // Ready swaps at once inside the grace period; once the loading state is up it waits
  // out the minimum so it never blinks. A failure swaps immediately so its message is
  // not hidden. Nothing swaps while the background layer still shows its own overlay:
  // it lingers a frame or two after the shell is ready.
  //
  // useEffect 必要性：最短停留的剩余时间只能对着墙钟算，且只应在加载态已经露
  // 出、内容已就绪时排期；重跑或卸载时清除定时器。
  useEffect(() => {
    if (!switching || probeStatus === 'pending' || probeLoading) return;
    const commit = () => {
      setSlots((current) => ({ ...current, [visible]: null }));
      setVisible(hidden);
      setPhase('idle');
      setProbeStatus('pending');
    };
    const { commit: now, waitMs } = shouldCommitProbe(
      phase,
      probeStatus,
      loadingAt.current,
      Date.now(),
      undefined,
      probeLoading,
    );
    if (now) {
      commit();
      return;
    }
    const timer = setTimeout(commit, waitMs);
    return () => clearTimeout(timer);
  }, [switching, probeStatus, probeLoading, phase, visible, hidden]);

  // 身份与当前目标一致的槽位跟随最新的页签对象：宿主每次渲染都会重建它，冻住
  // 旧对象会让保存后的正文、新的资源表都到不了屏幕。后台槽位同样要跟——宿主常
  // 先给一份 `loading: true` 的占位，再在同一个 key 下补上正文；冻结的话它会永远
  // 停在加载分支，既不挂载渲染器也不上报就绪，遮罩就再也撤不掉。只有身份已经
  // 落后的那一层（屏幕上正在被替换的旧内容）保持原样。
  //
  // 跟随时要把最新的对象写回槽位：下一轮切换里这一层会变成「旧内容」，那时读的
  // 就是槽位里的快照。只在这里返回最新对象而不落库，等它退居后台后会退回创建时
  // 的占位（例如最初的 loading 空壳），「旧内容留在屏幕上」就失效了。
  //
  // A slot whose identity matches the current target follows the latest tab object: the
  // host rebuilds it every render, and freezing the old one would keep a saved body or a
  // new resource list from reaching the screen. The background slot follows too — a host
  // often supplies a `loading: true` placeholder first and the body under the same key
  // afterwards; frozen, it would stay on the loading branch forever, never mounting the
  // renderer or reporting ready, and the overlay could never go away. Only a layer whose
  // identity has fallen behind — the old content being replaced — keeps its own object.
  //
  // Following also has to write the latest object back into the slot: in the next switch
  // this layer becomes the old content, and what it renders then is the snapshot stored
  // here. Returning the latest object without storing it would send it back to its
  // creation-time placeholder (the original empty loading shell) once it steps aside,
  // defeating "the previous content stays on screen".
  // 每层最后一次**真正提交到屏幕**的 tab。槽位退居后台后读它，屏幕因此保留用户
  // 实际看过的那份内容。
  //
  // 只在提交后写入，不能在渲染期间写：那次渲染可能被 Suspense 或 startTransition
  // 放弃，写进去的内容从未显示过。之后切走时就会从快照里取出一份没人看过的
  // 内容——若它还是 loading 占位，加载态会重新露出来。
  //
  // The tab each layer last actually **committed** to the screen. A slot reads it once
  // it steps aside, so the screen keeps what the user really saw.
  //
  // Written only after commit, never during render: a render can be abandoned by
  // Suspense or startTransition, and what it produced was never shown. Reading it back
  // after a later switch would surface content nobody saw — a loading placeholder would
  // reappear as a loading state.
  const shownTabs = useRef<
    Record<SlotName, { identity: string; tab: T } | null>
  >({
    a: { identity, tab },
    b: null,
  });
  // useEffect 必要性：提交后才更新快照，正是「只记录真正显示过的内容」这一要求
  // 本身——渲染阶段无法判断本次渲染会不会被放弃。没有需要清理的资源。
  //
  // The snapshot is updated after commit because "only what was really shown" cannot be
  // decided during render, where an abandoned attempt looks identical. Nothing to clean
  // up.
  useEffect(() => {
    for (const name of ['a', 'b'] as const) {
      const slot = slots[name];
      if (!slot) shownTabs.current[name] = null;
      else if (slot.identity === identity)
        shownTabs.current[name] = { identity, tab };
    }
  }, [slots, identity, tab]);
  const layerTab = (name: SlotName) => {
    const slot = slots[name];
    if (!slot) return null;
    // 身份与当前目标一致的槽位用实时的 tab；其余（正在退居后台的旧内容）用提交
    // 过的快照。
    //
    // The slot matching the current target uses the live tab; the others — old content
    // stepping aside — use the committed snapshot.
    return slot.identity === identity
      ? tab
      : (shownTabs.current[name]?.tab ?? slot.tab);
  };

  return (
    <div className="ov-file-preview__layers">
      {(['a', 'b'] as const).map((name) => {
        const slotTab = layerTab(name);
        if (!slotTab) return null;
        // 可见槽位在过渡期间继续显示旧内容——这正是宽限期要的效果。切换只翻转
        // 哪个槽位可见，不能因为「正在切换」就把两层都藏起来，那会空一帧。
        //
        // The visible slot keeps showing the old content during a transition — that
        // is the whole point of the grace period. A switch only flips which slot is
        // visible; hiding both because "a switch is in flight" would blank a frame.
        const onScreen = name === visible;
        return (
          <div
            key={name}
            className={
              onScreen
                ? 'ov-file-preview__layer'
                : 'ov-file-preview__layer ov-file-preview__layer--hidden'
            }
            aria-hidden={onScreen ? undefined : true}
          >
            {children({
              tab: slotTab,
              targetKey:
                slots[name]?.identity === identity
                  ? targetKey
                  : slots[name]?.targetKey,
              probe: !onScreen,
              onProbeStatus: onScreen ? noop : setProbeStatus,
              onLoadingChange: onScreen ? noop : setProbeLoading,
              // 后台层即使已经 `ready` 也还不能代表屏幕：它的句柄属于另一份内容。
              // A background layer that is already `ready` still does not speak for
              // the screen: its handle belongs to different content.
              reports: onScreen,
            })}
          </div>
        );
      })}
      {phase === 'loading' ? (
        <div className="ov-file-preview__layers-loading">
          <LoadingState text={text} />
        </div>
      ) : null}
    </div>
  );
}
