// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The look of the vika command: colour, gradients, display widths, spinners
// and boxes. Fancy only on an interactive terminal; piped output stays plain
// text, so scripts and logs read the same words without escape codes.

const ESC = '\x1b[';
export const RESET = `${ESC}0m`;
export const HIDE_CURSOR = `${ESC}?25l`;
export const SHOW_CURSOR = `${ESC}?25h`;

// Vika's colours: the sail gradient (also the wordmark), the sea, and accents.
export const PALETTE = Object.freeze({
  sail: [[255, 106, 213], [199, 116, 232], [173, 140, 255], [135, 149, 232], [148, 208, 255]],
  sea: [[28, 84, 178], [40, 146, 232], [64, 214, 232]],
  foam: [210, 245, 255],
  hull: [[214, 120, 98], [255, 170, 120]],
  skin: [255, 224, 180],
  star: [255, 250, 220],
  accent: [148, 208, 255],
  violet: [173, 140, 255],
  pink: [255, 106, 213],
  green: [80, 220, 140],
  yellow: [255, 204, 102],
  red: [255, 107, 107],
  dim: [128, 136, 150],
});

// 24 (truecolor), 8 (256 colours), 4 (16 colours) or 1 (none). Node reads
// NO_COLOR, FORCE_COLOR, TERM and COLORTERM; iTerm2 also says who it is in
// LC_TERMINAL, which ssh forwards, so a remote shell in iTerm2 gets truecolor.
export function colorDepth(stream = process.stdout, env = process.env) {
  if (!stream?.isTTY || typeof stream.getColorDepth !== 'function') return 1;
  const depth = stream.getColorDepth(env);
  if (depth === 8 && (env.LC_TERMINAL === 'iTerm2' || /kitty|ghostty|wezterm|alacritty/i.test(env.TERM ?? ''))) return 24;
  return depth;
}

const CUBE = [0, 95, 135, 175, 215, 255];

function nearestCube(value) {
  let best = 0;
  for (let index = 1; index < CUBE.length; index += 1) {
    if (Math.abs(CUBE[index] - value) < Math.abs(CUBE[best] - value)) best = index;
  }
  return best;
}

const distance = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;

export function ansi256([r, g, b]) {
  const cube = [nearestCube(r), nearestCube(g), nearestCube(b)];
  const cubeColor = cube.map((index) => CUBE[index]);
  const level = Math.max(0, Math.min(23, Math.round(((r + g + b) / 3 - 8) / 10)));
  const gray = 8 + level * 10;
  return distance([gray, gray, gray], [r, g, b]) < distance(cubeColor, [r, g, b])
    ? 232 + level
    : 16 + 36 * cube[0] + 6 * cube[1] + cube[2];
}

const BASIC = [
  [0, 0, 0], [205, 49, 49], [13, 188, 121], [229, 229, 16], [36, 114, 200], [188, 63, 188], [17, 168, 205], [229, 229, 229],
  [102, 102, 102], [241, 76, 76], [35, 209, 139], [245, 245, 67], [59, 142, 234], [214, 112, 214], [41, 184, 219], [255, 255, 255],
];

export function ansi16(rgb) {
  let best = 0;
  for (let index = 1; index < BASIC.length; index += 1) if (distance(BASIC[index], rgb) < distance(BASIC[best], rgb)) best = index;
  return best < 8 ? 30 + best : 90 + best - 8;
}

export function fgCode(rgb, depth) {
  if (!rgb || depth < 4) return '';
  if (depth >= 24) return `${ESC}38;2;${rgb[0]};${rgb[1]};${rgb[2]}m`;
  if (depth >= 8) return `${ESC}38;5;${ansi256(rgb)}m`;
  return `${ESC}${ansi16(rgb)}m`;
}

export function bgCode(rgb, depth) {
  if (!rgb || depth < 4) return '';
  if (depth >= 24) return `${ESC}48;2;${rgb[0]};${rgb[1]};${rgb[2]}m`;
  if (depth >= 8) return `${ESC}48;5;${ansi256(rgb)}m`;
  return `${ESC}${ansi16(rgb) + 10}m`;
}

// A colour along the stops, t in [0, 1]; `cycle` wraps back to the first stop.
export function mix(stops, t, { cycle = false } = {}) {
  if (stops.length === 1) return stops[0];
  const list = cycle ? [...stops, stops[0]] : stops;
  const clamped = cycle ? ((t % 1) + 1) % 1 : Math.max(0, Math.min(1, t));
  const position = clamped * (list.length - 1);
  const index = Math.min(list.length - 2, Math.floor(position));
  const local = position - index;
  return list[index].map((channel, k) => Math.round(channel + (list[index + 1][k] - channel) * local));
}

export function scale(rgb, factor) {
  return rgb.map((channel) => Math.max(0, Math.min(255, Math.round(channel * factor))));
}

// Display columns: ANSI codes take none, CJK and emoji take two.
const ANSI_PATTERN = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07/g;

export function stripAnsi(text) {
  return String(text).replace(ANSI_PATTERN, '');
}

export function charWidth(code) {
  if (code < 0x20 || (code >= 0x7f && code < 0xa0)) return 0;
  if (code >= 0x300 && code <= 0x36f) return 0;
  if (code === 0x200b || code === 0x200d || (code >= 0xfe00 && code <= 0xfe0f)) return 0;
  if ((code >= 0x1100 && code <= 0x115f) || (code >= 0x2e80 && code <= 0xa4cf) || (code >= 0xac00 && code <= 0xd7a3)
    || (code >= 0xf900 && code <= 0xfaff) || (code >= 0xfe30 && code <= 0xfe4f) || (code >= 0xff00 && code <= 0xff60)
    || (code >= 0xffe0 && code <= 0xffe6) || (code >= 0x1f300 && code <= 0x1faff) || (code >= 0x20000 && code <= 0x3fffd)) return 2;
  return 1;
}

export function width(text) {
  let columns = 0;
  for (const char of stripAnsi(text)) columns += charWidth(char.codePointAt(0));
  return columns;
}

// How many terminal rows the text takes once the terminal wraps it.
export function rowsOf(text, columns) {
  if (!columns) return String(text).split('\n').length;
  return String(text).split('\n').reduce((sum, line) => sum + Math.max(1, Math.ceil(width(line) / columns)), 0);
}

export function padEnd(text, columns) {
  return `${text}${' '.repeat(Math.max(0, columns - width(text)))}`;
}

// Paint helpers for one output stream. With no colour they return the text as is.
export function styler(stream = process.stdout, env = process.env) {
  const depth = colorDepth(stream, env);
  const on = depth >= 4;
  const wrap = (open, close) => (text) => (on ? `${open}${text}${close}` : String(text));
  const s = {
    depth,
    enabled: on,
    paint: (text, rgb) => (on ? `${fgCode(rgb, depth)}${text}${RESET}` : String(text)),
    bold: wrap(`${ESC}1m`, `${ESC}22m`),
    dim: wrap(`${ESC}2m`, `${ESC}22m`),
    italic: wrap(`${ESC}3m`, `${ESC}23m`),
    underline: wrap(`${ESC}4m`, `${ESC}24m`),
    // Each character its own colour along the stops; `phase` slides the
    // gradient, so redrawing with a growing phase makes it flow.
    gradient(text, stops = PALETTE.sail, { phase = 0, cycle = false } = {}) {
      if (!on) return String(text);
      const chars = [...String(text)];
      const visible = Math.max(1, chars.length - 1);
      let result = '';
      chars.forEach((char, index) => {
        result += char === ' ' ? char : `${fgCode(mix(stops, index / visible + phase, { cycle: cycle || phase !== 0 }), depth)}${char}`;
      });
      return `${result}${RESET}`;
    },
  };
  s.accent = (text) => s.paint(text, PALETTE.accent);
  s.violet = (text) => s.paint(text, PALETTE.violet);
  s.pink = (text) => s.paint(text, PALETTE.pink);
  s.green = (text) => s.paint(text, PALETTE.green);
  s.yellow = (text) => s.paint(text, PALETTE.yellow);
  s.red = (text) => s.paint(text, PALETTE.red);
  s.gray = (text) => s.paint(text, PALETTE.dim);
  return s;
}

export const SYMBOLS = Object.freeze({
  active: '◆', done: '✔', fail: '✖', warn: '▲', info: '›', pointer: '❯',
  radioOn: '●', radioOff: '○', boxOn: '◼', boxOff: '◻', bar: '│',
});

// Ctrl-C while the terminal is in a state of ours (a hidden cursor, a scroll
// region): every registered restore runs, then the signal is raised again
// and the process stops as it would have. Returns the unregister function.
const restores = new Set();

function interrupted() {
  for (const restore of [...restores]) restore();
  restores.clear();
  process.off('SIGINT', interrupted);
  process.kill(process.pid, 'SIGINT');
}

export function onInterrupt(restore) {
  if (restores.size === 0) process.on('SIGINT', interrupted);
  restores.add(restore);
  return () => {
    restores.delete(restore);
    if (restores.size === 0) process.off('SIGINT', interrupted);
  };
}

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

// A spinner on one line while `text` is going on. Without a terminal it
// stays quiet and only the final line is written, through `commit` when
// given (the setup prompter keeps such lines to draw the screen again).
export function spinner(output, text, { env = process.env, indent = '', commit = (line) => output.write(line) } = {}) {
  const s = styler(output, env);
  const live = Boolean(output.isTTY) && s.enabled;
  let label = text;
  let frame = 0;
  let timer = null;
  const started = Date.now();
  const draw = () => {
    const glyph = s.paint(SPINNER_FRAMES[frame % SPINNER_FRAMES.length], mix(PALETTE.sail, frame / 24, { cycle: true }));
    const seconds = Math.floor((Date.now() - started) / 1000);
    const clock = seconds >= 3 ? s.gray(` ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`) : '';
    output.write(`\r${ESC}2K${indent}${glyph} ${label}${clock}`);
    frame += 1;
  };
  // Ctrl-C while it spins: give the cursor back, then stop as usual.
  let unguard = () => {};
  if (live) {
    output.write(HIDE_CURSOR);
    unguard = onInterrupt(() => output.write(`\r${ESC}2K${SHOW_CURSOR}`));
    draw();
    timer = setInterval(draw, 80);
  }
  const finish = (symbol, message) => {
    if (timer) clearInterval(timer);
    timer = null;
    unguard();
    if (live) output.write(`\r${ESC}2K${SHOW_CURSOR}`);
    if (message !== null) commit(`${indent}${symbol} ${message ?? label}\n`);
  };
  return {
    update(next) { label = next; if (live) draw(); },
    succeed(message) { finish(s.green(SYMBOLS.done), message); },
    fail(message) { finish(s.red(SYMBOLS.fail), message); },
    warn(message) { finish(s.yellow(SYMBOLS.warn), message); },
    stop() { finish('', null); },
  };
}

// Lines in a rounded box, padded by display width. The border takes the
// gradient when there is colour.
export function box(lines, { output = process.stdout, env = process.env, title = '', padding = 2 } = {}) {
  const s = styler(output, env);
  const inner = Math.max(width(title) + 2, ...lines.map((line) => width(line))) + padding * 2;
  const edge = (text) => s.gradient(text, PALETTE.sail);
  const heading = title ? ` ${title} ` : '';
  const top = `╭─${heading}${'─'.repeat(Math.max(0, inner - width(heading) - 1))}╮`;
  const rows = lines.map((line) => `${edge('│')}${' '.repeat(padding)}${padEnd(line, inner - padding * 2)}${' '.repeat(padding)}${edge('│')}`);
  return [edge(top), ...rows, edge(`╰${'─'.repeat(inner)}╯`)].join('\n');
}
