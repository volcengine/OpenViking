import type { CSSProperties } from 'react';

import {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import type { ExperienceDashboardLabels, ExperienceItem } from './types.js';

/**
 * 更新时间的展示：不足 3 天显示相对时间，更早显示日期与时刻，与火山版一致。
 *
 * How the update time reads: a relative phrase within three days, otherwise the date
 * and time, matching the console.
 */
function updatedAt(value: string, locale: 'en-US' | 'zh-CN') {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return value;
  const seconds = (time - Date.now()) / 1000;
  const absolute = Math.abs(seconds);
  if (absolute >= 3 * 86400)
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(time);
  const [unit, size] =
    absolute < 3600
      ? (['minute', 60] as const)
      : absolute < 86400
        ? (['hour', 3600] as const)
        : (['day', 86400] as const);
  return new Intl.RelativeTimeFormat(locale, { numeric: 'always' }).format(
    Math.round(seconds / size),
    unit,
  );
}

/**
 * 表头的说明触发器：虚线下划线加一个可聚焦的按钮，悬停或聚焦时弹出说明。
 *
 * 用浮层而不是 `title`：`title` 要等一秒才出现、键盘聚焦不展示，而且样式无法
 * 跟随主题。按钮本身是说明的触发元素，因此读屏器把它当作可操作控件朗读。
 *
 * The hint trigger of a column header: a dotted underline on a focusable button that
 * opens the explanation on hover or focus.
 *
 * A popover rather than `title`: a native tooltip waits a second, never appears on
 * keyboard focus, and cannot follow the theme. The button is the trigger, so assistive
 * technology announces it as an operable control.
 */
function MetricHint({ label, content }: { label: string; content: string }) {
  return (
    <Tooltip content={content} placement="top">
      <button type="button" className="ov-experience-dashboard__hint-label">
        {label}
      </button>
    </Tooltip>
  );
}

/**
 * 目录视图的经验列表：表格化的行，列依次是序号、经验文件、注入次数、召回次数。
 *
 * 表头右侧两列是说明触发器，说明文案来自文案包。名称右侧的「更新」标记由宿主在
 * `isUpdated` 里给出，组件不比较时间。
 *
 * The experience list of the directory view: table-like rows with the index, the
 * experience file and the two counters.
 *
 * The last two headers trigger an explanation from the label pack. The "Updated" tag
 * beside a name comes from the host through `isUpdated`; the component compares no
 * timestamps.
 */
export function ExperienceList({
  items,
  startIndex = 0,
  locale = 'en-US',
  labels: t,
  onOpenUri,
}: {
  items: readonly ExperienceItem[];
  startIndex?: number;
  locale?: 'en-US' | 'zh-CN';
  labels: ExperienceDashboardLabels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-experience-dashboard__table-scroll">
      <div className="ov-experience-dashboard__table" role="table">
        <div className="ov-experience-dashboard__table-head" role="row">
          <span role="columnheader" aria-label={t.index} />
          <span role="columnheader">{t.name}</span>
          <span role="columnheader">
            <MetricHint label={t.injection} content={t.injectionHint} />
          </span>
          <span role="columnheader">
            <MetricHint label={t.listRecall} content={t.recallHint} />
          </span>
        </div>
        {items.map((item, index) => {
          const Icon = getFileTypeIcon(item.name);
          const colorKey = resolveFileTypeIconKey(item.name);
          return (
            <div
              key={item.uri}
              className="ov-experience-dashboard__table-row"
              role="row"
            >
              <span
                className="ov-experience-dashboard__table-index"
                role="cell"
              >
                #{startIndex + index + 1}
              </span>
              <div className="ov-experience-dashboard__table-file" role="cell">
                <div className="ov-experience-dashboard__table-file-title">
                  <Icon
                    aria-hidden="true"
                    className="ov-experience-dashboard__file-icon"
                    style={
                      {
                        '--ov-ui-experience-icon-light': getFileTypeColor(
                          colorKey,
                          'light',
                        ),
                        '--ov-ui-experience-icon-dark': getFileTypeColor(
                          colorKey,
                          'dark',
                        ),
                      } as CSSProperties
                    }
                  />
                  <button
                    type="button"
                    className="ov-experience-dashboard__table-file-name"
                    disabled={!onOpenUri}
                    onClick={() => onOpenUri?.(item.uri)}
                  >
                    {item.name}
                  </button>
                  {item.isUpdated ? (
                    <span className="ov-experience-dashboard__updated-tag">
                      <span>{t.updatedTag}</span>
                    </span>
                  ) : null}
                </div>
                <div className="ov-experience-dashboard__table-file-meta">
                  <span title={item.uri}>{item.uri}</span>
                  {item.modTime ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <span>
                        {t.updated} {updatedAt(item.modTime, locale)}
                      </span>
                    </>
                  ) : null}
                </div>
              </div>
              <span
                className="ov-experience-dashboard__table-metric"
                role="cell"
              >
                {item.injectionCount ?? '—'}
              </span>
              <span
                className="ov-experience-dashboard__table-metric"
                role="cell"
              >
                {item.recallCount ?? '—'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
