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
 * 生成块的内容。每个值原样写出，不尝试拆解 CSS 函数——`linear-gradient` 里可能
 * 嵌套 `rgb()` / `color-mix()` 等带逗号和括号的函数，手工按 `(` 或 `,` 切分会
 * 把内层参数截断。折行交给 Prettier：下面统一格式化整份文件，生成结果与
 * `prettier --check` 的期望一致。
 *
 * The generated block. Each value is written verbatim rather than parsed: a
 * `linear-gradient` may nest `rgb()`, `color-mix()` and other functions that
 * contain their own commas and parentheses, and splitting on `(` or `,` would
 * truncate those inner arguments. Line wrapping is left to Prettier — the whole
 * file is formatted below, so the output matches what `prettier --check` expects.
 */
/**
 * 把调色板渲染成自定义属性声明。导出为纯函数，测试可以直接断言嵌套函数的完整
 * 性，不必真的改写文件。
 *
 * Render the palette into custom-property declarations. Exported as a pure
 * function so a test can assert on nested functions without mutating files.
 */
export function renderDeclarations(values) {
  return values
    .map(
      (gradient, index) =>
        `  --ov-ui-library-swatch-${index + 1}-default: ${gradient};`,
    )
    .join('\n');
}

const declarations = renderDeclarations(swatches);

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}

async function main() {
  const theme = readFileSync(themePath, 'utf8');
  const beginAt = theme.indexOf(BEGIN);
  const endAt = theme.indexOf(END);

  if (beginAt === -1 || endAt === -1 || endAt < beginAt) {
    throw new Error(`${themePath} is missing the ${BEGIN} … ${END} markers`);
  }

  // Keep the END marker's own indentation: slice from the start of its line.
  const lineStart = theme.lastIndexOf('\n', endAt) + 1;
  const indent = theme.slice(lineStart, endAt);
  const spliced = `${theme.slice(0, beginAt + BEGIN.length)}\n${declarations}\n${indent}${theme.slice(endAt)}`;

  // Format through the workspace's own Prettier config, so the generated block is
  // byte-identical to what `prettier --check` expects.
  const prettier = await import('prettier');
  const config = (await prettier.resolveConfig(themePath)) ?? {};
  const next = await prettier.format(spliced, {
    ...config,
    filepath: themePath,
  });

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
}
