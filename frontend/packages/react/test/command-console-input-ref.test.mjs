import assert from 'node:assert/strict';
import test from 'node:test';
import { createRef, version } from 'react';
import { mount, key } from './helpers/command-console-dom.mjs';

test('object input refs follow replacement and unmount without breaking keyboard recall', async () => {
  const first = createRef();
  const second = createRef();
  const command = 'ov read viking://resources/demo';
  const host = await mount({ inputRef: first, history: [{ command }] });
  try {
    assert.equal(first.current, host.input);
    await key(host.input, 'ArrowUp');
    assert.equal(host.input.value, command);
    await host.render({ inputRef: second });
    assert.equal(first.current, null);
    assert.equal(second.current, host.input);
  } finally {
    await host.dispose();
  }
  assert.equal(second.current, null);
});

test('callback input refs receive replacement and unmount notifications', async () => {
  const first = [];
  const second = [];
  const host = await mount({
    inputRef: (node) => {
      first.push(node);
    },
  });
  const input = host.input;
  try {
    assert.deepEqual(first, [input]);
    await host.render({
      inputRef: (node) => {
        second.push(node);
      },
    });
    assert.deepEqual(first, [input, null]);
    assert.deepEqual(second, [input]);
  } finally {
    await host.dispose();
  }
  assert.deepEqual(second, [input, null]);
});

test(
  'React 19 callback input ref cleanups run on replacement and unmount',
  {
    skip: Number.parseInt(version, 10) < 19,
  },
  async () => {
    const attached = [];
    const released = [];
    const ref = (name) => (node) => {
      attached.push([name, node]);
      return () => {
        released.push(name);
      };
    };
    const host = await mount({ inputRef: ref('first') });
    const input = host.input;
    try {
      assert.deepEqual(attached, [['first', input]]);
      await host.render({ inputRef: ref('second') });
      assert.deepEqual(attached, [
        ['first', input],
        ['second', input],
      ]);
      assert.deepEqual(released, ['first']);
    } finally {
      await host.dispose();
    }
    assert.deepEqual(released, ['first', 'second']);
  },
);
