/**
 * 从单一数据源同步库图标的渐变默认值。
 *
 * 调色板只维护在 `packages/icons/library-swatches.json`，React 侧直接导入这份
 * JSON 当兜底值，CSS 侧的 `--ov-ui-library-swatch-N-default` 由本脚本写入
 * `packages/react/src/theme.css` 的标记块之间。这样只改一处颜色就能同时更新
 * 「加载样式表」与「不加载样式表」两条路径，不会出现两份调色板各自漂移。
 *
 * 用法：`node scripts/sync-library-swatches.mjs`（`--check` 只校验不写入，供 CI
 * 或本地预检使用）。
 *
 * Sync the library icon's gradient defaults from a single source.
 *
 * The palette lives only in `packages/icons/library-swatches.json`. React imports
 * that JSON directly for its fallback values, and this script writes the CSS side
 * (`--ov-ui-library-swatch-N-default`) into the marked block in
 * `packages/react/src/theme.css`. Editing one colour therefore updates both the
 * "stylesheet loaded" and "no stylesheet" paths instead of letting two palettes
 * drift apart.
 *
 * Usage: `node scripts/sync-library-swatches.mjs` (`--check` verifies without
 * writing, for CI or a local pre-flight).
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const palettePath = `${root}packages/icons/library-swatches.json`;
const themePath = `${root}packages/react/src/theme.css`;
const BEGIN = '/* @generated library-swatches:begin */';
const END = '/* @generated library-swatches:end */';

const { swatches } = JSON.parse(readFileSync(palettePath, 'utf8'));

if (!Array.isArray(swatches) || swatches.length === 0) {
  throw new Error(`${palettePath} must declare a non-empty "swatches" array`);
}

/**
 * 一条自定义属性的多行写法。渐变串都超过 Prettier 的折行宽度，它会把声明拆成
 * 多行；这里直接输出同样的形状，`prettier --check` 才不会被这段生成内容判为
 * 未格式化。
 *
 * One custom-property declaration in the wrapped shape Prettier produces: every
 * gradient is past its print width, so it splits the declaration across lines.
 * Emitting the same shape keeps `prettier --check` from flagging generated output.
 */
const declaration = (gradient, index) => {
  const [head, rest] = gradient.split('(');
  const parts = rest.replace(/\)$/, '').split(',');
  return [
    `  --ov-ui-library-swatch-${index + 1}-default: ${head}(`,
    ...parts.map((part) => `    ${part.trim()},`),
    '  );',
  ]
    .join('\n')
    .replace(/,\n {2}\);$/, '\n  );');
};

const block = swatches
  .map((gradient, index) => declaration(gradient, index))
  .join('\n');

const theme = readFileSync(themePath, 'utf8');
const beginAt = theme.indexOf(BEGIN);
const endAt = theme.indexOf(END);

if (beginAt === -1 || endAt === -1 || endAt < beginAt) {
  throw new Error(`${themePath} is missing the ${BEGIN} … ${END} markers`);
}

// Keep the END marker's own indentation: slice from the start of its line.
const lineStart = theme.lastIndexOf('\n', endAt) + 1;
const indent = theme.slice(lineStart, endAt);
const next = `${theme.slice(0, beginAt + BEGIN.length)}\n${block}\n${indent}${theme.slice(endAt)}`;

if (process.argv.includes('--check')) {
  if (next !== theme) {
    console.error(
      'library swatches are out of date: run `node scripts/sync-library-swatches.mjs`',
    );
    process.exitCode = 1;
  } else {
    console.log('library swatches are in sync');
  }
} else if (next === theme) {
  console.log('library swatches already in sync');
} else {
  writeFileSync(themePath, next);
  console.log(`wrote ${swatches.length} swatches to ${themePath}`);
}
