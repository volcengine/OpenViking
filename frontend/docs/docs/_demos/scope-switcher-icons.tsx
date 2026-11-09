/**
 * 演示用的三个层级图标。图标由宿主传入，因此这里是示例实现而不是组件的一部分：
 * 全部渲染为内联 SVG 或纯 CSS，不会出现可被点开放大的 `<img>`。
 *
 * The three level icons used by the examples. Icons come from the host, so these
 * are example implementations rather than part of the component: everything here
 * renders as inline SVG or pure CSS, so nothing can be opened and scaled as an
 * `<img>`.
 */

/** Gradients picked by name; a library has no fixed glyph, only a colour. */
const LIBRARY_GRADIENTS = [
  'linear-gradient(145deg, #ffd8ec 8%, #ffabc8 46%, #d8cbff 100%)',
  'linear-gradient(145deg, #d9e8ff 8%, #b8a6ff 52%, #f3d6ff 100%)',
  'linear-gradient(145deg, #ffe6bc 8%, #ffaf96 48%, #ff8bc5 100%)',
  'linear-gradient(145deg, #eaf7ba 8%, #b5efb8 52%, #73d9c6 100%)',
  'linear-gradient(145deg, #fff1ba 8%, #ffc776 52%, #bd9cff 100%)',
];

function libraryGradient(name: string) {
  const hash = Array.from(name).reduce(
    (total, character) => total + character.charCodeAt(0),
    0,
  );
  return LIBRARY_GRADIENTS[hash % LIBRARY_GRADIENTS.length];
}

/** Circular gradient swatch; decorative, so it carries no accessible name. */
export function LibraryIcon({ name = '' }: { name?: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'block',
        width: 14,
        height: 14,
        borderRadius: '50%',
        background: libraryGradient(name),
      }}
    />
  );
}

/** Data space glyph, 14 × 14, drawn with `currentColor`. */
export function SpaceIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 14 14" width="14" height="14">
      <path
        d="M1.925 4.083 7 7l5.075-2.917M7 7v5.833M12.25 4.667c0-.205-.054-.406-.157-.583a1.167 1.167 0 0 0-.426-.427L7.583 1.324a1.167 1.167 0 0 0-1.167 0L2.333 3.657a1.167 1.167 0 0 0-.583 1.01v4.666c0 .205.054.406.157.583.102.177.249.324.426.426l4.084 2.334a1.167 1.167 0 0 0 1.167 0l4.083-2.334a1.167 1.167 0 0 0 .583-1.009V4.667Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
    </svg>
  );
}

/** User glyph, 14 × 14, drawn with `currentColor`. */
export function UserIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 14 14" width="14" height="14">
      <path
        d="M1.75 11.667A7.583 7.583 0 0 1 7 9.333c2.038 0 3.888.888 5.25 2.334M9.625 4.375a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
    </svg>
  );
}
