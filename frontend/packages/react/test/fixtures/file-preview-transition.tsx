import { FilePreview } from '@openviking/react/components/file-preview';
import type { ReactElement } from 'react';
import { useState } from 'react';
import { createRoot } from 'react-dom/client';

/**
 * 过渡层的两个边界场景，供打包后由 JSDOM 驱动：
 *
 * - `cancel`：切到另一级、等遮罩露出、再切回屏幕上那一级，遮罩与后台槽位都应消失。
 * - `late`：宿主先给一份 `loading` 占位，随后在同一层级补上正文，遮罩应能撤掉。
 *
 * Two edges of the transition, driven from JSDOM after bundling: `cancel` switches
 * away, waits for the overlay, then returns to the level on screen; `late` supplies a
 * loading placeholder first and the body under the same level afterwards.
 */
const level = (key: string, content: string) => ({
  key,
  label: key,
  source: { content, fileName: `${key}.md` },
});

function CancelCase() {
  const [active, setActive] = useState('L0');
  return (
    <FilePreview
      tabs={[
        {
          kind: 'file',
          key: 'guide',
          source: { content: '# A', fileName: 'a.md' },
          activeLevelKey: active,
          onLevelChange: setActive,
          levels: [level('L0', '# A'), level('L1', '# B')],
        },
      ]}
    />
  );
}

function LateCase() {
  // 先显示 L0，用户切到 L1 时宿主给一份仍在 loading 的正文占位，随后在同一层级
  // 补上正文：后台槽位必须跟着换，否则它永远停在加载分支，遮罩再也撤不掉。
  //
  // 占位正文必须非空：空正文走同步分支、不进过渡，那样测不到后台槽位。
  //
  // L0 shows first; switching to L1 makes the host hand over a non-empty placeholder
  // that is still loading, then the body under the same level. The background slot has
  // to follow, or it stays on the loading branch and the overlay never goes away.
  //
  // The placeholder must be non-empty: an empty body takes the synchronous branch and
  // never enters the transition, which would not exercise the slot at all.
  const [active, setActive] = useState('L0');
  const [ready, setReady] = useState(false);
  (window as unknown as { __setLateReady: () => void }).__setLateReady = () =>
    setReady(true);
  return (
    <FilePreview
      tabs={[
        {
          kind: 'file',
          key: 'guide',
          source: { content: '# A', fileName: 'a.md' },
          activeLevelKey: active,
          onLevelChange: setActive,
          loading: active === 'L1' && !ready,
          levels: [
            level('L0', '# A'),
            level('L1', ready ? '# late body' : '# placeholder'),
          ],
        },
      ]}
    />
  );
}

const cases: Record<string, () => ReactElement> = {
  cancel: CancelCase,
  late: LateCase,
};

const Case = cases[new URLSearchParams(location.search).get('case') ?? ''];
createRoot(document.getElementById('root')!).render(Case ? <Case /> : null);
