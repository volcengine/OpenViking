import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from './helpers/command-console-dom.mjs';
import {
  hover,
  observedLists,
  resize,
  setDescWidth,
} from './helpers/quick-command-widths.mjs';

const commands = [
  {
    name: 'ls',
    description: 'List directory entries',
    usage: 'ov ls [uri]',
  },
];

const actions = (container) => [
  ...container.querySelectorAll('.ov-command-console__empty-action'),
];

const withCommand = {
  commands,
  quickCommands: [{ name: 'ls', value: '' }],
};

test('a list that appears after mount still gets observed', async () => {
  // 空态区域在没有匹配到快捷命令时照样挂载、但不渲染列表。订阅若只在首次挂载
  // 时建立，这里会因 ref 为空而跳过，之后列表出现也不会被观察。
  //
  // The empty region mounts without a list when no quick command resolves. A
  // subscription built only on first mount would skip it and never observe the
  // list that appears later.
  setDescWidth(1000);
  const view = await mount({ commands, quickCommands: [] });
  try {
    assert.equal(observedLists(), 0, 'no list, nothing to observe');
    await view.render(withCommand);
    assert.equal(observedLists(), 1, 'the list that appeared is observed');
    setDescWidth(80);
    await resize();
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});

test('StrictMode keeps the list observed', async () => {
  // StrictMode 会重放 effect 的 setup→cleanup→setup，但不重新调用 DOM callback
  // ref。若另有 effect 在 cleanup 里断开订阅，这里就再也没人恢复它。
  //
  // StrictMode replays an effect's setup→cleanup→setup without re-invoking DOM
  // callback refs, so a cleanup that disconnects the subscription leaves nothing
  // to restore it.
  setDescWidth(1000);
  const view = await mount(withCommand, true, { strict: true });
  try {
    assert.equal(
      observedLists(),
      1,
      'the list stays observed under StrictMode',
    );
    setDescWidth(80);
    await resize();
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});

test('a list hidden and shown again is observed afresh', async () => {
  // 列表消失后旧观察器绑在已卸载的节点上；恢复时必须重新订阅当前列表，否则
  // 之后的宽度变化收不到。
  //
  // Once the list goes away the old observer holds an unmounted node; showing it
  // again has to subscribe to the new one, or later resizes go unheard.
  setDescWidth(1000);
  const view = await mount(withCommand);
  try {
    assert.equal(observedLists(), 1);
    await view.render({ commands, quickCommands: [] });
    assert.equal(observedLists(), 0, 'the old list is no longer observed');
    await view.render(withCommand);
    assert.equal(observedLists(), 1, 'the new list is observed');
    setDescWidth(80);
    await resize();
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});
