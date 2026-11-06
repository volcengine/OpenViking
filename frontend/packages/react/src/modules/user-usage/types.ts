import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

/** 用量统计的指标。Usage metric. */
export type UserUsageMetric =
  'contexts' | 'tokens' | 'retrievals' | 'submissions';

/** 用量统计的时间粒度。Time bucket of the usage statistics. */
export type UserUsageBucket = 'hour' | 'day' | 'week';

/** 用量模块的插槽。Slots of the user usage module. */
export interface UserUsageSlots {
  /**
   * 时间范围选择器等附加控件。省略时模块不渲染时间范围控件。
   * Extra controls such as a date-range picker. No range control renders when
   * omitted.
   */
  controls?: ReactNode;
  /**
   * 指标卡下方的附加内容，例如趋势说明。
   * Extra content below the metric cards, for example a note on the trend.
   */
  extra?: ReactNode;
  /**
   * 空态内容。省略时展示 `labels.empty`。
   * Empty state. Omission shows `labels.empty`.
   */
  emptyState?: ReactNode;
}

/** 用量模块的可替换文案。Replaceable copy of the user usage module. */
export interface UserUsageLabels {
  /** 区域标题。Area title. */
  title?: string;
  /** 上下文数量指标名称。Context-count metric label. */
  contexts?: string;
  /** Token 用量指标名称。Token metric label. */
  tokens?: string;
  /** 检索次数指标名称。Retrieval metric label. */
  retrievals?: string;
  /** 提交次数指标名称。Submission metric label. */
  submissions?: string;
  /** 没有数据时的提示。Message shown when there is no data. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
}

/**
 * 用量模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有统计结果：宿主给配置、时间范围、要展示
 * 的指标、插槽与文案。统计只覆盖当前 User，不含库 / 空间统计、成员对比与账单
 * 费用。
 *
 * Props of the user usage module.
 *
 * The module loads its own data through a Hook, so no prop carries statistics:
 * the host supplies configuration, the time range, which metrics to show, slots
 * and copy. Statistics cover the current user only, excluding library / space
 * statistics, member comparison and billing costs.
 */
export interface UserUsageProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /**
   * 统计的起始日期，格式为 `YYYY-MM-DD`（宿主本地时区）。省略时由模块取最近
   * 七天。
   * Start date of the statistics as `YYYY-MM-DD` in the host's local time zone.
   * The module takes the last seven days when omitted.
   */
  startDate?: string;
  /**
   * 统计的结束日期，格式为 `YYYY-MM-DD`（宿主本地时区）。省略时由模块取今天。
   * End date of the statistics as `YYYY-MM-DD` in the host's local time zone.
   * The module takes today when omitted.
   */
  endDate?: string;
  /**
   * 统计的时间粒度，缺省为 `day`。
   * Time bucket of the statistics; defaults to `day`.
   */
  bucket?: UserUsageBucket;
  /**
   * 要展示的指标。省略时展示全部四项。
   * Metrics to show. All four show when omitted.
   */
  metrics?: readonly UserUsageMetric[];
  /**
   * 插槽，见 {@link UserUsageSlots}。
   * Slots, see {@link UserUsageSlots}.
   */
  slots?: UserUsageSlots;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: UserUsageLabels;
  /**
   * 请求配置覆盖，逐字段覆盖最近的 Provider。
   * Request configuration override, applied field by field over the nearest
   * Provider.
   */
  requestOptions?: OvRequestOptions;
}
