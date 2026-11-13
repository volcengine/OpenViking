import {
  ActivityIcon,
  FileInputIcon,
  ScanSearchIcon,
} from '../../icons/index.js';
import type { DashboardStatus } from '../../shared/dashboard/dashboard-state.js';
import type { Locale } from '../../shared/locale.js';
import { ExperienceQueryState } from './experience-state.js';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
} from './types.js';

/**
 * 注入率：服务端给 0 到 1 的比例，固定显示一位小数的百分比。
 *
 * 位数固定而不是「最多一位」：控制台的三项指标同排一行，`0%` 与 `60.0%` 混排会
 * 让数字宽度来回跳；固定一位后 `0.0%` 与 `60.0%` 对齐。越界值夹到 0 到 1，
 * 比例不合法时也不会显示成三位数百分比。
 *
 * The injection rate: a 0–1 ratio from the server, shown as a percentage with exactly
 * one decimal.
 *
 * The digits are fixed rather than "at most one": the console puts the three metrics on
 * one row, and mixing `0%` with `60.0%` makes the numbers jump in width; a fixed digit
 * keeps `0.0%` and `60.0%` aligned. An out-of-range value is clamped to 0–1, so a bad
 * ratio never reads as a three-digit percentage.
 */
function formatRate(rate: number, locale: Locale) {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(Math.min(Math.max(rate, 0), 1));
}

/**
 * 三个指标：注入上下文次数、检索召回次数与注入率。
 *
 * 指标自己一块取数，失败时只把这一块换成错误与重试，结果分布和轨迹照常显示——
 * 它们各自取数，不应被同一区域的失败牵连。
 *
 * The three metrics: context injections, retrieval recalls and the injection rate.
 *
 * They fetch on their own, so a failure replaces only this block with an error and a
 * retry while the distribution and the timeline still render — those fetch separately
 * and must not be dragged down by it.
 */
export function ExperienceMetrics({
  data,
  labels: t,
  locale,
  state = {},
}: {
  data: ExperienceDashboardData;
  labels: ExperienceDashboardLabels;
  locale: Locale;
  state?: DashboardStatus;
}) {
  if (state.loading || state.error)
    return (
      <div className="ov-experience-dashboard__metrics-state">
        <ExperienceQueryState
          state={state}
          rows={2}
          empty={t.unsupportedMetric}
          loading={t.loading}
          failed={t.failed}
          retry={t.retry}
        />
      </div>
    );
  const metrics = [
    {
      key: 'injection',
      label: t.injection,
      hint: t.injectionHint,
      value: data.effect?.injectionCount,
      icon: <FileInputIcon width={14} height={14} />,
    },
    {
      key: 'recall',
      label: t.recall,
      hint: t.recallHint,
      value: data.effect?.recallCount,
      icon: <ScanSearchIcon width={14} height={14} />,
    },
    {
      key: 'rate',
      label: t.rate,
      hint: t.injectionHint,
      value: data.effect
        ? formatRate(data.effect.injectionRate, locale)
        : undefined,
      icon: <ActivityIcon width={14} height={14} />,
    },
  ];
  return (
    <dl className="ov-experience-dashboard__metrics">
      {metrics.map((metric) => (
        <div key={metric.key} title={metric.hint}>
          {metric.icon}
          <dd>{metric.value ?? '—'}</dd>
          <dt>{metric.label}</dt>
        </div>
      ))}
    </dl>
  );
}
