/**
 * 把宽度夹到 `[min, max]` 之间，并取整。
 *
 * 上限可能小于下限——容器被压得很窄时就会这样。此时以下限为准：面板宁可把
 * 邻居挤小，也不该缩到看不见内容。
 *
 * Clamps a width into `[min, max]` and rounds it.
 *
 * The maximum can fall below the minimum when the container is squeezed hard. The
 * minimum wins then: a panel may crowd its neighbour rather than shrink until
 * nothing is readable.
 */
export function clampResizableWidth(width: number, min: number, max: number) {
  const rounded = Math.round(width);
  const lower = Math.max(min, rounded);

  return Math.min(Math.max(min, max), lower);
}
