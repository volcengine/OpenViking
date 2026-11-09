/** 每一级缩进与祖先连线的宽度。Indent per level, and the guide through it. */
export const INDENT = 16;
export const CHEVRON_SIZE = 16;

/** 行按深度缩进。Indents a row by its depth. */
export function rowStyle(depth: number) {
  return { paddingLeft: depth * INDENT };
}

/**
 * 每一级祖先画一条竖线。线的位置固定在缩进列的中点，与设计稿一致——上一级的
 * 竖线穿过本行时正好落在自己缩进列的中心。
 *
 * One vertical line per ancestor level. The line sits at the centre of its indent
 * column, matching the design: an ancestor's guide crossing this row lands exactly
 * in the middle of that column.
 */
export function Guides({ depth }: { depth: number }) {
  return (
    <>
      {Array.from({ length: depth }, (_, level) => (
        <span
          key={level}
          aria-hidden="true"
          className="ov-file-tree__guide"
          style={{ left: level * INDENT + CHEVRON_SIZE / 2 }}
        />
      ))}
    </>
  );
}
