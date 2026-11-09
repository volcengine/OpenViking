/** 每一级缩进与祖先连线的宽度。Indent per level, and the guide through it. */
export const INDENT = 20;
export const CHEVRON_SIZE = 16;

/** 行按深度缩进。Indents a row by its depth. */
export function rowStyle(depth: number) {
  return { paddingLeft: 8 + depth * INDENT };
}

/** 每一级祖先画一条竖线，上下各溢出 1px 让相邻行接上。 */
export function Guides({ depth }: { depth: number }) {
  return (
    <>
      {Array.from({ length: depth }, (_, level) => (
        <span
          key={level}
          aria-hidden="true"
          className="ov-file-tree__guide"
          style={{ left: 8 + level * INDENT + CHEVRON_SIZE / 2 }}
        />
      ))}
    </>
  );
}
