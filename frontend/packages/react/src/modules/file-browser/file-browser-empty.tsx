/**
 * 未选中资源时的空状态：插画在上、提示文案在下，整体居中。
 *
 * 插画直接用设计提供的位图，不自行重绘——手绘 SVG 与原图在折角、网格密度上
 * 很难完全一致，反而更难对齐。
 *
 * The empty state shown before anything is selected: artwork above, hint below,
 * centred as a whole.
 *
 * The artwork is the bitmap supplied by the design rather than a redrawn SVG: a
 * hand-drawn version tends to differ in the folded corner and grid density, which
 * makes alignment harder rather than easier.
 */
import emptyStateImage from '../../../../icons/empty-state.png';

export function FileBrowserEmpty({ label }: { label: string }) {
  return (
    <div className="ov-file-browser__empty">
      <img
        className="ov-file-browser__empty-art"
        src={emptyStateImage}
        alt=""
        aria-hidden="true"
      />
      <p className="ov-file-browser__empty-label">{label}</p>
    </div>
  );
}
