import { useId } from 'react';
import type { SVGProps } from 'react';

/**
 * `nextjs` 的斜笔画靠渐变淡出，而渐变要用片段 ID 引用。SVGR 会把 `id` 原样
 * 编译进组件，同页多个实例共用同一个 ID，后面的实例会解析到先出现实例的渐变，
 * 取到别人的 `currentColor`。这里手写这个图标，用 `useId` 生成实例级唯一 ID。
 *
 * 图形数据与 `packages/icons/svg/file-type/nextjs.svg` 保持一致，那份源文件仍是
 * 该图标的 canonical 来源；差别只有 ID 由 `useId` 生成，以及属性写成 JSX。
 *
 * The `nextjs` slash fades out through a gradient referenced by a fragment ID.
 * SVGR compiles that `id` verbatim, so instances on one page share it and a later
 * instance resolves to an earlier one's gradient, picking up its `currentColor`.
 * This icon is written by hand so `useId` can give each instance its own ID.
 *
 * The artwork matches `packages/icons/svg/file-type/nextjs.svg`, which stays the
 * canonical source for this icon; the only differences are the ID coming from
 * `useId` and the attributes being written as JSX.
 */
export function NextjsIcon({
  'aria-label': ariaLabel,
  ...props
}: SVGProps<SVGSVGElement>) {
  const gradientId = `ov-ft-nextjs-${useId().replaceAll(':', '')}`;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={16}
      height={16}
      viewBox="0 0 16 16"
      fill="none"
      // A labelled icon must stay in the accessibility tree, so only hide it when
      // the caller supplies no name; an explicit `aria-hidden` still wins.
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      {...props}
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1={4.522}
          x2={14}
          y1={3.943}
          y2={16}
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="currentColor" />
          <stop offset={1} stopColor="currentColor" stopOpacity={0} />
        </linearGradient>
      </defs>
      <path fill="currentColor" d="M3 2h1.522v9.09H3z" />
      <path
        fill={`url(#${gradientId})`}
        d="M4.903 2 15 15.075q-.565.5-1.195.925L4.522 3.943z"
      />
      <path fill="currentColor" d="M12.172 2h-1.508v9.094h1.508z" />
    </svg>
  );
}
