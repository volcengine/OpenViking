// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { Prompter, PromptAborted } from '../src/prompt.mjs';
import { stripAnsi } from '../src/ui.mjs';

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

// A colour terminal: the drawn questions, which need both ends to be terminals.
function drawnTerminal() {
  const input = new PassThrough();
  input.isTTY = true;
  input.raw = false;
  input.setRawMode = (value) => { input.raw = value; };
  const output = new PassThrough();
  Object.assign(output, { isTTY: true, columns: 80, getColorDepth: () => 24 });
  let shown = '';
  output.on('data', (chunk) => { shown += chunk; });
  const type = (...keys) => setImmediate(() => { for (const key of keys) input.write(key); });
  return { prompter: new Prompter({ input, output, env: {} }), input, type, shown: () => shown };
}

const DOWN = '\x1b[B';
const UP = '\x1b[A';

// A terminal without colours: the same questions, in plain text.
function plainTerminal() {
  const input = new PassThrough();
  input.isTTY = true;
  input.raw = false;
  input.setRawMode = (value) => { input.raw = value; };
  const output = new PassThrough();
  Object.assign(output, { isTTY: true, columns: 80, getColorDepth: () => 1 });
  let shown = '';
  output.on('data', (chunk) => { shown += chunk; });
  const type = (...keys) => setImmediate(() => { for (const key of keys) input.write(key); });
  return { prompter: new Prompter({ input, output, env: {} }), input, type, shown: () => shown };
}

test('piped answers: defaults, numbered choices, and a retry after an invalid answer', async () => {
  const { prompter, written } = piped(['', 'bad id', 'agent2', '2', '1,2', 'y']);
  assert.equal(await prompter.text('名字', { initial: 'OpenViking SuperAgent' }), 'OpenViking SuperAgent');
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

test('piped answers can explicitly select none when a multiselect allows it', async () => {
  const { prompter } = piped(['0']);
  assert.deepEqual(await prompter.multiselect('技能目录', [
    { label: '工作区', value: 'workspace' }, { label: '宿主', value: 'host' },
  ], { initial: [0, 1], min: 0 }), []);
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

test('terminal: a yes/no question is typed and then submitted, as text is', async () => {
  const { prompter, type, shown } = drawnTerminal();
  assert.equal(prompter.fancy, true, 'the drawn branch is the one under test');
  // The key shows on screen and only Enter answers; a later y/n replaces the
  // earlier one, so a slip of the finger is not an answer.
  type('y', 'n', '\r');
  assert.equal(await prompter.confirm('开机后自动启动 OpenViking SuperAgent？'), false);
  assert.match(stripAnsi(shown()), /\(Y\/n\) › y\x08 \x08n/, 'the key is echoed and can be replaced');
  assert.match(stripAnsi(shown()), /· 否\n$/, 'the question folds into 否');

  // Backspace clears what is typed, so Enter takes the value in brackets.
  type('n', '\x7f', '\r');
  assert.equal(await prompter.confirm('再来一次？', true), true);
  // Nothing typed at all is the value in brackets too, and either case answers.
  type('\r');
  assert.equal(await prompter.confirm('再来一次？', false), false);
  type('Y', '\r');
  assert.equal(await prompter.confirm('再来一次？', false), true);
  // ← while nothing is typed leaves the question, as in a text field.
  const { prompter: navigable, type: typeAgain } = drawnTerminal();
  navigable.navigable = true;
  const back = navigable.confirm('开始？');
  typeAgain('\x1b[D');
  await assert.rejects(back, { name: 'GoBack' });
});

test('terminal: a confirm that is typed into and then interrupted leaves raw mode', async () => {
  const { prompter, input, type } = drawnTerminal();
  type('y', '\x03');
  await assert.rejects(prompter.confirm('开始？'), PromptAborted);
  assert.equal(input.raw, false);
});

test('terminal: a yes/no question gives up when no key ever arrives', async () => {
  const { prompter, input, shown } = drawnTerminal();
  const started = Date.now();
  assert.equal(await prompter.confirm('开始？', true, { timeoutMs: 80 }), null, 'no answer, not yes and not no');
  assert.ok(Date.now() - started >= 60, 'it waited for the answer first');
  assert.equal(input.raw, false, 'raw mode is given back');
  assert.match(stripAnsi(shown()), /· （没有回答）\n$/, 'the question folds, saying nothing came');
});

test('terminal: a line-mode yes/no takes y, n or Enter, and anything else is asked again', async () => {
  const { prompter, input, type, shown } = drawnTerminal();
  type('y', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), true);
  type('\r');
  assert.equal(await prompter.confirm('开始？', false, { line: true }), false, 'Enter takes the value in brackets');
  type('n', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), false);
  type('maybe', '\r', 'yes', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), true);
  assert.match(stripAnsi(shown()), /输入 y 或 n 再回车/, 'a word that is not y/n says what is expected');
  assert.equal(input.raw, false, 'the console keeps its own line mode');
  assert.equal(await prompter.confirm('开始？', true, { line: true, timeoutMs: 60 }), null, 'no line at all is still an answer-less end');
});

test('plain terminal: the yes/no question reads the line the console completes, and gives up too', async () => {
  const { prompter, input, type, shown } = plainTerminal();
  assert.equal(prompter.fancy, false, 'the colours are off');
  type('y', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), true);
  type('\r');
  assert.equal(await prompter.confirm('开始？', false, { line: true }), false, 'Enter takes the value in brackets');
  type('n', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), false);
  type('maybe', '\r', 'yes', '\r');
  assert.equal(await prompter.confirm('开始？', true, { line: true }), true);
  assert.match(shown(), /不是 y 或 n/, 'a word that is not y/n says what is expected');
  assert.equal(input.raw, false, 'the console keeps its own line mode');
  const started = Date.now();
  assert.equal(await prompter.confirm('开始？', true, { line: true, timeoutMs: 60 }), null, 'no line at all is still an answer-less end');
  assert.ok(Date.now() - started >= 40, 'it waited for the answer first');
});
