// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { t } from './i18n.mjs';
import { PALETTE, RESET, bgCode, colorDepth, fgCode, mix, scale, styler, width } from './ui.mjs';

// The OpenViking SuperAgent banner: a small spaced "O P E N V I K I N G" over
// a big 5x7 pixel "SUPERAGENT" in the sunset gradient, then the tagline and
// version. 69 columns wide; on anything narrower, without colour, or in CI it
// is a single title line instead.

const PRODUCT_NAME = 'OpenViking SuperAgent';
const LABEL = 'O P E N V I K I N G';
const tagline = () => t('把本机的 coding agent 带上船', 'Bring your coding agent aboard');

// 5 wide x 7 tall glyphs; '#' is on.
const FONT = {
  S: ['.####', '#....', '#....', '.###.', '....#', '....#', '####.'],
  U: ['#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  P: ['####.', '#...#', '#...#', '####.', '#....', '#....', '#....'],
  E: ['#####', '#....', '#....', '####.', '#....', '#....', '#####'],
  R: ['####.', '#...#', '#...#', '####.', '#.#..', '#..#.', '#...#'],
  A: ['.###.', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'],
  G: ['.####', '#....', '#....', '#.###', '#...#', '#...#', '.###.'],
  N: ['#...#', '##..#', '#.#.#', '#.#.#', '#..##', '#...#', '#...#'],
  T: ['#####', '..#..', '..#..', '..#..', '..#..', '..#..', '..#..'],
};
const GAP = 2;
const WORD = (() => {
  const rows = Array.from({ length: 7 }, () => '');
  [...'SUPERAGENT'].forEach((ch, i, all) => FONT[ch].forEach((row, y) => {
    rows[y] += row + (i < all.length - 1 ? '.'.repeat(GAP) : '');
  }));
  return rows;
})();
const WORD_WIDTH = WORD[0].length;
// One column of margin on the left.
export const BANNER_WIDTH = WORD_WIDTH + 1;

const sunset = (x, span) => mix(PALETTE.sunset, x / Math.max(1, span - 1));

// Two pixels per cell: '▀' / '▄' / '█', or '▀' over a background.
function wordCells() {
  const at = (x, y) => (WORD[y]?.[x] === '#' ? mix([sunset(x, WORD_WIDTH), [255, 255, 255]], y === 0 ? 0.18 : 0) : null);
  const out = [];
  for (let y = 0; y < WORD.length; y += 2) {
    out.push([...WORD[0]].map((_, x) => {
      const top = at(x, y);
      const bottom = at(x, y + 1);
      if (!top && !bottom) return { ch: ' ' };
      if (!top) return { ch: '▄', rgb: bottom };
      if (!bottom) return { ch: '▀', rgb: top };
      if (top.join() === bottom.join()) return { ch: '█', rgb: top };
      return { ch: '▀', rgb: top, bg: bottom };
    }));
  }
  return out;
}

const text = (value, rgbAt = () => null) => [...value].map((ch, x) => ({ ch, rgb: rgbAt(x) }));
const blank = (n) => Array.from({ length: Math.max(0, n) }, () => ({ ch: ' ' }));

// Cell rows as strings, a colour change only where the colour changes. A
// cell without a colour is the terminal's own foreground.
function render(rows, depth) {
  return rows.map((row) => {
    let line = '';
    let open = '';
    for (const cell of row) {
      const code = `${fgCode(cell.rgb, depth)}${bgCode(cell.bg, depth)}`;
      if (code !== open) {
        line += `${open ? RESET : ''}${code}`;
        open = code;
      }
      line += cell.ch;
    }
    return `${line}${open ? RESET : ''}`.replace(/\s+$/, '');
  });
}

// The full banner's rows, coloured for `depth`.
export function bannerLines({ depth = 24, version = '' } = {}) {
  const versionText = version ? `v${version}` : '';
  const footer = [...blank(1), ...text(tagline())];
  const used = width(tagline()) + versionText.length;
  footer.push(...blank(WORD_WIDTH - used), ...text(versionText, () => PALETTE.dim));
  return render([
    [...blank(1), ...text(LABEL, (x) => scale(sunset(x, LABEL.length), 0.95))],
    ...wordCells().map((row) => [...blank(1), ...row]),
    footer,
  ], depth);
}

export const BANNER_ROWS = 6;

// The one-line title: `▲ OpenViking SuperAgent  vX  tagline`, the tagline
// only when it fits.
export function titleLine({ output = process.stdout, env = process.env, version = '', columns = output.columns ?? 80 } = {}) {
  const s = styler(output, env);
  let line = `${s.paint('▲', PALETTE.sunset[2])} ${s.bold(s.gradient(PRODUCT_NAME, PALETTE.sunset))}`;
  let used = 2 + PRODUCT_NAME.length;
  if (version) {
    line += s.gray(`  v${version}`);
    used += 3 + version.length;
  }
  if (columns >= 65 && used + 2 + width(tagline()) < columns) line += `  ${tagline()}`;
  return line;
}

function fullBanner(output, env) {
  const depth = colorDepth(output, env);
  return depth >= 8 && !env.CI && (output.columns ?? 0) > BANNER_WIDTH && (output.rows ?? 24) > BANNER_ROWS + 2;
}

// Prints the banner (or the title line) once.
export async function playBanner({ output = process.stdout, env = process.env, version = '' } = {}) {
  if (!fullBanner(output, env)) {
    output.write(`\n${titleLine({ output, env, version })}\n\n`);
    return;
  }
  output.write(`\n${bannerLines({ depth: colorDepth(output, env), version }).join('\n')}\n\n`);
}

// The banner for setup: printed once at the top; the questions scroll below.
export async function openingBanner(options = {}) {
  await playBanner(options);
  return { release() {}, resume() {}, top: () => null };
}
