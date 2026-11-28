// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { RESET, bgCode, colorDepth, fgCode, mix, styler } from './ui.mjs';

// The OpenViking SuperAgent banner (Halftone Dither Fade):
// an "OPENVIKING ━━━ DAEMON SETUP" header above a 7-row "SUPERAGENT" display
// wordmark carved in vertical █ -> ▓ -> ▒ -> ░ risograph shade bands with a
// slate cast shadow and dual-theme calibrated colors (for both dark and light
// terminals). Uses 88 columns when wide enough, a 78-column compact layout on
// standard 80-column terminals, and a single title line on narrow/plain/CI runs.

const ESC = '\x1b[';
const PRODUCT_NAME = 'OpenViking SuperAgent';
const HEADER_LEFT = 'OPENVIKING';
const HEADER_RIGHT = 'DAEMON SETUP';
const CHANNELS_FULL = 'Lark · WeChat · Slack · Telegram · QQ · Email';
const CHANNELS_SHORT = 'Lark · WeChat · Slack · TG · QQ · Mail';

// Dual-theme calibrated stops (strong contrast on both #0d1117 and #ffffff):
// crimson (#D42A56) -> coral (#E04E30) -> burnt amber (#CC6C0A).
const HALFTONE_STOPS = Object.freeze([[212, 42, 86], [224, 78, 48], [204, 108, 10]]);
const SHADOW_RGB = Object.freeze([107, 118, 136]);
const RULE_RGB = Object.freeze([122, 133, 150]);

const SHADE_BY_ROW = ['█', '█', '█', '▓', '▓', '▒', '░'];
const GAP = 2;

// 7 wide x 7 tall display glyphs (full 88-col wordmark).
const FONT_WIDE = Object.freeze({
  S: ['.#####.', '##...##', '##.....', '.#####.', '.....##', '##...##', '.#####.'],
  U: ['##...##', '##...##', '##...##', '##...##', '##...##', '##...##', '.#####.'],
  P: ['######.', '##...##', '##...##', '######.', '##.....', '##.....', '##.....'],
  E: ['#######', '##.....', '##.....', '#####..', '##.....', '##.....', '#######'],
  R: ['######.', '##...##', '##...##', '######.', '##.##..', '##..##.', '##...##'],
  A: ['..###..', '.##.##.', '##...##', '#######', '##...##', '##...##', '##...##'],
  G: ['.#####.', '##...##', '##.....', '##.###.', '##...##', '##...##', '.#####.'],
  N: ['##...##', '###..##', '####.##', '##.####', '##..###', '##...##', '##...##'],
  T: ['#######', '..###..', '..###..', '..###..', '..###..', '..###..', '..###..'],
});

// 6 wide x 7 tall display glyphs (compact 78-col wordmark for 80-col terminals).
const FONT_COMPACT = Object.freeze({
  S: ['.####.', '##..##', '##....', '.####.', '....##', '##..##', '.####.'],
  U: ['##..##', '##..##', '##..##', '##..##', '##..##', '##..##', '.####.'],
  P: ['#####.', '##..##', '##..##', '#####.', '##....', '##....', '##....'],
  E: ['######', '##....', '##....', '#####.', '##....', '##....', '######'],
  R: ['#####.', '##..##', '##..##', '#####.', '##.##.', '##..##', '##..##'],
  A: ['..##..', '.####.', '##..##', '######', '##..##', '##..##', '##..##'],
  G: ['.####.', '##..##', '##....', '##.###', '##..##', '##..##', '.####.'],
  N: ['##..##', '###.##', '######', '##.###', '##..##', '##..##', '##..##'],
  T: ['######', '..##..', '..##..', '..##..', '..##..', '..##..', '..##..'],
});

function buildMask(font) {
  const rows = Array.from({ length: 7 }, () => '');
  [...'SUPERAGENT'].forEach((ch, index, all) => {
    font[ch].forEach((row, y) => {
      rows[y] += row + (index < all.length - 1 ? '.'.repeat(GAP) : '');
    });
  });
  return rows;
}

const MASK_WIDE = buildMask(FONT_WIDE);
const MASK_COMPACT = buildMask(FONT_COMPACT);

export const BANNER_WIDTH = MASK_WIDE[0].length;
export const COMPACT_BANNER_WIDTH = MASK_COMPACT[0].length;
export const BANNER_ROWS = 11;

const text = (value, { rgb = null, bold = false } = {}) => [...value].map((ch) => ({ ch, rgb, bold }));
const blank = (n) => Array.from({ length: Math.max(0, n) }, () => ({ ch: ' ' }));

function halftoneCells(mask) {
  const span = Math.max(1, mask[0].length - 1);
  return mask.map((line, y) => {
    const shade = SHADE_BY_ROW[y];
    return [...line].map((ch, x) => {
      if (ch === '#') {
        const t = (x / span) * 0.75 + (y / 6) * 0.25;
        return { ch: shade, rgb: mix(HALFTONE_STOPS, t) };
      }
      if (y > 0 && x > 0 && mask[y - 1][x - 1] === '#') {
        return { ch: '░', rgb: SHADOW_RGB };
      }
      return { ch: ' ' };
    });
  });
}

// Cell rows as strings, emitting ANSI codes only where style changes.
function render(rows, depth) {
  return rows.map((row) => {
    let line = '';
    let open = '';
    for (const cell of row) {
      const code = depth >= 4
        ? `${cell.bold ? `${ESC}1m` : ''}${fgCode(cell.rgb, depth)}${bgCode(cell.bg, depth)}`
        : '';
      if (code !== open) {
        line += `${open ? RESET : ''}${code}`;
        open = code;
      }
      line += cell.ch;
    }
    return `${line}${open ? RESET : ''}`.replace(/\s+$/, '');
  });
}

// The full banner's rows, coloured for `depth` and sized for `columns`.
export function bannerLines({ depth = 24, version = '', columns = BANNER_WIDTH } = {}) {
  const mask = columns >= BANNER_WIDTH ? MASK_WIDE : MASK_COMPACT;
  const widthCols = mask[0].length;
  const ruleWidth = Math.max(1, widthCols - HEADER_LEFT.length - HEADER_RIGHT.length - 2);
  const header = [
    ...text(`${HEADER_LEFT} `, { bold: true }),
    ...text('━'.repeat(ruleWidth), { rgb: RULE_RGB }),
    ...text(` ${HEADER_RIGHT}`, { rgb: HALFTONE_STOPS[1], bold: true }),
  ];

  const versionText = version ? `v${version}` : '';
  const channelLabel = versionText && CHANNELS_FULL.length + 2 + versionText.length > widthCols
    ? CHANNELS_SHORT
    : CHANNELS_FULL;
  const footerPad = versionText ? Math.max(1, widthCols - channelLabel.length - versionText.length) : 0;
  const footer = versionText
    ? [...text(channelLabel), ...blank(footerPad), ...text(versionText, { rgb: SHADOW_RGB })]
    : text(channelLabel);

  return render([
    header,
    [],
    ...halftoneCells(mask),
    [],
    footer,
  ], depth);
}

// The one-line title: `▲ OpenViking SuperAgent  vX`.
export function titleLine({ output = process.stdout, env = process.env, version = '' } = {}) {
  const s = styler(output, env);
  let line = `${s.paint('▲', HALFTONE_STOPS[1])} ${s.bold(s.gradient(PRODUCT_NAME, HALFTONE_STOPS))}`;
  if (version) line += s.paint(`  v${version}`, SHADOW_RGB);
  return line;
}

function fullBanner(output, env) {
  const depth = colorDepth(output, env);
  return depth >= 8 && !env.CI && (output.columns ?? 0) >= COMPACT_BANNER_WIDTH && (output.rows ?? 24) > BANNER_ROWS + 2;
}

// Prints the banner (or the title line) once.
export async function playBanner({ output = process.stdout, env = process.env, version = '' } = {}) {
  if (!fullBanner(output, env)) {
    output.write(`\n${titleLine({ output, env, version })}\n\n`);
    return;
  }
  const lines = bannerLines({ depth: colorDepth(output, env), version, columns: output.columns });
  output.write(`\n${lines.join('\n')}\n\n`);
}

// The banner for setup: printed once at the top; the questions scroll below.
export async function openingBanner(options = {}) {
  await playBanner(options);
  return { release() {}, resume() {}, top: () => null };
}
