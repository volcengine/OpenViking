import assert from 'node:assert/strict';
import test from 'node:test';

// 只测过渡的判定逻辑，不经过组件与底座：底座的 Arco 依赖用目录导入，只有打包器
// 能解析，jsdom 里导入它会报错。这些纯函数决定了切换时机，正是最容易出错的地方。
//
// Only the transition's decision logic, never the component or the base: the base's
// Arco dependency uses directory imports that only a bundler resolves. These pure
// functions decide when to swap, which is exactly where the bugs live.
const {
  LEVEL_TRANSITION_GRACE_MS,
  LEVEL_TRANSITION_MIN_VISIBLE_MS,
  needsAsyncLoad,
  probeStatusOf,
  shouldCommitProbe,
} = await import('../src/components/file-preview/level-transition-phase.ts');

test('the grace period is shorter than the minimum a loading state stays', () => {
  assert.equal(LEVEL_TRANSITION_GRACE_MS, 150);
  assert.equal(LEVEL_TRANSITION_MIN_VISIBLE_MS, 250);
  // 最短停留必须长于宽限期，否则「宽限期用尽即露出」的加载态一出现就会被撤掉。
  // The minimum must exceed the grace period, or a loading state shown when the grace
  // runs out would be withdrawn immediately.
  assert.ok(LEVEL_TRANSITION_MIN_VISIBLE_MS > LEVEL_TRANSITION_GRACE_MS);
});

test('content ready inside the grace period swaps without ever showing loading', () => {
  const decision = shouldCommitProbe('grace', 'ready', null, 1000);
  assert.deepEqual(decision, { commit: true, waitMs: 0 });
});

test('content ready after the loading state appears waits out the minimum', () => {
  // 加载态在第 150ms 露出，内容第 200ms 就绪：还要再等 200ms 才换上，
  // 否则加载态只闪 50ms。
  // Loading appeared at 150ms and content is ready at 200ms: it must stay another
  // 200ms, or the loading state would blink for 50ms.
  const decision = shouldCommitProbe('loading', 'ready', 150, 200);
  assert.equal(decision.commit, false);
  assert.equal(decision.waitMs, 200);
});

test('content ready after the minimum has elapsed swaps at once', () => {
  const decision = shouldCommitProbe('loading', 'ready', 150, 500);
  assert.deepEqual(decision, { commit: true, waitMs: 0 });
});

test('a layer still showing its own loading never swaps, even once the shell is ready', () => {
  // 外壳 `ready` 之后渲染器自己的遮罩还会停一两帧；此时换上去会把它露出来。
  // The renderer's overlay lingers after the shell reaches `ready`; swapping then
  // would put it on screen.
  const decision = shouldCommitProbe(
    'grace',
    'ready',
    null,
    1000,
    undefined,
    true,
  );
  assert.deepEqual(decision, { commit: false, waitMs: 0 });
});

test('a pending probe never swaps; a failed one swaps at once', () => {
  assert.deepEqual(shouldCommitProbe('grace', 'pending', null, 0), {
    commit: false,
    waitMs: 0,
  });
  assert.deepEqual(shouldCommitProbe('loading', 'pending', 150, 900), {
    commit: false,
    waitMs: 0,
  });
  // 失败立即换上，不让兜底提示被加载遮罩挡住。
  // A failure swaps immediately so its message is not hidden behind the overlay.
  assert.deepEqual(shouldCommitProbe('loading', 'failed', 150, 160), {
    commit: true,
    waitMs: 0,
  });
});

test('empty files and media need no async load; everything else does', () => {
  assert.equal(needsAsyncLoad({ content: '', fileName: 'a.md' }), false);
  assert.equal(
    needsAsyncLoad({ content: 'x', fileName: 'a.png', fileType: 'image/png' }),
    false,
  );
  assert.equal(needsAsyncLoad({ content: '# x', fileName: 'a.md' }), true);
});

test('only ready counts as ready; terminal states swap in as failures', () => {
  assert.equal(probeStatusOf('ready'), 'ready');
  assert.equal(probeStatusOf('resolving'), 'pending');
  assert.equal(probeStatusOf('loading'), 'pending');
  assert.equal(probeStatusOf('preparing'), 'pending');
  assert.equal(probeStatusOf('unsupported'), 'failed');
  assert.equal(probeStatusOf('error'), 'failed');
});
