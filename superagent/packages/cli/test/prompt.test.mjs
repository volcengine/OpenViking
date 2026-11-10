// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { Prompter, PromptAborted } from '../src/prompt.mjs';

function piped(lines) {
  const input = new PassThrough();
  input.end(lines.map((line) => `${line}\n`).join(''));
  const output = new PassThrough();
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  return { prompter: new Prompter({ input, output }), written: () => written };
}

// A fake terminal: raw mode is a flag, keys are bytes typed after a tick.
function terminal() {
  const input = new PassThrough();
  input.isTTY = true;
  input.raw = false;
  input.setRawMode = (value) => { input.raw = value; };
  const output = new PassThrough();
  output.resume();
  const type = (...keys) => setImmediate(() => { for (const key of keys) input.write(key); });
  return { prompter: new Prompter({ input, output }), input, type };
}

const DOWN = '\x1b[B';
const UP = '\x1b[A';

test('piped answers: defaults, numbered choices, and a retry after an invalid answer', async () => {
  const { prompter, written } = piped(['', 'bad id', 'agent2', '2', '1,2', 'y']);
  assert.equal(await prompter.text('名字', { initial: 'Vika' }), 'Vika');
  assert.equal(await prompter.text('实例', { validate: (value) => (/\s/.test(value) ? '不能有空格' : null) }), 'agent2');
  assert.match(written(), /不能有空格/);
  assert.equal(await prompter.select('运行时', [
    { label: 'Codex', value: 'codex' }, { label: 'Pi', value: 'pi' },
  ]), 'pi');
  assert.deepEqual(await prompter.multiselect('IM', [
    { label: '飞书', value: 'lark' }, { label: '微信', value: 'weixin' }, { label: '邮箱', value: 'email', disabled: true },
  ]), ['lark', 'weixin']);
  assert.equal(await prompter.confirm('开始？'), true);
  await assert.rejects(prompter.text('再问一次'), PromptAborted);
});

test('piped answers cannot pick a disabled choice', async () => {
  const { prompter, written } = piped(['3', '1']);
  assert.deepEqual(await prompter.multiselect('IM', [
    { label: '飞书', value: 'lark' }, { label: '微信', value: 'weixin' }, { label: '邮箱', value: 'email', disabled: true },
  ]), ['lark']);
  assert.match(written(), /编号无效/);
});

test('terminal: arrows skip disabled rows and wrap; raw mode ends with the question', async () => {
  const { prompter, input, type } = terminal();
  const choices = [
    { label: 'Codex', value: 'codex', disabled: true }, { label: 'Claude', value: 'claude' }, { label: 'Pi', value: 'pi' },
  ];
  type(DOWN, '\r');
  assert.equal(await prompter.select('运行时', choices, { initial: 1 }), 'pi');
  assert.equal(input.raw, false);
  type(DOWN, '\r');
  assert.equal(await prompter.select('运行时', choices, { initial: 2 }), 'claude');
});

test('terminal: a multiselect needs at least one tick before enter is accepted', async () => {
  const { prompter, type } = terminal();
  type(' ', '\r', DOWN, ' ', UP, ' ', '\r');
  assert.deepEqual(await prompter.multiselect('IM', [
    { label: '飞书', value: 'lark' }, { label: '微信', value: 'weixin' },
  ], { initial: [0] }), ['lark', 'weixin']);
});

test('terminal: typed text, backspace, and ctrl-c', async () => {
  const { prompter, type } = terminal();
  type('a', 'b', 'x', '\x7f', '\r');
  assert.equal(await prompter.text('名字'), 'ab');
  type('\x03');
  await assert.rejects(prompter.text('名字'), PromptAborted);
});

test('terminal: a key can interrupt a wait, and a secret shows only stars', async () => {
  const { prompter, input, type } = terminal();
  const watch = prompter.watchKey('m');
  type('x', 'm');
  assert.equal(await watch.pressed, true);
  assert.equal(input.raw, false);
  const idle = prompter.watchKey('m');
  idle.stop();
  assert.equal(await idle.pressed, false);

  let echoed = '';
  prompter.output.on('data', (chunk) => { echoed += chunk; });
  type('s', '3', 'c', '\r');
  assert.equal(await prompter.secret('App Secret'), 's3c');
  assert.doesNotMatch(echoed, /s3c/);
  assert.match(echoed, /App Secret: \*\*\*\n/);

  // Nothing typed is not an answer; a paste arrives whole.
  echoed = '';
  const asked = prompter.secret('授权码');
  type('\r');
  await new Promise((resolve) => setTimeout(resolve, 10));
  type('abcd efgh', 'x', '\x7f', '\r');
  assert.equal(await asked, 'abcd efgh');
  assert.match(echoed, /没有收到输入/);
  assert.match(echoed, /授权码: \*{10}\x08 \x08\n/);
});
