// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { bannerLines, miniLines, openingBanner, playBanner, sceneFrame } from '../src/banner.mjs';
import { Prompter } from '../src/prompt.mjs';
import { opener, qrLines, showScan } from '../src/qr.mjs';
import { ansi256, box, colorDepth, mix, stripAnsi, width } from '../src/ui.mjs';

// A colour terminal of a given size, recording what is written.
function screen({ columns = 100, rows = 40, depth = 24 } = {}) {
  const output = new PassThrough();
  Object.assign(output, { isTTY: true, columns, rows, getColorDepth: () => depth });
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
  assert.equal(stripAnsi('\x1b[1mVIKA\x1b[22m'), 'VIKA');
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

test('every banner frame fits the terminal; a narrow one drops the wordmark', () => {
  for (const t of [0, 0.37, 1.2, 2.9, 7.5]) {
    const wide = bannerLines(t, { depth: 24, version: '0.4.0-cdb22789fc75', columns: 100 });
    assert.equal(wide.length, 16);
    assert.ok(wide.every((line) => width(line) < 100), `t=${t}`);
    assert.ok(wide.some((line) => stripAnsi(line).includes('██╗   ██╗██╗██╗')));
    const narrow = bannerLines(t, { depth: 8, columns: 60 });
    assert.ok(narrow.every((line) => width(line) < 60) && !narrow.some((line) => line.includes('██╗')));
    for (const columns of [46, 47, 60, 72, 73, 80, 100]) {
      const mini = miniLines(t, { depth: 24, columns });
      assert.ok(mini.every((line) => width(line) < columns), `t=${t}, columns=${columns}`);
    }
  }
});

test('the banner is one plain title line on a terminal that is too small or has no colour', async () => {
  const small = screen({ columns: 40 });
  await playBanner({ output: small.output, input: new PassThrough(), env: {} });
  assert.equal(stripAnsi(small.written()).trim(), 'VIKA  把本机的 coding agent 带上船');
  const edge = screen({ columns: 46 });
  const edgeBanner = await openingBanner({ output: edge.output, input: new PassThrough(), env: {} });
  assert.equal(edgeBanner.top(), null);
  assert.equal(stripAnsi(edge.written()).trim(), 'VIKA  把本机的 coding agent 带上船');
  const still = screen();
  await playBanner({ output: still.output, input: new PassThrough(), env: { VIKA_NO_ANIMATION: '1' } });
  assert.equal(stripAnsi(still.written()).split('\n').filter((line) => line.includes('██╗   ██╗██╗██╗')).length, 1, 'one frame, drawn once');
});

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('the truecolor banner preserves subtle shading while bounding glyph variants over time', () => {
  const variants = new Set();
  // Sample across ten minutes, including fractional times between animation ticks.
  for (let frame = 0; frame < 180; frame += 1) {
    const t = frame * 3.337;
    for (const line of [...bannerLines(t, { depth: 24, columns: 100 }), ...miniLines(t, { depth: 24, columns: 60 })]) {
      for (const cell of rgbCells(line)) if (cell.ch !== ' ') variants.add(JSON.stringify(cell));
    }
  }
  assert.ok(variants.size < 3000, `${variants.size} distinct glyph/color combinations`);
  for (const t of [0, 0.37, 1.2, 2.9, 7.5]) {
    const original = sceneFrame(t);
    const rendered = bannerLines(t, { depth: 24, columns: 60 }).map(rgbCells);
    original.forEach((row, y) => row.forEach((cell, x) => {
      if (cell.ch === ' ') return;
      assert.equal(rendered[y][x].ch, cell.ch);
      for (const [source, actual] of [[cell.rgb, rendered[y][x].fg], [cell.bg, rendered[y][x].bg]]) {
        if (!source) { assert.equal(actual, null); continue; }
        assert.equal(actual.length, 3);
        source.forEach((value, channel) => assert.ok(Math.abs(value - actual[channel]) <= 12,
          `t=${t}, cell=${x},${y}, channel=${channel}: ${value} -> ${actual[channel]}`));
      }
    }));
  }
});

test('playback and both live headers preserve truecolor, with a 256-color fallback', async () => {
  for (const depth of [8, 24]) {
    for (const rows of [26, 40]) {
      const { output, written } = screen({ depth, rows });
      const banner = await openingBanner({ output, input: new PassThrough(), env: {}, durationMs: 0 });
      banner.release();
      assert.match(written(), depth === 24 ? /\x1b\[38;2;/ : /\x1b\[38;5;/);
      assert.doesNotMatch(written(), depth === 24 ? /\x1b\[(?:38|48);5;/ : /\x1b\[(?:38|48);2;/);
    }
  }
});

test('on a tall window the boat stays at the top and keeps sailing until released', async () => {
  const { output, written } = screen({ columns: 100, rows: 40 });
  const banner = await openingBanner({ output, input: new PassThrough(), env: {}, version: '1.0.0' });
  assert.match(written(), /\x1b\[18;40r\x1b\[18;1H/, 'questions scroll below the 16-row scene');
  await pause(200);
  const frames = written().split('\x1b7').length - 1;
  assert.ok(frames >= 2, `redrawn while waiting (${frames} frames)`);
  banner.release();
  assert.ok(written().endsWith('\x1b7\x1b[r\x1b8'), 'the whole screen is the terminal\'s again');
  const after = written().length;
  await pause(150);
  assert.equal(written().length, after, 'no frames after release');
  banner.resume();
  assert.match(written().slice(after), /\x1b\[18;40r/);
  banner.release();
  banner.release();
  assert.equal(process.listenerCount('SIGINT'), 0);
});

test('on a short window the scene plays, then a small boat stays at the top', async () => {
  const { output, written } = screen({ columns: 100, rows: 26 });
  const banner = await openingBanner({ output, input: new PassThrough(), env: {}, durationMs: 50 });
  assert.match(stripAnsi(written()), /██╗   ██╗██╗██╗/, 'the full scene first');
  assert.match(written(), /\x1b\[4;26r/, 'then a two-row header');
  banner.release();
});

test('on a colour terminal an answered question folds into one line', async () => {
  const { output, written } = screen();
  const input = new PassThrough();
  Object.assign(input, { isTTY: true, setRawMode() {} });
  const prompter = new Prompter({ input, output, env: {} });
  assert.equal(prompter.fancy, true);
  setImmediate(() => input.write('小V\r'));
  assert.equal(await prompter.text('机器人名字', { initial: 'Vika' }), '小V');
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
  assert.equal(opener({}, 'darwin'), 'open');
  assert.equal(opener({ DISPLAY: ':0' }, 'linux'), 'xdg-open');
  assert.equal(opener({}, 'linux'), null, 'a server over ssh');
});
