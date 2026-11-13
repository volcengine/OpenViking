import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mount } from './helpers/command-console-dom.mjs';
import {
  hover,
  resize,
  setDescWidth,
} from './helpers/quick-command-widths.mjs';

const commands = [
  {
    name: 'ls',
    description: 'List directory entries',
    usage: 'ov ls [uri]',
  },
  {
    name: 'find',
    summary: 'Find matching context',
    description: 'Unused when a summary exists',
    usage: 'ov find <query>',
  },
];

const actions = (container) => [
  ...container.querySelectorAll('.ov-command-console__empty-action'),
];

test('a row shows its description on hover only when a summary hides it', async () => {
  const withSummary = await mount({
    commands,
    quickCommands: [{ name: 'find', value: '' }],
  });
  try {
    assert.equal(
      await hover(actions(withSummary.container)[0]),
      'Unused when a summary exists',
    );
  } finally {
    await withSummary.dispose();
  }
  const withoutSummary = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(await hover(actions(withoutSummary.container)[0]), null);
  } finally {
    await withoutSummary.dispose();
  }
});

test('a truncated description keeps its tooltip without a summary', async () => {
  // 'List directory entries' 是 22 个字符，自然宽度 176px；容器给 80px 便截断。
  // The 22-character description measures 176px; an 80px container truncates it.
  setDescWidth(80);
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});

test('a description that fits keeps no tooltip and gains one when truncated', async () => {
  setDescWidth(1000);
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(await hover(actions(view.container)[0]), null);
    // 容器收窄后重新测量，Tooltip 随之出现。
    // Re-measuring after the container narrows brings the tooltip back.
    setDescWidth(80);
    await resize();
    assert.equal(
      await hover(actions(view.container)[0]),
      'List directory entries',
    );
    // 恢复宽度后说明完整可见，Tooltip 撤销。
    // Widening again makes the whole description visible, so the tooltip goes.
    setDescWidth(1000);
    await resize();
    assert.equal(await hover(actions(view.container)[0]), null);
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});

test('narrowing then widening repeatedly keeps tracking the current node', async () => {
  // 覆盖评审指出的节点替换问题：Tooltip 开关会替换子树，若观察器盯着旧节点，
  // 旧节点脱离文档后宽度为 0，第二轮收窄就再也测不到截断。
  //
  // Covers the stale-node regression: toggling the tooltip replaces the subtree,
  // and a watcher bound to the old node reads 0 once it detaches, so the second
  // narrowing would never be detected.
  setDescWidth(1000);
  const view = await mount({
    commands,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    for (const width of [80, 1000, 80, 1000, 80]) {
      setDescWidth(width);
      await resize();
      const expected = width < 176 ? 'List directory entries' : null;
      assert.equal(await hover(actions(view.container)[0]), expected);
    }
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});

test('a longer description at the same width gains its tooltip', async () => {
  // 单行文本变长不改变节点盒尺寸，ResizeObserver 不会回调；只有按内容重测才
  // 能发现新的截断。这里不触发 resize，只替换 commands 的文案。
  //
  // A longer single-line text keeps the node's box unchanged, so the observer
  // never fires; only a content-driven re-measure reveals the new truncation.
  // No resize is fired here — only the command copy changes.
  setDescWidth(200);
  const short = [{ name: 'ls', description: 'Short', usage: 'ov ls [uri]' }];
  const view = await mount({
    commands: short,
    quickCommands: [{ name: 'ls', value: '' }],
  });
  try {
    assert.equal(await hover(actions(view.container)[0]), null);
    await view.render({
      commands: [
        {
          name: 'ls',
          description: 'A much longer description that no longer fits the row',
          usage: 'ov ls [uri]',
        },
      ],
      quickCommands: [{ name: 'ls', value: '' }],
    });
    assert.equal(
      await hover(actions(view.container)[0]),
      'A much longer description that no longer fits the row',
    );
  } finally {
    await view.dispose();
    setDescWidth(1000);
  }
});
