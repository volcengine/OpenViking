import { ArrowUpRightIcon, FileLinkIcon } from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { ExperienceCopyAction } from './experience-copy-action.js';
import type {
  ExperienceDashboardLabels,
  ExperienceOutcome,
  ExperienceTrajectory,
} from './types.js';

/** 只给有语义的三种结果上色，其余保持中性。 Only these three outcomes are coloured. */
const OUTCOME_CLASS: Partial<Record<ExperienceOutcome, string>> = {
  success: 'ov-experience-dashboard__outcome-tag--success',
  failure: 'ov-experience-dashboard__outcome-tag--failure',
  partial: 'ov-experience-dashboard__outcome-tag--partial',
};

/**
 * 轨迹标题：服务端有时返回空 `name`，此时回退到 URI 的文件名，否则整行只剩图标。
 *
 * The trajectory title: the server sometimes returns an empty `name`; fall back to
 * the file name in the URI, or the row would be left with only an icon.
 */
function titleOf(item: ExperienceTrajectory) {
  if (item.name) return item.name;
  const file = item.uri.split('/').filter(Boolean).at(-1);
  return file ? decodeURIComponent(file) : item.uri;
}

/**
 * 轨迹时间：按界面语言格式化，不直接把接口的时间串摆出来。
 *
 * The trajectory time, formatted for the interface language instead of printing the
 * endpoint's raw timestamp.
 */
function timeOf(value: string, locale: string) {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return value;
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(time);
}

/**
 * 轨迹时间线：左侧一条竖线加圆点，右侧依次是时间、标题与结果、URI 与两个操作。
 *
 * 结构与火山版一致——URI 前的文件图标、复制与「在上下文管理中打开」两个图标
 * 按钮，都是轨迹行固定的一部分。
 *
 * The trajectory timeline: a rail with a dot on the left, then the time, the title
 * with its outcome, and the URI with two actions on the right.
 *
 * The structure follows the console: the file icon before the URI and the copy and
 * "open in context management" icon buttons are part of every row.
 */
export function ExperienceTrajectories({
  items,
  labels: t,
  locale = 'en-US',
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  onOpenUri,
}: {
  items: readonly ExperienceTrajectory[];
  labels: ExperienceDashboardLabels;
  locale?: 'en-US' | 'zh-CN';
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      <ol className="ov-experience-dashboard__timeline">
        {items.map((item) => (
          <li key={item.uri} className="ov-experience-dashboard__timeline-item">
            <span
              className="ov-experience-dashboard__timeline-rail"
              aria-hidden="true"
            >
              <i />
            </span>
            <div className="ov-experience-dashboard__timeline-content">
              {item.created_at ? (
                <time dateTime={item.created_at}>
                  {timeOf(item.created_at, locale)}
                </time>
              ) : null}
              <div className="ov-experience-dashboard__trajectory-title">
                <button
                  type="button"
                  className="ov-experience-dashboard__trajectory-name"
                  disabled={!onOpenUri}
                  onClick={() => onOpenUri?.(item.uri)}
                >
                  {titleOf(item)}
                </button>
                {item.outcome ? (
                  <span
                    className={[
                      'ov-experience-dashboard__outcome-tag',
                      OUTCOME_CLASS[item.outcome] ?? '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    <i aria-hidden="true" />
                    {t[item.outcome]}
                  </span>
                ) : null}
              </div>
              <div className="ov-experience-dashboard__trajectory-path">
                <FileLinkIcon aria-hidden="true" width={12} height={12} />
                <span title={item.uri}>{item.uri}</span>
                <span className="ov-experience-dashboard__path-actions">
                  <ExperienceCopyAction value={item.uri} labels={t} />
                  <Tooltip content={t.open} placement="top">
                    <button
                      type="button"
                      className="ov-experience-dashboard__icon-button"
                      aria-label={t.open}
                      disabled={!onOpenUri}
                      onClick={() => onOpenUri?.(item.uri)}
                    >
                      <ArrowUpRightIcon width={12} height={12} />
                    </button>
                  </Tooltip>
                </span>
              </div>
              {item.description ? (
                <p className="ov-experience-dashboard__trajectory-description">
                  {item.description}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
      {hasMore || loadingMore ? (
        <div className="ov-experience-dashboard__load-more">
          {loadingMore ? (
            t.loadingMore
          ) : (
            <button
              type="button"
              className="ov-experience-dashboard__load-more-action"
              disabled={!onLoadMore}
              onClick={onLoadMore}
            >
              {t.loadMore}
            </button>
          )}
        </div>
      ) : null}
    </>
  );
}
