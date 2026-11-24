/**
 * 工具栏图标：搜索与刷新。
 *
 * 路径取自控制台所用的图标集（Arco `IconSearch` / `IconRefresh`），不是手绘近似。
 * 两者都是 48 网格、`stroke-width: 4`、`fill: none`、`stroke: currentColor`，按
 * 同一比例缩放到 16px，描边粗细因此与原图标一致。
 *
 * Toolbar icons: search and refresh.
 *
 * The paths come from the icon set the console uses (Arco `IconSearch` /
 * `IconRefresh`) rather than a hand-drawn approximation. Both are 48-grid,
 * `stroke-width: 4`, `fill: none`, `stroke: currentColor`, scaled to 16px at the
 * same ratio so the stroke weight matches the original.
 */

const SIZE = 16;

/** 两个图标共用的绘制属性。Drawing attributes shared by both icons. */
const strokeProps = {
  width: SIZE,
  height: SIZE,
  viewBox: '0 0 48 48',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 4,
  'aria-hidden': true,
  focusable: 'false',
} as const;

/** 搜索图标。Search icon. */
export function SearchIcon() {
  return (
    <svg {...strokeProps}>
      <path d="M33.072 33.071c6.248-6.248 6.248-16.379 0-22.627-6.249-6.249-16.38-6.249-22.628 0-6.248 6.248-6.248 16.379 0 22.627 6.248 6.248 16.38 6.248 22.628 0Zm0 0 8.485 8.485" />
    </svg>
  );
}

/** 刷新图标。Refresh icon. */
export function RefreshIcon() {
  return (
    <svg {...strokeProps}>
      <path d="M38.837 18C36.463 12.136 30.715 8 24 8 15.163 8 8 15.163 8 24s7.163 16 16 16c7.455 0 13.72-5.1 15.496-12M40 8v10H30" />
    </svg>
  );
}
