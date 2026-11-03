// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { t } from './i18n.mjs';
import { PALETTE, SYMBOLS, colorDepth, mix, rowsOf, stripAnsi, width } from './ui.mjs';
import { qrMatrix, qrPng, terminalQr } from './qr-code.mjs';

// A code to scan, drawn as soon as the window can hold it all and replaced
// in place when the packing must change. A link and, where a desktop can
// open it, a PNG of the same code are the ways round a code that does not
// scan. Feishu's app-creation link carries the whole permission set (~1700
// characters): 2×2 cells fit the width of an 80-column window, but the
// height is still ~70 rows, so a short window (Cursor's default 80×25)
// opens the PNG instead of stacking broken copies on resize. A link that
// long is shown only on request, never pushing the code away.

const LONG_LINK = 300;

export function qrLines(url) {
  return terminalQr(qrMatrix(url));
}

// The program that opens a link or image on this machine's desktop; none on a server.
export function opener(env = process.env, platform = process.platform) {
  if (platform === 'darwin') return 'open';
  if (platform === 'win32') return 'cmd';
  if (platform === 'linux' && (env.DISPLAY || env.WAYLAND_DISPLAY)) return 'xdg-open';
  return null;
}

function launchArgs(program, target) {
  if (program === 'cmd') return ['/c', 'start', '', target];
  if (program === 'code') return ['--reuse-window', target];
  return [target];
}

export function openUrl(url, { env = process.env, platform = process.platform } = {}) {
  const program = opener(env, platform);
  if (!program) return false;
  try {
    spawn(program, launchArgs(program, url), { detached: true, stdio: 'ignore', windowsHide: true })
      .on('error', () => {}).unref();
    return true;
  } catch {
    return false;
  }
}

const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function oneLine(text, columns) {
  if (width(text) < columns) return text;
  let result = '';
  for (const character of stripAnsi(text)) {
    if (width(result + character) >= columns - 2) break;
    result += character;
  }
  return `${result}…`;
}

// prompter: the setup Prompter. where: what to open the link in (飞书 / 微信).
// stopKey: a key that ends the wait (resolves `pressed` with true); `keys`
// are more keys with their labels and actions, e.g. { o: ['浏览器打开', fn] };
// onBack, when given, runs on ← or Esc (going back while nothing is made yet).
// Returns { imagePath, pressed, status(label), say(text), stop() }.
export function showScan(prompter, {
  url, title, where = t('飞书', 'Feishu/Lark'), label = t('等待扫码', 'Waiting for the scan'),
  stopKey = null, stopLabel = '', keys = {}, onBack = null, env = process.env,
}) {
  const { output, s } = prompter;
  const matrix = qrMatrix(url);
  const depth = s.enabled ? colorDepth(output, env) : 1;
  // Without keys to press, the link is printed however long it is.
  const linkShown = url.length <= LONG_LINK || !prompter.tty;
  const lead = t(`扫不出来的话，在${where}里打开：`, `Can't scan it? Open in ${where}: `);
  const linkLine = prompter.fancy ? `  ${s.gray(lead)}${s.underline(s.accent(url))}\n` : `${lead}${url}\n`;
  const below = linkShown ? linkLine : '';
  const extra = 2 + (linkShown ? 1 : 0);
  const halfHeight = Math.ceil(matrix.length / 2);
  function packFor(columns, rows) {
    if (!output.isTTY) return 1;
    if ((columns ?? 0) > matrix.length && (rows ?? 0) >= halfHeight + extra) return 1;
    if ((columns ?? 0) > Math.ceil(matrix.length / 2) && (rows ?? 0) >= halfHeight + extra) return 2;
    return 0;
  }
  const need = { width: Math.ceil(matrix.length / 2) + 1, rows: halfHeight + extra };
  function body(pack) {
    if (!pack) return `${warning()}${below}`;
    return `${terminalQr(matrix, depth, pack).join('\r\n')}\r\n${below}`;
  }
  let imageDirectory, imagePath;
  const cleanupImage = () => {
    try { if (imageDirectory) fs.rmSync(imageDirectory, { recursive: true, force: true }); } catch { /* Best effort on exit. */ }
  };
  try {
    imageDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-qr-'));
    imagePath = path.join(imageDirectory, 'scan.png');
    fs.writeFileSync(imagePath, qrPng(matrix), { mode: 0o600, flag: 'wx' });
    process.on('exit', cleanupImage);
  } catch { cleanupImage(); imageDirectory = null; imagePath = null; }
  const canOpen = Boolean(opener(env));
  // Preview / Photos / xdg-open is what a phone can scan; `code` is a last resort.
  const imageOpener = opener(env) || ((env.TERM_PROGRAM === 'vscode' || env.VSCODE_IPC_HOOK_CLI) ? 'code' : null);
  const unopened = () => say(t(`打不开，图片在 ${imagePath}`, `Could not open it; the image is at ${imagePath}`));
  const openImage = () => {
    if (!imagePath || !imageOpener) return;
    try {
      spawn(imageOpener, launchArgs(imageOpener, imagePath),
        { detached: true, stdio: 'ignore', env, windowsHide: true }).on('error', unopened).unref();
    } catch { unopened(); }
  };
  const actions = {
    ...(canOpen ? { o: [t('浏览器打开', 'open in browser'), () => {
      if (openUrl(url, { env })) say(t('已在浏览器打开，按页面提示确认', 'Opened in the browser; follow the page'));
    }] } : {}),
    ...(linkShown ? {} : { l: [t('显示链接', 'show link'), () => print(linkLine)] }),
    ...(imagePath && imageOpener ? { i: [t('看图片', 'open image'), openImage] } : {}), ...keys,
  };

  let ticker = null;
  let frame = 0;
  let current = label;
  const started = Date.now();
  const hints = [...Object.entries(actions).map(([key, [text]]) => [key, text]), ...(stopKey ? [[stopKey, stopLabel]] : []),
    ...(onBack ? [['←', t('返回', 'back')]] : [])];
  const line = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (!prompter.fancy) {
      const keysText = t(`（${hints.map(([key, text]) => `按 ${key} ${text}`).join('，')}）`, ` (${hints.map(([key, text]) => `${key}: ${text}`).join(', ')})`);
      return `  ${current} ${clock}${hints.length ? keysText : ''}`;
    }
    const glyph = s.paint(FRAMES[frame % FRAMES.length], mix(PALETTE.sail, frame / 24, { cycle: true }));
    const keysText = hints.map(([key, text]) => `${s.bold(key)} ${text}`).join(s.gray(' · '));
    return `  ${glyph} ${current} ${s.gray(clock)}${hints.length ? `   ${s.gray('│')} ${keysText}` : ''}`;
  };
  const draw = () => { output.write(`\r\x1b[2K${oneLine(line(), output.columns || 80)}`); frame += 1; };
  let extraRows = 0;
  const print = (text) => {
    if (ticker) output.write('\r\x1b[2K');
    output.write(text);
    extraRows += rowsOf(String(text).replace(/\r\n/g, '\n'), output.columns || 80);
    if (ticker) draw();
  };
  function say(text) {
    print(prompter.fancy ? `  ${s.gray(SYMBOLS.info)} ${text}\n` : `  ${text}\n`);
  }

  const head = prompter.fancy ? `\n  ${s.accent('▣')} ${s.bold(title)}\n` : `\n${title}\n`;
  const warning = () => {
    const size = `${output.columns ?? '?'}×${output.rows ?? '?'}`;
    const hint = imagePath && imageOpener
      ? t('；也可以按 i 打开图片扫', '; or press i to open the image')
      : imagePath ? t(`；也可以打开图片 ${imagePath}`, `; or open the image ${imagePath}`) : '';
    const text = t(`终端放不下这个二维码（要 ${need.width}×${need.rows}，现在 ${size}）：缩小字体或拉大窗口，它会自动出现`,
      `This code needs a ${need.width}×${need.rows} terminal (now ${size}): shrink the font or enlarge the window and it appears`)
      + hint;
    return prompter.fancy ? `  ${s.yellow(SYMBOLS.warn)} ${text}\n` : `${text}\n`;
  };
  let pack = packFor(output.columns, output.rows);
  let written = body(pack);
  let writtenRows = rowsOf(written.replace(/\r\n/g, '\n'), output.columns || 80);
  output.write(head);
  output.write(written);
  if (!pack && prompter.tty) openImage();

  const replaceBody = (next) => {
    if (ticker) output.write('\r\x1b[2K');
    const up = writtenRows + extraRows;
    if (output.isTTY && up > 0) output.write(`\x1b[${up}A\r\x1b[J`);
    output.write(next);
    written = next;
    writtenRows = rowsOf(next.replace(/\r\n/g, '\n'), output.columns || 80);
    extraRows = 0;
    if (ticker) draw();
  };
  const onResize = () => {
    const next = packFor(output.columns, output.rows);
    if (next === pack) return;
    pack = next;
    replaceBody(body(pack));
  };
  output.on?.('resize', onResize);

  let watch = { pressed: new Promise(() => {}), stop() {} };
  if (prompter.tty) {
    watch = prompter.watchKey(stopKey, { also: Object.fromEntries(Object.entries(actions).map(([key, [, run]]) => [key, run])), onBack });
    draw();
    ticker = setInterval(draw, prompter.fancy ? 90 : 1000);
  }
  return {
    imagePath,
    pressed: watch.pressed,
    status(next) { current = next; if (ticker) draw(); },
    say,
    stop() {
      if (ticker) { clearInterval(ticker); ticker = null; output.write('\r\x1b[2K'); }
      output.off?.('resize', onResize);
      watch.stop();
      process.off('exit', cleanupImage);
      cleanupImage();
    },
  };
}
