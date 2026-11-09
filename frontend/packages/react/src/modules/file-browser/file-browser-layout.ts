/**
 * 文件浏览的栏宽约束。
 *
 * 树默认 352px（与设计稿一致）；窄于 200px 就只剩图标可辨。上限除了 520px 的
 * 绝对值，还要给右侧详情留出可读宽度——容器被宿主压窄时，树不能继续占着
 * 520px 把详情挤没。
 *
 * Column-width limits of the file browser.
 *
 * The tree starts at 352px, matching the design; below 200px only icons stay
 * legible. Above the 520px absolute cap the detail pane must still have room, so a
 * container the host squeezed narrows the tree rather than letting it keep 520px
 * and crush the detail.
 */
export const TREE_DEFAULT_WIDTH = 352;
export const TREE_MIN_WIDTH = 200;
/** 详情区至少保留的宽度。 Width the detail pane keeps at minimum. */
const DETAIL_MIN_WIDTH = 320;
/**
 * 分隔条实际占的横向空间：16px 命中区，加两侧各 4px 外边距。
 * The divider's real horizontal footprint: a 16px hit area plus 4px of margin on
 * each side.
 */
const RESIZE_FOOTPRINT = 24;
/** 树的绝对宽度上限。 The tree's absolute width cap. */
const TREE_MAX_WIDTH = 520;

/**
 * 按容器宽度算出树的最大宽度。
 *
 * 容器宽度还没测出来（首帧）时用绝对值上限，避免把树压到最小。
 *
 * Resolves the tree's maximum width from the container width.
 *
 * Before the container is measured (the first frame) the absolute cap applies, so
 * the tree is never squeezed to its minimum.
 */
export function resolveTreeMaxWidth(containerWidth: number) {
  if (!containerWidth) return TREE_MAX_WIDTH;

  return Math.max(
    TREE_MIN_WIDTH,
    Math.min(
      TREE_MAX_WIDTH,
      containerWidth - DETAIL_MIN_WIDTH - RESIZE_FOOTPRINT,
    ),
  );
}
