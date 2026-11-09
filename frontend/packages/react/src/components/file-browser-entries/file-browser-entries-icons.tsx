/**
 * 入口条的两个图标。
 *
 * 路径取自控制台 `assets/context-console/` 下的 `connect-agent.svg` 与
 * `upload.svg`，只把 `vector-effect="non-scaling-stroke"` 去掉——本包的图标按
 * viewBox 原尺寸渲染，不需要它。描边宽度 1.4 与圆头圆角保持原值。
 *
 * The two icons of the entry row.
 *
 * The paths come from `connect-agent.svg` and `upload.svg` under the console's
 * `assets/context-console/`; only `vector-effect="non-scaling-stroke"` is dropped,
 * because this package renders icons at their viewBox size and does not need it.
 * The 1.4 stroke width and the round caps and joins are kept as-is.
 */

/** 连接 Agent 图标，14 × 14 的插头。Connect-agent icon: a 14 × 14 plug. */
export function ConnectAgentIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M5.187 1.75V3.736M8.876 1.75V3.736M7.031 10.547V12.25M4.052 3.736H10.011A1.135 1.135 0 0 1 11.147 4.872V8.277A2.27 2.27 0 0 1 8.876 10.547H5.187A2.27 2.27 0 0 1 2.917 8.277V4.872A1.135 1.135 0 0 1 4.052 3.736Z" />
    </svg>
  );
}

/** 导入资源图标，12 × 12 的方框向上箭头。Import-resources icon: a 12 × 12 box with an up arrow. */
export function ImportResourcesIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M10.5 7.5V8.1C10.5 8.94008 10.5 9.36012 10.3365 9.68099C10.1927 9.96323 9.96323 10.1927 9.68099 10.3365C9.36012 10.5 8.94008 10.5 8.1 10.5H3.9C3.05992 10.5 2.63988 10.5 2.31901 10.3365C2.03677 10.1927 1.8073 9.96323 1.66349 9.68099C1.5 9.36012 1.5 8.94008 1.5 8.1V7.5M3.5 4L6 1.5L8.5 4M6 1.5V7.5" />
    </svg>
  );
}
