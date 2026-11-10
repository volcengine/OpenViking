// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { BANNER_WIDTH, bannerLines, openingBanner, playBanner } from '../src/banner.mjs';
import { Prompter } from '../src/prompt.mjs';
import { urlOpenCommand, qrLines, showScan } from '../src/qr.mjs';
import { ansi256, box, colorDepth, mix, stripAnsi, width } from '../src/ui.mjs';

// A colour terminal of a given size, recording what is written.
function screen({ columns = 100, rows = 40, depth = 24 } = {}) {
  const output = new PassThrough();
  Object.assign(output, { isTTY: true, columns, rows, getColorDepth: (env = {}) => (env.NO_COLOR ? 1 : depth) });
  let written = '';
  output.on('data', (chunk) => { written += chunk; });
  return { output, written: () => written };
}

// Decode the RGB cells actually sent to a terminal, including backgrounds.
function rgbCells(line) {
  const cells = [];
  let fg = null, bg = null;
  for (const match of line.matchAll(/\x1b\[([0-9;]*)m|([^\x1b])/gu)) {
    if (match[1] === undefined) cells.push({ ch: match[2], fg, bg });
    else {
      const params = match[1].split(';').map(Number);
      if (params[0] === 0) { fg = null; bg = null; }
      else {
        assert.equal(params[1], 2, 'RGB output on a truecolor terminal');
        if (params[0] === 38) fg = params.slice(2);
        else if (params[0] === 48) bg = params.slice(2);
      }
    }
  }
  return cells;
}

test('widths count CJK as two columns and escape codes as none', () => {
  assert.equal(width('abc'), 3);
  assert.equal(width('飞书 · 微信'), 11);
  assert.equal(width('\x1b[38;2;1;2;3m飞\x1b[0m'), 2);
  assert.equal(stripAnsi('\x1b[1mOV\x1b[22m'), 'OV');
});

test('colours fall back to 256 colours, and nothing without a terminal', () => {
  assert.equal(ansi256([255, 0, 0]), 196);
  assert.equal(ansi256([128, 128, 128]), 244);
  assert.deepEqual(mix([[0, 0, 0], [200, 100, 0]], 0.5), [100, 50, 0]);
  assert.equal(colorDepth(new PassThrough()), 1);
  assert.equal(colorDepth(screen({ depth: 8 }).output, { LC_TERMINAL: 'iTerm2' }), 24, 'iTerm2 over ssh still has truecolor');
});

test('a box lines up its borders whatever the text is', () => {
  const { output } = screen();
  const lines = stripAnsi(box(['机器人  小V · Codex', 'IM  飞书'], { output, title: '将添加实例 primary' })).split('\n');
  assert.equal(new Set(lines.map(width)).size, 1, lines.join('\n'));
});

test('the banner fits 80 columns and spells OpenViking SuperAgent, never VIKA', () => {
  const lines = bannerLines({ depth: 24, version: '0.4.0-cdb22789fc75' });
  assert.equal(lines.length, 6);
  assert.ok(lines.every((line) => width(line) <= Math.min(80, BANNER_WIDTH)), lines.map(stripAnsi).join('\n'));
  assert.equal(stripAnsi(lines[0]).trim(), 'O P E N V I K I N G');
  assert.match(stripAnsi(lines.at(-1)), /v0\.4\.0-cdb22789fc75$/);
  assert.doesNotMatch(lines.join('\n'), /VIKA/);
  // No indigo / violet / blue: every colour leans red.
  for (const line of lines) for (const cell of rgbCells(line)) for (const rgb of [cell.fg, cell.bg]) {
    if (rgb && Math.max(...rgb) - Math.min(...rgb) > 40) assert.ok(rgb[0] > rgb[2], `${rgb}`);
  }
});

test('the full banner is drawn once at 80 columns with no tagline', async () => {
  const { output, written } = screen({ columns: 80, rows: 30 });
  const banner = await openingBanner({ output, input: new PassThrough(), env: {}, version: '1.0.0' });
  assert.equal(banner.top(), null);
  const plain = stripAnsi(written());
  assert.match(plain, /O P E N V I K I N G/);
  assert.ok(plain.split('\n').every((line) => width(line) <= 80));
  assert.match(written(), /\x1b\[38;2;/);
  assert.doesNotMatch(plain, /aboard|带上船/);
  assert.match(plain, /v1\.0\.0/);
  assert.doesNotMatch(written(), /VIKA/);
});

test('narrow, colourless, CI and short terminals get one title line', async () => {
  const expected = () => '▲ OpenViking SuperAgent  v1.0.0';
  for (const [size, env, tag] of [
    [{ columns: 40 }, {}, false],
    [{ columns: 64 }, {}, false],
    [{ columns: 100 }, { NO_COLOR: '1' }, true],
    [{ columns: 100 }, { CI: '1' }, true],
    [{ columns: 100, rows: 6 }, {}, true],
    [{ columns: 100, depth: 1 }, {}, true],
  ]) {
    const { output, written } = screen(size);
    await playBanner({ output, input: new PassThrough(), env, version: '1.0.0' });
    assert.equal(stripAnsi(written()).trim(), expected(tag), JSON.stringify([size, env]));
    assert.ok(stripAnsi(written()).split('\n').every((line) => width(line) <= size.columns));
    if (env.NO_COLOR || size.depth === 1) assert.doesNotMatch(written(), /\x1b\[/);
  }
});

test('on a colour terminal an answered question folds into one line', async () => {
  const { output, written } = screen();
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const prompter = new Prompter({ input, output, env: {} });
  assert.equal(prompter.fancy, true);
  setImmediate(() => input.write('小V\r'));
  assert.equal(await prompter.text('机器人名字', { initial: 'OpenViking SuperAgent' }), '小V');
  setImmediate(() => input.write('\x1b[B\r'));
  assert.equal(await prompter.select('运行时', [{ label: 'Codex', value: 'codex' }, { label: 'Pi', value: 'pi' }]), 'pi');
  const plain = stripAnsi(written());
  assert.match(plain, /✔ 机器人名字 · 小V\n/);
  assert.match(plain, /✔ 运行时 · Pi\n/);
});

test('going back takes the screen back: below a pinned header the questions are drawn again from the record', () => {
  const { output, written } = screen();
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const prompter = new Prompter({ input, output, env: {} });
  prompter.pageTop = () => 18;
  prompter.write('kept\n');
  const mark = prompter.mark();
  prompter.write('gone\n');
  prompter.rewind(mark);
  assert.ok(written().endsWith('\x1b[18;1H\x1b[Jkept\n'));
  assert.deepEqual(prompter.transcript, ['kept\n']);
  // Without a pinned header, the rows written since are cleared in place.
  prompter.pageTop = null;
  const later = prompter.mark();
  prompter.write('one\ntwo\n');
  prompter.rewind(later);
  assert.ok(written().endsWith('\r\x1b[2A\x1b[J'));
});

test('a code too big for the window waits for the window to grow, then draws itself', () => {
  const url = `https://open.feishu.cn/page/cli?addons=${'x'.repeat(1500)}`;
  const qrWidth = width(qrLines(url)[0]);
  assert.ok(qrWidth > 120, `Feishu-sized codes are wide (${qrWidth})`);
  const { output, written } = screen({ columns: 100, rows: 40 });
  const prompter = new Prompter({ input: new PassThrough(), output, env: {} });
  const scan = showScan(prompter, { url, title: '第 1 次扫码', env: {} });
  assert.match(stripAnsi(written()), /终端放不下这个二维码（要 \d+×\d+，现在 100×40）：缩小字体或拉大窗口，它会自动出现；也可以打开图片 \S+scan\.png/);
  assert.doesNotMatch(written(), /[▀▄█]/);
  Object.assign(output, { columns: 200, rows: 120 });
  output.emit('resize');
  assert.match(written(), /[▀▄█]/);
  scan.stop();
  assert.equal(output.listenerCount('resize'), 0);
});

test('a link opens in a browser only where there is a desktop', () => {
  assert.equal(urlOpenCommand({ DISPLAY: ':0' }), process.platform === 'win32' ? 'explorer.exe' : process.platform === 'darwin' ? 'open' : 'xdg-open');
  assert.equal(urlOpenCommand({}), process.platform === 'win32' ? 'explorer.exe' : process.platform === 'darwin' ? 'open' : null);
});
