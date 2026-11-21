import type { CSSProperties } from 'react';

/** 每一级缩进与祖先连线的宽度。Indent per level, and the guide through it. */
const INDENT = 'var(--ov-ui-file-tree-indent, 20px)';

/** 行按深度缩进。Indents a row by its depth. */
export function rowStyle(depth: number, pinTrailing = false): CSSProperties {
  // Deep padding would constrain sticky content outside the viewport. Move that
  // indentation to the leading control's margin when the trailing slot is pinned.
  return {
    paddingLeft: pinTrailing ? '2px' : `calc(2px + ${depth} * ${INDENT})`,
    '--ov-ui-file-tree-row-indent': `calc(${depth} * ${INDENT})`,
  } as CSSProperties;
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
          style={{ left: `calc(10px + ${level} * ${INDENT})` }}
        />
      ))}
    </>
  );
}
